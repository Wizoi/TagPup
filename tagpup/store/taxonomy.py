"""The tag tree's table, tag_taxonomy: what it says about people, its nodes, and
TagTaxonomy, the tree held in memory by the indexer and the suggester.

TagTaxonomy was scripts/taxonomy.py.
"""
import json
import logging
import os
from typing import Dict, List, Optional, Set

from tagpup.core import paths, vocabulary
from tagpup.core.vocabulary import PeopleVocabulary
from tagpup.store import db, generations, people, schema

logger = logging.getLogger(__name__)


def generation(conn):
    """The tag tree's generation (tagpup.store.generations), or 0 on a library that
    does not count it yet."""
    return generations.value(conn, "taxonomy")


def sql_branch(path, column="tag"):
    """(SQL, parameters) matching the tag `path` and every tag under it, in `column`.

    Not LIKE '<path>/%': LIKE ignores case and reads `_` and `%` as wildcards, so it
    matched ClubXA/... and club_a/... under Club_A (docs/findings.md, #36).
    """
    prefix = path + vocabulary.SEPARATOR
    return "(%s = ? OR substr(%s, 1, ?) = ?)" % (column, column), (path, len(prefix), prefix)


def add_path(conn, path, root_has_face=0):
    """Put a tag in the tree on `conn`, with each of its levels that is missing, and
    return the id of its node: None for a tag with no levels. The caller commits.

    A new root holds faces if `root_has_face` says so, whatever its name: the tree is
    the only thing that says which roots do (docs/findings.md, #66). A node made below
    another takes its parent's flag.
    A level already in the tree is left as it is. This is the one writer of new nodes:
    there were five, each with its own copy of the rule (docs/findings.md, #39).
    """
    parent_id, parent_has_face = None, 0
    for part, level in zip(vocabulary.segments(path), vocabulary.lineage(path)):
        row = conn.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (level,)).fetchone()
        if row:
            parent_id, parent_has_face = row[0], row[1] or 0
            continue
        if parent_id is None:
            has_face = 1 if root_has_face else 0
        else:
            has_face = 1 if parent_has_face else 0
        parent_id = conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
            (level, parent_id, part, has_face)).lastrowid
        parent_has_face = has_face
    return parent_id


def add_node(conn, path, root_has_face=0):
    """add_path, for one node added on its own: the photos whose people it changes are
    rebuilt (people.tree_edit). A loop adding many wraps the loop instead. Returns the
    node's id. The caller commits."""
    with people.tree_edit(conn):
        return add_path(conn, path, root_has_face=root_has_face)


def people_roots(conn):
    """Lowercased roots the library open on `conn` files people under: the roots its tree
    flags as holding faces. A library whose tree is empty, or missing, has a new
    library's: the indexer resolves people before it first saves a tree."""
    if not tree_has_nodes(conn):
        return {vocabulary.NEW_LIBRARY_FACE_ROOT.lower()}
    return {name.strip().lower() for (name,) in conn.execute(
        "SELECT name FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%'") if name}


def _read_people_paths(conn):
    """{lowercased leaf name: tag path} of everyone the tree on `conn` files under a
    people root, leaving out anyone filed in two places."""
    roots = people_roots(conn)
    mapping = {}
    for tag_path in tags(conn):
        if "/" not in tag_path or vocabulary.key(vocabulary.root_of(tag_path)) not in roots:
            continue
        leaf = vocabulary.key(vocabulary.leaf_of(tag_path))
        mapping[leaf] = None if leaf in mapping and mapping[leaf] != tag_path else tag_path
    return {k: v for k, v in mapping.items() if v}


#: One reading of each library's people, so resolving on every write costs nothing
#: after the first. Kept by the tree's generation, which catches the edits this process
#: cannot see -- TagTuner's, in another process; this process also forgets it when it
#: writes the tree (forget_people_paths).
_people_paths = generations.Cache(["taxonomy"], _read_people_paths)


def people_paths(db_path):
    """Every person the library's tag tree names, keyed by their lowercased leaf name.

    Someone filed in two places is left out: which one a bare name means cannot be
    told without guessing. A library that cannot be read just now keeps what was last
    read of it.
    """
    if not db_path:
        return {}
    key = paths.key(str(db_path))
    if not os.path.exists(db_path):
        return _people_paths.last(key, {})
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            return _people_paths.get(conn, key)
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Could not load people paths from %s: %s", db_path, e)
        return _people_paths.last(key, {})


def forget_people_paths(db_path=None):
    """Forget what was read of a library's people -- every library's, without one --
    after something changed its tree."""
    _people_paths.forget(None if db_path is None else paths.key(str(db_path)))


def people_vocabulary(db_path=None, conn=None):
    """What the library's tag tree says about people: from `conn`, else `db_path`.

    With neither, or a library that cannot be read, only the usual face roots. There
    is no fallback to a default library: that would apply one library's people to
    another's photos.
    """
    own = None
    try:
        if conn is None and db_path and os.path.exists(db_path):
            own = conn = db.connect(db_path, timeout=5.0)
        if conn is None:
            return PeopleVocabulary.defaults()
        return read_people_vocabulary(conn)
    except Exception as e:
        logger.warning("Error resolving people from database taxonomy: %s", e)
        return PeopleVocabulary.defaults()
    finally:
        if own is not None:
            own.close()


def read_people_vocabulary(conn):
    """The PeopleVocabulary of the library open on `conn`: a new library's while its tree
    is empty (people_roots)."""
    if not tree_has_nodes(conn):
        return PeopleVocabulary.defaults()
    roots = [name for (name,) in conn.execute(
        "SELECT name FROM tag_taxonomy WHERE (parent_id IS NULL OR tag NOT LIKE '%/%') AND has_face = 1")]
    faces = conn.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1").fetchall()
    return PeopleVocabulary.from_rows(roots, faces)


# ---- The tree's nodes ------------------------------------------------------------------

#: A node as the tree view and the services see it.
NODE_COLUMNS = ("id", "tag", "parent_id", "name", "has_face", "hidden_from_autocomplete")


def has_tree(db_path):
    """Does the library have its tag tree's table yet?"""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                 " AND name='tag_taxonomy'").fetchone())
    finally:
        conn.close()


def _nodes(db_path, where="", params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        rows = conn.execute("SELECT %s FROM tag_taxonomy %s" % (", ".join(NODE_COLUMNS), where),
                            params).fetchall()
    finally:
        conn.close()
    return [dict(zip(NODE_COLUMNS, row)) for row in rows]


def node(db_path, node_id):
    """The node `node_id`, as a dict of NODE_COLUMNS, or None."""
    found = _nodes(db_path, "WHERE id = ?", (node_id,))
    return found[0] if found else None


def find(db_path, tag):
    """The node whose path is `tag`, or None."""
    found = _nodes(db_path, "WHERE tag = ?", (tag,))
    return found[0] if found else None


def nodes(db_path):
    """Every node, ordered by path."""
    return _nodes(db_path, "ORDER BY tag")


def repair(db_path):
    """Put right what older writers left in the tree -- a node whose parent is missing,
    a name holding "/" -- and return how many nodes that was.

    Each read of the tree asks, so it writes only when there is something to put right,
    and then through the write lock: it wrote on a connection of its own
    (docs/findings.md, #37).
    """
    if not os.path.exists(db_path) or not has_tree(db_path):
        return 0
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        orphans = conn.execute("SELECT id, tag, has_face FROM tag_taxonomy"
                               " WHERE instr(tag, '/') > 0 AND parent_id IS NULL").fetchall()
        misnamed = conn.execute("SELECT id, tag FROM tag_taxonomy WHERE instr(name, '/') > 0").fetchall()
    finally:
        conn.close()
    if not orphans and not misnamed:
        return 0

    def put_right(conn):
        with people.tree_edit(conn):
            for node_id, tag, has_face in orphans:
                parent_id = add_path(conn, vocabulary.parent_of(tag), root_has_face=has_face)
                conn.execute("UPDATE tag_taxonomy SET parent_id = ? WHERE id = ?", (parent_id, node_id))
            for node_id, tag in misnamed:
                conn.execute("UPDATE tag_taxonomy SET name = ? WHERE id = ?", (vocabulary.leaf_of(tag), node_id))
        return len(orphans) + len(misnamed)

    count = db.write_with_connection(db_path, put_right, label="tag tree repair")
    logger.info("Put right %d node(s) of the tag tree in %s", count, os.path.basename(db_path))
    return count


def branch(db_path, path):
    """The node `path` and every node under it, parents first."""
    where, params = sql_branch(path)
    return _nodes(db_path, "WHERE %s ORDER BY length(tag)" % where, params)


def move_branch(conn, old, new):
    """Move the node `old`, and every node under it, to its place under `new`. A node
    whose place is free moves there, keeping its id and flags; one whose place is taken
    joins the node already there, and goes. The caller commits. Returns the nodes moved
    or joined.

    Renaming a node moves its branch to a free place. Merging one tag into another joins
    the branches.
    """
    with people.tree_edit(conn):
        where, params = sql_branch(old)
        nodes = conn.execute("SELECT id, tag FROM tag_taxonomy WHERE %s ORDER BY length(tag)" % where,
                             params).fetchall()
        if nodes and vocabulary.parent_of(new):
            add_path(conn, vocabulary.parent_of(new))
        joined = []
        # Parents first, so each node's new parent is in place when the node gets there.
        for node_id, tag in nodes:
            place = new + tag[len(old):]
            if conn.execute("SELECT 1 FROM tag_taxonomy WHERE tag = ?", (place,)).fetchone():
                joined.append(node_id)
                continue
            parent = vocabulary.parent_of(place)
            parent_id = conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?",
                                     (parent,)).fetchone()[0] if parent else None
            conn.execute("UPDATE tag_taxonomy SET tag = ?, name = ?, parent_id = ? WHERE id = ?",
                         (place, vocabulary.leaf_of(place), parent_id, node_id))
        if joined:
            conn.execute("DELETE FROM tag_taxonomy WHERE id IN (%s)" % ", ".join("?" * len(joined)), joined)
        return len(nodes)


def people_nodes(db_path, name):
    """The nodes holding faces whose name is `name`: where that person is filed."""
    return _nodes(db_path, "WHERE name = ? AND has_face = 1", (name,))


def tag_embeddings(db_path, tag):
    """How many CLIP embeddings the library caches for the word `tag`."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name='tag_embeddings'").fetchone():
            return 0
        return conn.execute("SELECT COUNT(*) FROM tag_embeddings WHERE tag = ?", (tag,)).fetchone()[0]
    finally:
        conn.close()


def tree_exists(conn):
    """Does the library open on `conn` have its tag tree's table?"""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                        " AND name = 'tag_taxonomy'").fetchone() is not None


def face_roots(db_path):
    """The roots of the library's tree that hold faces, as spelled, in order."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not tree_exists(conn):
            return []
        return [tag for (tag,) in conn.execute(
            "SELECT tag FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%' ORDER BY tag")]
    finally:
        conn.close()


def tree_has_nodes(conn):
    """Does the library open on `conn` have a tree with anything in it?"""
    return tree_exists(conn) and conn.execute("SELECT 1 FROM tag_taxonomy LIMIT 1").fetchone() is not None


def hidden_tags(conn):
    """The nodes hidden from autocomplete; a node under one is hidden too
    (vocabulary.hidden_by)."""
    if not tree_exists(conn):
        return set()
    return {tag for (tag,) in conn.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")}


def tags(conn):
    """Every node's tag; none without a tree."""
    if not tree_exists(conn):
        return []
    return [tag for (tag,) in conn.execute("SELECT tag FROM tag_taxonomy") if tag]


def node_ids(conn):
    """{tag: id} of every node; none without a tree."""
    if not tree_exists(conn):
        return {}
    return {tag: node_id for node_id, tag in conn.execute("SELECT id, tag FROM tag_taxonomy") if tag}


def remove_node(conn, tag):
    """Take one node out of the tree, and nothing under it. Returns nodes removed. The
    caller commits."""
    with people.tree_edit(conn):
        return conn.execute("DELETE FROM tag_taxonomy WHERE tag = ?", (tag,)).rowcount


def face_flags(conn):
    """(tag, has_face) of every node; none without a tree."""
    if not tree_exists(conn):
        return []
    return conn.execute("SELECT tag, has_face FROM tag_taxonomy").fetchall()


def embedded_tags(conn):
    """The words that have a CLIP embedding cached, under any prompt or model."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                        " AND name = 'tag_embeddings'").fetchone():
        return set()
    return {tag for (tag,) in conn.execute("SELECT DISTINCT tag FROM tag_embeddings") if tag}


def filed_people(conn):
    """{name: [tag]}: where the tree files each person, one read for all of them."""
    if not tree_exists(conn):
        return {}
    filed = {}
    for tag, name in conn.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1"):
        filed.setdefault(name, []).append(tag)
    return filed


def tag_embedding(conn, tag, prompt, model_name, pretrained):
    """The CLIP embedding cached for `tag` under this prompt and model, as float32 bytes,
    or None."""
    row = conn.execute(
        "SELECT embedding FROM tag_embeddings WHERE tag = ? AND prompt = ? AND model_name = ?"
        " AND pretrained = ?", (tag, prompt, model_name, pretrained)).fetchone()
    return row[0] if row else None


def keep_tag_embedding(conn, tag, prompt, model_name, pretrained, embedding):
    """Cache `embedding` (float32 bytes) for `tag` under this prompt and model. The caller
    commits."""
    conn.execute(
        "INSERT OR REPLACE INTO tag_embeddings (tag, prompt, model_name, pretrained, embedding)"
        " VALUES (?, ?, ?, ?, ?)", (tag, prompt, model_name, pretrained, embedding))


def forget_tag_embeddings(conn, tag):
    """Drop the CLIP embeddings cached for the word `tag`. The caller commits. Returns
    the rows dropped."""
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                        " AND name='tag_embeddings'").fetchone():
        return 0
    return conn.execute("DELETE FROM tag_embeddings WHERE tag = ?", (tag,)).rowcount


def delete_branch(conn, path):
    """Take the node `path` and every node under it out of the tree. The caller commits.
    Returns the nodes taken out."""
    with people.tree_edit(conn):
        where, params = sql_branch(path)
        return conn.execute("DELETE FROM tag_taxonomy WHERE " + where, params).rowcount


def set_branch_flags(conn, path, has_face=None, hidden=None):
    """Set a node's flags -- holding faces, hidden from autocomplete -- and the same on
    every node under it. None leaves a flag as it is. The caller commits. Returns the
    nodes the branch holds, when a flag was set."""
    with people.tree_edit(conn):
        where, params = sql_branch(path)
        changed = 0
        for column, value in (("has_face", has_face), ("hidden_from_autocomplete", hidden)):
            if value is not None:
                changed = conn.execute("UPDATE tag_taxonomy SET %s = ? WHERE %s" % (column, where),
                                       (value,) + params).rowcount
        return changed



# ---- The JSON file beside a library ----------------------------------------------------
#
# The tree lived in a JSON file beside each library, whose name decided which library it
# belonged to. The table is its only home since phase 4 (docs/findings.md, #13, #61); a
# copy is written only when asked for.

def export_json(db_path, target):
    """Write the tree's paths to `target` as JSON, {"paths": [...]}, a copy to keep or to
    read. Returns how many paths."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        paths_ = sorted(tags(conn))
    finally:
        conn.close()
    with open(target, "w", encoding="utf-8") as f:
        json.dump({"paths": paths_}, f, indent=2)
    return len(paths_)


def seed(db_path):
    """Give a library whose tree is empty its first nodes: the usual roots, every tag
    its photos carry, and everyone its faces name (under People). A tree with nodes in
    it is left alone."""
    try:
        schema.ensure(db_path)
        conn = db.connect(db_path, timeout=30.0)
        try:
            if conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0] > 0:
                return
            with people.tree_edit(conn):
                # One face root; the others are words. A library flags any other root
                # that holds faces itself (docs/findings.md, #66).
                add_path(conn, vocabulary.NEW_LIBRARY_FACE_ROOT, root_has_face=1)
                for root in vocabulary.NEW_LIBRARY_ROOTS:
                    add_path(conn, root)
                carried = set()
                for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL").fetchall():
                    try:
                        carried.update(json.loads(tags_json))
                    except (TypeError, ValueError):
                        pass
                for tag in carried:
                    add_path(conn, tag)
                # Under People, which holds faces.
                for (name,) in conn.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL").fetchall():
                    if name.strip():
                        add_path(conn, vocabulary.NEW_LIBRARY_FACE_ROOT + "/" + name)

            conn.commit()
            logger.info("Successfully seeded tag taxonomy database table.")
        finally:
            conn.close()
    except Exception as e:
        logger.error("Error seeding taxonomy from DB: %s", e)


# ---- The tree in memory -----------------------------------------------------------------

class TagTaxonomy:
    """The tag tree of one library, held in memory by the indexer and the suggester."""

    def __init__(self, db_path: str):
        # The library, not a JSON file whose name decided the library: the CLI's test
        # mode named test_photo_taxonomy.json and wrote into photo_index.db
        # (docs/findings.md, #61).
        self.db_path = db_path
        # Store full paths of known hierarchical tags, e.g., {"Family/Immediate/Jane Doe", "Activity/Botanical Garden"}
        self.paths: Set[str] = set()
        # What the database held when this was loaded or last saved. save_to_db adds
        # only paths beyond these: everything else is either still there or was
        # deleted or renamed by someone else since, and must stay that way.
        self._in_db: Set[str] = set()

    def load(self):
        """Read the library's tree. A library that does not exist has none: opening it
        would create it."""
        self.paths = set()
        self._in_db = set()
        if not os.path.exists(self.db_path):
            return
        try:
            conn = db.connect(db.readonly_uri(self.db_path), uri=True)
            try:
                self.paths = set(tags(conn))
            finally:
                conn.close()
            self._in_db = set(self.paths)
        except Exception as e:
            logger.error(f"Error loading taxonomy from DB: {e}")

    def save(self):
        """Add the paths this has gained to the library's tree (save_to_db)."""
        self.save_to_db()

    def save_to_db(self):
        """Sync self.paths with the database tag_taxonomy table."""
        if not os.path.exists(self.db_path):
            return
        try:
            seed(self.db_path)
            conn = db.connect(self.db_path, timeout=10.0)
            
            # Insert the paths added since this was loaded, and their ancestors. Not
            # the whole set: a long-running indexer holds what it loaded, and a tag
            # deleted or renamed in the app meanwhile came back on its next save.
            added = self.paths - self._in_db
            with people.tree_edit(conn):
                for path in sorted(added):
                    add_path(conn, path)
            conn.commit()
            conn.close()
            self._in_db |= added
        except Exception as e:
            logger.error(f"Error syncing taxonomy paths to DB: {e}")

    @staticmethod
    def normalize_tag(tag: str) -> str:
        """The one spelling of a tag (tagpup.core.vocabulary.normalize)."""
        return vocabulary.normalize(tag)

    def add_tag(self, tag: str):
        """Add a tag to the taxonomy, building all of its ancestor paths.

        A bare name that a people path already claims is not added as a root of its
        own. Photo keywords carry both forms in the wild -- a file may say
        "Cora Ingersoll" where the taxonomy says "People/Cora Ingersoll" -- and adding the
        bare one gives that person a second home, which is a choice nobody reading
        the Add Person list can make correctly. Only people are folded this way:
        "Kentridge" beside "School/Kentridge" is left alone, because deciding that
        for every tag is a different question and not this one.
        """
        normalized = self.normalize_tag(tag)
        if not normalized:
            return

        if "/" not in normalized and self.find_person_path(normalized):
            return

        self.paths.update(vocabulary.lineage(normalized))

    def add_tags(self, tags: List[str]):
        """Add multiple tags to the taxonomy."""
        for tag in tags:
            self.add_tag(tag)

    def people_roots(self) -> Set[str]:
        """Lowercased roots this library files people under (the module's people_roots)."""
        # Only a library that exists: opening one that does not creates it, and asking
        # who the people are made an empty library out of any name it was given.
        if getattr(self, "db_path", None) and os.path.exists(self.db_path):
            try:
                conn = db.connect(db.readonly_uri(self.db_path), uri=True)
                try:
                    return people_roots(conn)
                finally:
                    conn.close()
            except Exception:
                pass
        return {vocabulary.NEW_LIBRARY_FACE_ROOT.lower()}

    def find_person_path(self, name: str) -> Optional[str]:
        """An existing people path whose last segment is this name."""
        wanted = vocabulary.key(vocabulary.leaf_of(name))
        if not wanted:
            return None
        roots = self.people_roots()
        for path in self.paths:
            if "/" not in path:
                continue
            if vocabulary.key(vocabulary.root_of(path)) not in roots:
                continue
            if vocabulary.key(vocabulary.leaf_of(path)) == wanted:
                return path
        return None

    def find_by_leaf(self, name: str) -> Optional[str]:
        """The one existing path whose last segment is this name.

        None when the taxonomy has no such path, and also when it has two: a leaf
        under both "Trips/Boston MA" and "School/Boston MA" cannot be resolved without
        guessing which was meant, and a wrong guess files a photo under the wrong
        branch where nobody will look for it.
        """
        wanted = vocabulary.key(vocabulary.leaf_of(name))
        if not wanted:
            return None
        found = None
        for path in self.paths:
            if vocabulary.key(vocabulary.leaf_of(path)) != wanted:
                continue
            if found is not None and found != path:
                return None
            found = path
        return found

    def people_root(self) -> str:
        """The root this library files people under: People when the library flags it,
        else the first root it flags, else People -- the root a new library is given."""
        roots = self.people_roots()
        spelled = {vocabulary.key(vocabulary.root_of(p)): vocabulary.root_of(p) for p in self.paths}
        default = vocabulary.NEW_LIBRARY_FACE_ROOT
        if default.lower() in roots:
            return spelled.get(default.lower(), default)
        flagged = sorted(spelled[r] for r in roots if r in spelled)
        return flagged[0] if flagged else default

    def people_parent(self) -> str:
        """Where a newly seen person belongs, at the depth this library already uses.

        A library whose people live at Family/Immediate/<name> should not gain a
        Family/<name> beside them the first time somebody new turns up: that is a
        second, shallower home for people, which is the same fault as a bare root
        wearing different clothes. The commonest existing parent wins; the plain root
        is the fallback for a library with nobody in it yet.
        """
        roots = self.people_roots()
        parents = {}
        for path in self.paths:
            parts = vocabulary.segments(path)
            if len(parts) < 2:
                continue
            if vocabulary.key(parts[0]) not in roots:
                continue
            parent = vocabulary.parent_of(path)
            parents[parent] = parents.get(parent, 0) + 1
        if not parents:
            return self.people_root()
        # Deepest among the most common, so a tie does not silently flatten.
        best = max(parents.items(), key=lambda kv: (kv[1], kv[0].count("/")))
        return best[0]

    def add_people(self, names: List[str]):
        """Record people in the taxonomy, under a people root.

        `names` are leaf names: extract_people flattens a hierarchical keyword down
        to the person it names, because that is the form used for display and for
        matching. Passing them to add_tags instead treated each as a whole path and
        minted a bare root node per person, beside the People/<name> the keyword had
        already created -- and it ran on every index, so every cleanup was undone by
        the next run.

        A name already somewhere in the taxonomy is left where it is: the point is to
        avoid a second home for it, not to move the first one.
        """
        root = self.people_parent()
        for name in names:
            normalized = self.normalize_tag(name)
            if not normalized:
                continue
            if "/" in normalized:
                self.add_tag(normalized)
                continue
            if self.find_person_path(normalized):
                continue
            self.add_tag("%s/%s" % (root, normalized))

    def expand_tag(self, tag: str) -> List[str]:
        """Given a tag, if it matches a path in the taxonomy, expand it to include all ancestors."""
        normalized = self.normalize_tag(tag)
        if not normalized:
            return []
            
        matched_path = None
        if normalized in self.paths:
            matched_path = normalized
        else:
            sorted_paths = sorted(list(self.paths), key=len, reverse=True)
            for p in sorted_paths:
                if normalized == vocabulary.leaf_of(p) or p.endswith(vocabulary.SEPARATOR + normalized):
                    matched_path = p
                    break

        return vocabulary.lineage(matched_path or normalized)

    def get_root_categories(self) -> Dict[str, int]:
        """Get count of elements under each root (top-level) category."""
        roots = {}
        for path in self.paths:
            root = vocabulary.root_of(path)
            roots[root] = roots.get(root, 0) + 1
        return roots


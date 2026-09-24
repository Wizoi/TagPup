"""The tag tree's table, tag_taxonomy: what it says about people, its nodes, and
TagTaxonomy, the tree held in memory by the indexer and the suggester.

TagTaxonomy was scripts/taxonomy.py, which is now a name for this module.
"""
import json
import logging
import os
import threading
from typing import Dict, List, Optional, Set

from tagpup.core import paths, vocabulary
from tagpup.core.vocabulary import PeopleVocabulary
from tagpup.store import db, generations, schema

logger = logging.getLogger(__name__)

#: The roots a library files people under whatever its tree says (TagTaxonomy's).
PEOPLE_ROOTS = ("People", "Family", "Friends")


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

    A new root holds faces if `root_has_face` says so or its name does
    (vocabulary.root_holds_faces); a node made below another takes its parent's flag.
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
            has_face = 1 if root_has_face or vocabulary.root_holds_faces(part) else 0
        else:
            has_face = 1 if parent_has_face else 0
        parent_id = conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
            (level, parent_id, part, has_face)).lastrowid
        parent_has_face = has_face
    return parent_id


def people_roots(conn):
    """Lowercased roots the library open on `conn` files people under."""
    roots = {r.lower() for r in PEOPLE_ROOTS}
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'").fetchone():
        for (name,) in conn.execute("SELECT name FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%'"):
            if name:
                roots.add(name.strip().lower())
    return roots


#: One reading of each library's people, so resolving on every write costs nothing
#: after the first. Each entry is (the tree's generation when it was read, the mapping):
#: this process forgets it when it writes the tree (forget_people_paths), and the
#: generation catches the edits it cannot see -- TagTuner's, in another process.
_people_paths = {}
_people_paths_guard = threading.Lock()


def people_paths(db_path):
    """Every person the library's tag tree names, keyed by their lowercased leaf name.

    Someone filed in two places is left out: which one a bare name means cannot be
    told without guessing.
    """
    if not db_path:
        return {}
    key = paths.key(str(db_path))
    current = None
    if os.path.exists(db_path):
        try:
            conn = db.connect(db.readonly_uri(db_path), uri=True)
            try:
                current = generation(conn)
            finally:
                conn.close()
        except Exception:
            current = None
    with _people_paths_guard:
        cached = _people_paths.get(key)
    # A generation that cannot be read says nothing changed; what was read stands.
    if cached is not None and (current is None or cached[0] == current):
        return cached[1]

    mapping = {}
    try:
        if os.path.exists(db_path):
            conn = db.connect(db.readonly_uri(db_path), uri=True)
            try:
                roots = people_roots(conn)
                has_tree = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                        " AND name='tag_taxonomy'").fetchone()
                tags = [t for (t,) in conn.execute("SELECT tag FROM tag_taxonomy")] if has_tree else []
            finally:
                conn.close()
            for tag_path in tags:
                if not tag_path or "/" not in tag_path:
                    continue
                if vocabulary.key(vocabulary.root_of(tag_path)) not in roots:
                    continue
                leaf = vocabulary.key(vocabulary.leaf_of(tag_path))
                mapping[leaf] = None if leaf in mapping and mapping[leaf] != tag_path else tag_path
            mapping = {k: v for k, v in mapping.items() if v}
    except Exception as e:
        logger.debug("Could not load people paths from %s: %s", db_path, e)

    with _people_paths_guard:
        _people_paths[key] = (current, mapping)
    return mapping


def forget_people_paths(db_path=None):
    """Forget what was read of a library's people -- every library's, without one --
    after something changed its tree."""
    with _people_paths_guard:
        if db_path is None:
            _people_paths.clear()
        else:
            _people_paths.pop(paths.key(str(db_path)), None)


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
    """The PeopleVocabulary of the library open on `conn`."""
    has_tree = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'").fetchone()
    if not has_tree:
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


def hidden_tags(conn):
    """The nodes hidden from autocomplete; a node under one is hidden too
    (vocabulary.hidden_by)."""
    if not tree_exists(conn):
        return set()
    return {tag for (tag,) in conn.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")}


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
    where, params = sql_branch(path)
    return conn.execute("DELETE FROM tag_taxonomy WHERE " + where, params).rowcount


def set_branch_flags(conn, path, has_face=None, hidden=None):
    """Set a node's flags -- holding faces, hidden from autocomplete -- and the same on
    every node under it. None leaves a flag as it is. The caller commits. Returns the
    nodes the branch holds, when a flag was set."""
    where, params = sql_branch(path)
    changed = 0
    for column, value in (("has_face", has_face), ("hidden_from_autocomplete", hidden)):
        if value is not None:
            changed = conn.execute("UPDATE tag_taxonomy SET %s = ? WHERE %s" % (column, where),
                                   (value,) + params).rowcount
    return changed


# ---- The JSON file beside a library ----------------------------------------------------
#
# The tree used to live in a JSON file. The table replaced it, and the file is read only
# where the table is missing -- by TagTaxonomy.load and by seed() -- but the tree's edits
# still keep it in step. It goes in phase 4 (docs/findings.md, #13).

def json_file(db_path):
    """The JSON file kept beside a library: photo_taxonomy.json beside photo_index.db,
    <library>_taxonomy.json beside any other. The servers' Library.taxonomy_file names
    the first one differently (docs/findings.md, #13)."""
    if os.path.basename(db_path) == "photo_index.db":
        return os.path.join(os.path.dirname(db_path), "photo_taxonomy.json")
    return os.path.splitext(db_path)[0] + "_taxonomy.json"


def export_json(db_path):
    """Write the tree's paths to the JSON file beside the library, as TagTaxonomy.save
    did after each edit. A file that cannot be written is logged, not raised: the table
    is the tree."""
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            tags = sorted(tag for (tag,) in conn.execute("SELECT tag FROM tag_taxonomy"))
        finally:
            conn.close()
        with open(json_file(db_path), "w", encoding="utf-8") as f:
            json.dump({"paths": tags}, f, indent=2)
    except Exception as e:
        logger.error("Could not write the taxonomy JSON beside %s: %s", db_path, e)


def seed(db_path):
    """Give a library whose tree is empty its first nodes: the usual roots, every tag
    its photos carry, everyone its faces name (under People), and the paths in its JSON
    file. A tree with nodes in it is left alone."""
    try:
        schema.ensure(db_path)
        conn = db.connect(db_path, timeout=30.0)
        try:
            if conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0] > 0:
                return
            for root in ("People", "Activity", "Pets", "School", "Trips"):
                add_path(conn, root)

            tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "photos" in tables:
                carried = set()
                for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL").fetchall():
                    try:
                        carried.update(json.loads(tags_json))
                    except (TypeError, ValueError):
                        pass
                for tag in carried:
                    add_path(conn, tag)
            # Under People, which holds faces.
            if "faces" in tables:
                for (name,) in conn.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL").fetchall():
                    if name.strip():
                        add_path(conn, "People/" + name)

            if os.path.exists(json_file(db_path)):
                try:
                    with open(json_file(db_path), encoding="utf-8") as f:
                        for path in json.load(f).get("paths", []):
                            add_path(conn, path)
                except Exception as json_err:
                    logger.error("Error seeding from taxonomy json: %s", json_err)
            conn.commit()
            logger.info("Successfully seeded tag taxonomy database table.")
        finally:
            conn.close()
    except Exception as e:
        logger.error("Error seeding taxonomy from DB: %s", e)


# ---- The tree in memory -----------------------------------------------------------------

class TagTaxonomy:
    def __init__(self, file_path: Optional[str] = None, db_path: Optional[str] = None):
        if db_path is not None:
            self.db_path = db_path
            self.file_path = file_path if file_path is not None else json_file(db_path)
        else:
            if file_path is None:
                file_path = "data/photo_taxonomy.json"
            self.file_path = file_path
            if file_path.endswith("photo_taxonomy.json"):
                self.db_path = os.path.join(os.path.dirname(file_path), "photo_index.db")
            elif file_path.endswith("_taxonomy.json"):
                self.db_path = file_path.replace("_taxonomy.json", ".db")
            else:
                self.db_path = os.path.splitext(file_path)[0] + ".db"
        # Store full paths of known hierarchical tags, e.g., {"Family/Immediate/Jane Doe", "Activity/Botanical Garden"}
        self.paths: Set[str] = set()
        # What the database held when this was loaded or last saved. save_to_db adds
        # only paths beyond these: everything else is either still there or was
        # deleted or renamed by someone else since, and must stay that way.
        self._in_db: Set[str] = set()

    def load(self):
        """Load taxonomy from database tag_taxonomy table, falling back to JSON file if DB doesn't have it."""
        self.paths = set()
        self._in_db = set()
        loaded_from_db = False
        
        # 1. Try to load from database
        if os.path.exists(self.db_path):
            try:
                conn = db.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
                if cursor.fetchone():
                    cursor.execute("SELECT tag FROM tag_taxonomy")
                    for row in cursor.fetchall():
                        self.paths.add(row[0])
                    self._in_db = set(self.paths)
                    loaded_from_db = True
                conn.close()
            except Exception as e:
                logger.error(f"Error loading taxonomy from DB: {e}")
                
        # 2. Fall back to JSON file if not loaded from DB and JSON exists
        if not loaded_from_db and os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.paths = set(data.get("paths", []))
                logger.info(f"Loaded taxonomy from JSON fallback with {len(self.paths)} paths.")
                # Since we have JSON but not DB, we can write it to DB if DB exists
                if os.path.exists(self.db_path):
                    self.save_to_db()
            except Exception as e:
                logger.error(f"Error loading taxonomy JSON fallback: {e}")

    def save(self):
        """Save taxonomy to both JSON file (for backward compatibility) and database."""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"paths": sorted(list(self.paths))}, f, indent=2)
            logger.info(f"Saved taxonomy to JSON with {len(self.paths)} paths.")
        except Exception as e:
            logger.error(f"Error saving taxonomy to JSON: {e}")
            
        self.save_to_db()

    def save_to_db(self):
        """Sync self.paths with the database tag_taxonomy table."""
        if not os.path.exists(self.db_path):
            return
        try:
            schema.ensure(self.db_path)
            conn = db.connect(self.db_path, timeout=10.0)
            
            # Insert the paths added since this was loaded, and their ancestors. Not
            # the whole set: a long-running indexer holds what it loaded, and a tag
            # deleted or renamed in the app meanwhile came back on its next save.
            added = self.paths - self._in_db
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

    #: Roots that hold people. A library may use any of them, or its own.
    DEFAULT_PEOPLE_ROOTS = PEOPLE_ROOTS

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
        return {r.lower() for r in PEOPLE_ROOTS}

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
        """The root this library files people under.

        Whichever of the usual people roots already exists, so a library using
        "Family" does not suddenly grow a "People" beside it. Falls back to People.
        """
        existing = {vocabulary.key(vocabulary.root_of(p)) for p in self.paths}
        for root in self.DEFAULT_PEOPLE_ROOTS:
            if root.lower() in existing:
                return root
        return self.DEFAULT_PEOPLE_ROOTS[0]

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


#: The old name, kept for scripts/taxonomy.py's callers.
seed_taxonomy_from_db = seed

"""The tag tree's table, tag_taxonomy: what it says about people, and its nodes.

The in-memory TagTaxonomy in scripts/taxonomy.py still moves here (ARCHITECTURE.md,
phase 2).
"""
import json
import logging
import os
import sqlite3
import threading

from tagpup.core import paths, vocabulary
from tagpup.core.vocabulary import PeopleVocabulary
from tagpup.store import db

logger = logging.getLogger(__name__)

#: The roots a library files people under whatever its tree says (TagTaxonomy's).
PEOPLE_ROOTS = ("People", "Family", "Friends")


def generation(conn):
    """The tag tree's generation counter (PhotoIndex keeps it moving with triggers), or
    0 on a library that does not have it yet."""
    try:
        row = conn.execute("SELECT generation FROM taxonomy_generation WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


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
        conn = db.connect(db_path, timeout=30.0)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tag_taxonomy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag TEXT UNIQUE,
                    parent_id INTEGER,
                    name TEXT,
                    has_face INTEGER DEFAULT 0,
                    hidden_from_autocomplete INTEGER DEFAULT 0,
                    FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
                )
            """)
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

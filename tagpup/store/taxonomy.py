"""The tag tree's table, tag_taxonomy.

For now, what the tree says about people. The rest of scripts/taxonomy.py moves here
with the store step of phase 2 (ARCHITECTURE.md).
"""
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

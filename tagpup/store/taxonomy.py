"""The tag tree's table, tag_taxonomy.

For now, what the tree says about people. The rest of scripts/taxonomy.py moves here
with the store step of phase 2 (ARCHITECTURE.md).
"""
import logging
import os

from tagpup.core.vocabulary import PeopleVocabulary
from tagpup.store import db

logger = logging.getLogger(__name__)


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

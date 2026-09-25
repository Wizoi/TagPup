"""A library's settings table: reading it (migration 10; docs/ARCHITECTURE.md, phase 7.6).

Nothing here writes: a setting is changed only through the journal
(tagpup.store.journal, called by tagpup.services.settings), so each change is in the
library's history and can be undone. What a value means, and what it may be, is
tagpup.core.validation.SETTINGS'; the service fills in the defaults.
"""
from tagpup.store import db, schema

TABLE = "settings"


def _rows(conn):
    return {key: value for key, value in conn.execute("SELECT key, value FROM settings")}


def read(db_path):
    """{key: value} of every setting the library at `db_path` holds; {} for a library
    never stamped. Brings the library to the current schema first."""
    schema.ensure(db_path)
    conn = db.connect(db_path)
    try:
        return _rows(conn)
    finally:
        conn.close()


def read_only(db_path):
    """read(), without writing anything to the library: {} for one whose schema has no
    settings table yet (a library not opened since migration 10)."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'").fetchone() is None:
            return {}
        return _rows(conn)
    finally:
        conn.close()

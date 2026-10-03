"""A library's settings table: reading it (migration 10; docs/ARCHITECTURE.md, phase 7.6).

Nothing here writes: a setting is changed only through the journal
(tagpup.store.journal, called by tagpup.services.settings), so each change is in the
library's history and can be undone. What a value means, and what it may be, is
tagpup.core.validation.SETTINGS'; the service fills in the defaults.
"""
from tagpup.store import db, schema
from tagpup.store import roots as store_roots

TABLE = "settings"


def _rows(conn):
    """{key: value} of the settings, the two that name folders (library.roots,
    library.ignored) as this machine reads them: a library may hold a folder under a root as
    the root's row, and what is shown and compared is the native path (tagpup.store.roots).
    Their journaled values hold the row."""
    held = {key: value for key, value in conn.execute("SELECT key, value FROM settings")}
    roots = store_roots.roots_for(conn)
    if not roots.identity:
        for key in store_roots.FOLDER_SETTINGS:
            if key in held:
                held[key] = store_roots.folders_to_native(held[key], roots)
    return held


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

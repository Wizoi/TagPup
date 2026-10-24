"""A library's settings table: reading it (migration 10; docs/ARCHITECTURE.md, phase 7.6).

Nothing here writes: a setting is changed only through the journal
(tagpup.store.journal, called by tagpup.services.settings), so each change is in the
library's history and can be undone. What a value means, and what it may be, is
tagpup.core.validation.SETTINGS'; the service fills in the defaults.
"""
from tagpup.core import validation
from tagpup.store import db, schema
from tagpup.store import roots as store_roots

TABLE = "settings"

#: The setting that names the library's face model, and the key its face vectors are read under (migration 33).
FACE_MODEL = "faces.model"


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
    """read(), without writing anything to the library."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return _rows(conn)
    finally:
        conn.close()


def held_on(conn):
    """read(), on the caller's connection: what an undo's check reads in the transaction it asks in."""
    return _rows(conn)


def face_model(conn):
    """The library's face model on `conn` (faces.model), trimmed as the validator trims, its default for a library that holds
    none: the key every reader of a face's vector reads `face_embeddings` under (tagpup.store.faces). Read on the reader's own
    connection, in its transaction, so one read cannot mix two models' vectors. A writer does not ask it: it files a vector
    under the model that made it."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (FACE_MODEL,)).fetchone()
    return validation.trim(row[0]) if row else validation.SETTINGS[FACE_MODEL]["default"]

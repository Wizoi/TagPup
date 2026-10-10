"""The ids folders carry: `folder_ids` and `library_identity` (migration 26; docs/ARCHITECTURE.md,
"Folder ids").

`library_identity` holds at most one row, the library's own identifier (a random UUID). Nothing
opens a library into having one: it is stamped by the first explicit `folder-ids mark --apply`,
in the transaction that records the first ids (`stamp`), because an identifier handed out in
marker files (tagpup.files.folder_marker) cannot be taken back. A snapshot restored keeps it
-- it is that library -- and a library file copied for a trial carries the same one, which
`twins` finds so that two files answering to one entry are refused and named.

`folder_ids` has a row for each folder the library has marked: the id the folder's marker holds
for this library (the key), the folder as last seen, spelled as `photos.path` is (native here,
`@name/rel` in the library: the journal converts both ways, tagpup.store.roots), and when it was
marked. It is journaled -- a change inserts and moves its rows, History lists it and undo
reverses it -- and it is not derived: the `folders` table is rebuilt from the photos and its
integer ids change with them, which is why a folder tag cannot hang on it.

A library behind this version has neither table, and has marked nothing: every read here
answers as an empty one.
"""
import os
import time

from tagpup.core.library import Library
from tagpup.store import db, journal
from tagpup.store import roots as store_roots

TABLE = "folder_ids"
IDENTITY = "library_identity"

#: What the journaled change that follows marked folders is recorded as: here, with the table it moves rows of, so that
#: the services that undo it (folder_follow) and write it (folder_ids) name it without importing one another.
FOLLOW_OPERATION = "follow_folder_markers"

TIME = "%Y-%m-%d %H:%M:%S"


def _there(conn):
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
                        (TABLE, IDENTITY)).fetchone()[0] == 2


def now():
    return time.strftime(TIME)


def identity(conn):
    """The library's identifier, or None while it has not been stamped (or the library has no
    such table yet)."""
    if not _there(conn):
        return None
    row = conn.execute("SELECT id FROM library_identity WHERE slot = 1").fetchone()
    return row[0] if row else None


def stamp(conn, library_id):
    """Stamp the library with `library_id` unless it is stamped already, and return the
    identifier it holds. The caller holds the transaction and commits: the first mark's, with
    the ids that carry it."""
    conn.execute("INSERT OR IGNORE INTO library_identity (slot, id, stamped) VALUES (1, ?, ?)", (library_id, now()))
    return conn.execute("SELECT id FROM library_identity WHERE slot = 1").fetchone()[0]


def rows(conn):
    """[(id, folder as this machine spells it, when marked)] of every folder marked."""
    if not _there(conn):
        return []
    return store_roots.natives(conn, conn.execute("SELECT id, path, marked FROM folder_ids").fetchall(), 1)


def held(db_path):
    """(the library's identifier, [(folder id, folder AS THE LIBRARY HOLDS IT)]) read from the file at `db_path`, with
    a connection of its own and the map of no machine: `@name/rel` under a root, native under none. Verify of a place the
    map does not say yet turns them into paths itself (tagpup.services.roots_verify). (None, []) for a library that has no
    such tables (a schema behind migration 26) or has marked nothing. Read-only; the two small columns only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        if not _there(conn):
            return None, []
        return identity(conn), [tuple(row) for row in conn.execute("SELECT id, path FROM folder_ids")]
    finally:
        conn.close()


def count(conn):
    if not _there(conn):
        return 0
    return conn.execute("SELECT COUNT(*) FROM folder_ids").fetchone()[0]


def insert_edit(folder, folder_id, marked=None):
    """The journal's edit recording `folder_id` for `folder` (native)."""
    return journal.insert(TABLE, {"id": folder_id, "path": folder, "marked": marked or now()}, kind="marked")


def move_edit(folder_id, old, new):
    """The journal's edit pointing `folder_id` at `new` while it names `old` (native)."""
    return journal.update(TABLE, (folder_id,), {"path": old}, {"path": new}, kind="followed")


def identity_of(db_path):
    """The identifier the library file at `db_path` carries, or None: not stamped, no table,
    or a file that cannot be read now (locked, not a library). Reads only."""
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
    except Exception:
        return None
    try:
        return identity(conn)
    except Exception:
        return None
    finally:
        conn.close()


def twins(db_path, library_id):
    """The file names of the other libraries in the same data folder that carry `library_id`:
    a library file copied for a trial keeps its identifier, and two libraries answering to one
    entry of a marker would follow each other's folders. [] for no identifier. Only the folder
    the library is in is looked at (its backups are snapshots of it, not libraries)."""
    if not library_id:
        return []
    folder = os.path.dirname(os.path.abspath(db_path))
    me = Library(db_path).key
    found = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    for name in names:
        other = os.path.join(folder, name)
        if not name.endswith(".db") or Library(other).key == me or not os.path.isfile(other):
            continue
        if identity_of(other) == library_id:
            found.append(name)
    return found

"""The photo files found damaged: `damaged_files` (migration 16; docs/findings.md, #407).

The indexer decodes each photo's whole picture before it writes anything into it
(tagpup.files.metadata.IdentityWriter). A photo that does not decode -- a file cut short,
one of zero bytes -- is not indexed and gets no row, so sync saw it as new every time and
queued the indexer for it again: each run loaded the photo index and CLIP to fail on it
again. Remembered here with its stamp (mtime and size) as the indexer found it, it is not
queued again until the file changes (tagpup.services.sync), and it is listed for the
owner to restore. A photo that decodes but ends in a long run of zero bytes -- possibly
an incomplete copy, tagpup.files.images.ZERO_TAIL -- is indexed, and kept here too, as
INCOMPLETE, so it is listed beside them.

A record of what was found, not a change of the library's photos: not journaled, as
indexing is not. A record describes its file only while the file has its stamp: one
replaced or changed no longer does, and is left out of every list (tagpup.services.
damaged_photos) until the next sync or index forgets it.
"""
import collections
import time

from tagpup.core import paths

TABLE = "damaged_files"

#: How a record spells a time (the journal's format).
TIME = "%Y-%m-%d %H:%M:%S"

#: The kind of a photo that decodes and may be an incomplete copy: indexed, and listed.
#: Every other kind is one of tagpup.files.images.DAMAGE: the picture does not decode.
INCOMPLETE = "incomplete"

#: One record: the file (as stored), its stamp when found, how it is damaged and what the
#: decoder said, the zero bytes it ends in, when it was first and last found, and the run
#: that first found it (tagpup.core.runs), or None.
Record = collections.namedtuple("Record", "path mtime size kind detail zero_tail found seen run")

_COLUMNS = "path, mtime, size, kind, detail, zero_tail, found, seen, run"


def _there(conn):
    """Has the library the table? One behind this version's schema, opened read-only for a
    look, has none, and has found nothing."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def record(conn, photo_path, stamp, kind, detail, zero_tail=0, run=None, now=None):
    """Record the file at `photo_path`, whose stamp was (mtime, size) as it was read, as
    damaged: `kind`, `detail`. True when this is a new finding -- the file was not
    recorded, or was recorded with another stamp or kind -- else False, and only when it
    was last found moves. The caller commits."""
    now = now or time.strftime(TIME)
    stored = paths.stored(photo_path)
    where, params = paths.sql_equals("path", stored)
    held = conn.execute("SELECT mtime, size, kind FROM damaged_files WHERE " + where, params).fetchone()
    if held is not None and (held[0], held[1], held[2]) == (stamp[0], stamp[1], kind):
        conn.execute("UPDATE damaged_files SET seen = ?, detail = ? WHERE " + where, (now, detail) + params)
        return False
    conn.execute("INSERT INTO damaged_files (" + _COLUMNS + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                 " ON CONFLICT(path) DO UPDATE SET path = excluded.path, mtime = excluded.mtime,"
                 " size = excluded.size, kind = excluded.kind, detail = excluded.detail,"
                 " zero_tail = excluded.zero_tail, found = excluded.found, seen = excluded.seen, run = excluded.run",
                 (stored, stamp[0], stamp[1], kind, detail, int(zero_tail or 0), now, now, run))
    return True


def forget(conn, photo_paths):
    """Forget the records of `photo_paths`. Returns records removed. The caller commits."""
    removed = 0
    for photo_path in photo_paths:
        where, params = paths.sql_equals("path", photo_path)
        removed += conn.execute("DELETE FROM damaged_files WHERE " + where, params).rowcount
    return removed


def forget_as_found(conn, found):
    """Forget each of `found`, [Record], while it holds the stamp it was read with: a
    record made again meanwhile, of the file as it is now, is kept. Returns records
    removed. The caller commits."""
    removed = 0
    for each in found:
        where, params = paths.sql_equals("path", each.path)
        removed += conn.execute("DELETE FROM damaged_files WHERE " + where + " AND mtime = ? AND size = ?",
                                params + (each.mtime, each.size)).rowcount
    return removed


def forget_under(conn, folder):
    """Forget the records of the files at or under `folder`: it is taken out of the
    library. Returns records removed. The caller commits."""
    if not _there(conn):
        return 0
    under, params = paths.sql_under("path", folder)
    return conn.execute("DELETE FROM damaged_files WHERE " + under, params).rowcount


def every(conn):
    """[Record] of every file recorded, by path."""
    if not _there(conn):
        return []
    return [Record(*row) for row in conn.execute("SELECT " + _COLUMNS + " FROM damaged_files ORDER BY path")]


def under(conn, folder):
    """[Record] of the files recorded at any depth under `folder`, by path."""
    if not _there(conn):
        return []
    where, params = paths.sql_under("path", folder)
    return [Record(*row) for row in conn.execute(
        "SELECT " + _COLUMNS + " FROM damaged_files WHERE " + where + " ORDER BY path", params)]

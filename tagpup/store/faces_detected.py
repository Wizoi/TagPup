"""The photos whose faces were detected: `faces_detected` (migration 25; docs/findings.md,
#773), one row for each photo a detector has run on -- which detector (its settings), the
file's stamp it read, how many faces it found -- whether or not it found any.

A photo with faces has rows in `faces`; a photo the index read and found no face in had
nothing at all, so Suggest could not tell it from one never looked at: it took the
graphics card, loaded the face model and detected again, every time (10,732 of
photo_index's 68,324 photos had no face). A row here says detection ran, so Suggest asks
it before detecting (tagpup.services.suggester). Written by the indexer (record_batch) and
by Suggest (record_detected) for every photo they detect; a photo marked as having faces
still to detect (tagpup.store.faces_pending) has its row taken away.

A row is read only under the detector it names: detected again under other face settings,
a photo may find faces it did not. The stamp is kept, not compared: a write of a photo's
metadata changes its stamp and not its picture, as with the faces' own rows, which are not
compared either. Photos indexed before migration 25 have no row and are detected once
more, by the next Suggest that looks at them: no rule tells from the library that the
index detected a photo's faces (an index run with --skip-faces made vectors without, and
Suggest makes vectors of photos it never detected faces in).

Like faces_pending: no foreign key and no trigger -- a row whose photo is gone is read as
none, a photo's id never being handed out again -- so deleting a photo takes nothing the
journal must account for; not journaled, as indexing is not.
"""
import json
import time

from tagpup.store import roots as store_roots

TABLE = "faces_detected"

#: How a row spells a time (the journal's format).
TIME = "%Y-%m-%d %H:%M:%S"


def detector(settings):
    """The name a detector is kept under: its settings ({"min_face_size", ...}), as JSON
    with sorted keys."""
    return json.dumps(settings, sort_keys=True)


def _there(conn):
    """Has the library the table? One behind this version's schema has none."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (TABLE,)).fetchone() is not None


def _photo_id(conn, photo_path):
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id FROM photos WHERE " + where + " LIMIT 1", params).fetchone()
    return row[0] if row else None


def record(conn, photo_path, name, stamp, found, now=None):
    """Record that detector `name` ran on the photo at `photo_path`, its file at `stamp`
    ((mtime, size) or None), and found `found` faces. 1 when the photo has a row to record
    it on, else 0. The caller commits."""
    if not _there(conn):
        return 0
    photo_id = _photo_id(conn, photo_path)
    if photo_id is None:
        return 0
    mtime, size = stamp if stamp else (None, None)
    conn.execute("INSERT OR REPLACE INTO faces_detected (photo_id, detector, mtime, size, found, at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (photo_id, name, mtime, size, int(found), now or time.strftime(TIME)))
    return 1


def detected(conn, photo_path, name):
    """Has detector `name` run on the photo at `photo_path`?"""
    if conn is None or not _there(conn):
        return False
    where, params = store_roots.sql_equals(conn, "p.path", photo_path)
    return conn.execute("SELECT 1 FROM photos p JOIN faces_detected d ON d.photo_id = p.id WHERE " + where
                        + " AND d.detector = ? LIMIT 1", tuple(params) + (name,)).fetchone() is not None


def forget_faceless(conn, photo_ids):
    """Take away the row of each photo of `photo_ids` that has no face row now: its faces were deleted (a photo's
    faces removed, a face deleted by id), so what the row says -- detection ran and these are its faces -- is no
    longer true, and Suggest detects it again (docs/findings.md, #779). A photo that keeps a face keeps its row.
    Returns rows taken away. The caller commits."""
    photo_ids = sorted(photo_ids)
    if not photo_ids or not _there(conn):
        return 0
    removed = 0
    for start in range(0, len(photo_ids), 500):
        chunk = photo_ids[start:start + 500]
        removed += conn.execute(
            "DELETE FROM faces_detected WHERE photo_id IN (%s)"
            " AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = faces_detected.photo_id)"
            % ",".join("?" * len(chunk)), chunk).rowcount
    return removed


def forget(conn, photo_id):
    """Take the photo's row away: its faces are to be detected again. The caller commits."""
    if _there(conn):
        conn.execute("DELETE FROM faces_detected WHERE photo_id = ?", (photo_id,))

"""The photos whose faces were detected: `faces_detected` (migration 25; docs/findings.md,
#773), one row for each photo a detector has run on -- which detector (its settings), the
file's stamp it read, how many faces it found -- whether or not it found any.

A photo with faces has rows in `faces`; a photo the index read and found no face in had
nothing at all, so Suggest could not tell it from one never looked at: it took the
graphics card, loaded the face model and detected again, every time (10,732 of
photo_index's 68,324 photos had no face). A row here says detection ran, so Suggest asks
it before detecting (tagpup.services.suggester). Written by the indexer (record_batch) and
by Suggest (record_detected) for every photo they detect, and by the new face model's pass
(tagpup.services.face_pass) for a photo it found no face in and that holds none.

A row is read only under the detector it names: detected again under other face settings,
a photo may find faces it did not. The stamp is kept, not compared: a write of a photo's
metadata changes its stamp and not its picture, as with the faces' own rows, which are not
compared either -- but for its Orientation, which changes the picture the detector looks at
(it detects on the photo as shown): a rotation takes the row away (`forget`,
tagpup.services.rotation), so the photo is looked at again. Photos indexed before migration 25 have no row and are detected once
more, by the next Suggest that looks at them: no rule tells from the library that the
index detected a photo's faces (an index run with --skip-faces made vectors without, and
Suggest makes vectors of photos it never detected faces in).

No foreign key and no trigger -- a row whose photo is gone is read as
none, a photo's id never being handed out again -- so deleting a photo takes nothing the
journal must account for; not journaled when written, as indexing is not. A change that deletes rows records them
(tagpup.store.journal.KEYS: the old detector's rows, tagpup.services.old_detector_rows), so its undo puts them back.
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


def detectors(conn):
    """{detector name: rows recorded under it} of the library on `conn`: one scan of the table (one row a photo)."""
    return dict(conn.execute("SELECT detector, COUNT(*) FROM faces_detected GROUP BY detector"))


def rows_under(conn, names):
    """{photo id: the row as stored, by column} of the rows recorded under any detector of `names`, in photo id order."""
    names = sorted(names)
    if not names:
        return {}
    cursor = conn.execute("SELECT photo_id, detector, mtime, size, found, at FROM faces_detected WHERE detector IN (%s)"
                          " ORDER BY photo_id" % ",".join("?" * len(names)), names)
    columns = [column[0] for column in cursor.description]
    return {row[0]: dict(zip(columns[1:], row[1:])) for row in cursor}


def record(conn, photo_path, name, stamp, found, now=None):
    """Record that detector `name` ran on the photo at `photo_path`, its file at `stamp`
    ((mtime, size) or None), and found `found` faces. 1 when the photo has a row to record
    it on, else 0. The caller commits."""
    photo_id = next(iter(store_roots.photo_ids_at(conn, [photo_path])), None)
    if photo_id is None:
        return 0
    mtime, size = stamp if stamp else (None, None)
    conn.execute("INSERT OR REPLACE INTO faces_detected (photo_id, detector, mtime, size, found, at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (photo_id, name, mtime, size, int(found), now or time.strftime(TIME)))
    return 1


def forget(conn, photo_path):
    """Take away the row of the photo at `photo_path`, under any detector: its picture as shown turned (a rotation), so
    what was found in it is not what is there now. Not journaled, as the photo's vectors are not: a fact of the picture,
    which the next look records again. Returns rows deleted. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return conn.execute("DELETE FROM faces_detected WHERE photo_id IN (SELECT id FROM photos WHERE %s)" % where,
                        params).rowcount


def detected(conn, photo_path, name):
    """Has detector `name` run on the photo at `photo_path`?"""
    if conn is None:
        return False
    where, params = store_roots.sql_equals(conn, "p.path", photo_path)
    return conn.execute("SELECT 1 FROM photos p JOIN faces_detected d ON d.photo_id = p.id WHERE " + where
                        + " AND d.detector = ? LIMIT 1", tuple(params) + (name,)).fetchone() is not None


def faceless_under(conn, name, photo_ids):
    """The ids among `photo_ids` of the photos detector `name` has run on that hold no face row: those it found no face
    in (or none it made a face of), and none was made since. The face pass's photos done that hold no face. Chunked; the
    row's key and idx_faces_photo_id."""
    photo_ids = sorted(photo_ids)
    found = set()
    for start in range(0, len(photo_ids), 500):
        chunk = photo_ids[start:start + 500]
        found.update(photo_id for (photo_id,) in conn.execute(
            "SELECT d.photo_id FROM faces_detected d WHERE d.photo_id IN (%s) AND d.detector = ?"
            " AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = d.photo_id)" % ",".join("?" * len(chunk)),
            chunk + [name]))
    return found


def forget_faceless(conn, photo_ids):
    """Take away the row of each photo of `photo_ids` that has no face row now and whose detector found some: its faces
    were deleted (a photo's faces removed, a face deleted by id), so what the row says -- detection ran and these are its
    faces -- is no longer true, and Suggest detects it again (docs/findings.md, #779). A photo that keeps a face keeps
    its row; so does a row that found none, which no deletion of faces makes untrue: a photo the detector found no face
    in, given faces by another (the new face model's pass) and those undone, is as that detector left it.
    Returns rows taken away. The caller commits."""
    photo_ids = sorted(photo_ids)
    if not photo_ids:
        return 0
    removed = 0
    for start in range(0, len(photo_ids), 500):
        chunk = photo_ids[start:start + 500]
        removed += conn.execute(
            "DELETE FROM faces_detected WHERE photo_id IN (%s) AND found > 0"
            " AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = faces_detected.photo_id)"
            % ",".join("?" * len(chunk)), chunk).rowcount
    return removed

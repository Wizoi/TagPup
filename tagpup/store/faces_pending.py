"""The photos whose faces are still to be detected: `faces_pending` (migration 17;
docs/findings.md, #407).

A photo indexed from a damaged file -- a possibly incomplete copy, grey below a line -- has
its vectors and its undecided faces taken away once the file reads whole
(tagpup.services.damaged_photos.forget_to_reindex), and its folder queued to be indexed.
But the indexer passes over a photo whose row describes its file and that has a vector,
and Suggest makes a vector of any photo it looks at: between the two, the queued index
skipped the photo and its faces were never detected. So the photo is marked here, and the
indexer detects its faces whether or not it has a vector; recording the faces detected
in it -- by the indexer or by Suggest (tagpup.services.faces) -- clears the mark. A mark
whose photo's row is gone is read as none: a photo's id is never handed out again, and
deleting a photo takes nothing the journal must account for. Counted by tools/doctor.py
and the Activity page, so a mark no index has cleared -- a sync that queued nothing -- is
seen.
"""
import time

from tagpup.core import paths
from tagpup.store import faces_detected
from tagpup.store import roots as store_roots

TABLE = "faces_pending"

#: How a mark spells a time (the journal's format).
TIME = "%Y-%m-%d %H:%M:%S"


def _there(conn):
    """Has the library the table? One behind this version's schema has none."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def mark(conn, photo_path, now=None):
    """Mark the photo at `photo_path` as having faces still to detect. Returns 1 when it has
    a row to mark, else 0. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id FROM photos WHERE " + where + " LIMIT 1", params).fetchone()
    if row is None:
        return 0
    conn.execute("INSERT OR REPLACE INTO faces_pending (photo_id, since) VALUES (?, ?)",
                 (row[0], now or time.strftime(TIME)))
    # Its faces are to be detected again: no record says they were (store.faces_detected).
    faces_detected.forget(conn, row[0])
    return 1


def _none(conn):
    """Is no photo marked -- the common case, answered by one look at the table's first row?"""
    return not _there(conn) or conn.execute("SELECT 1 FROM faces_pending LIMIT 1").fetchone() is None


def clear(conn, photo_paths):
    """Clear the marks of `photo_paths`: their faces were detected. Nothing is looked up
    while no photo is marked, the common case. Returns marks cleared. The caller commits."""
    if _none(conn):
        return 0
    cleared = 0
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        cleared += conn.execute("DELETE FROM faces_pending WHERE photo_id IN (SELECT id FROM photos WHERE "
                                + where + ")", params).rowcount
    return cleared


def pending(conn):
    """The paths, as stored, of the photos marked, by path. At once when none is."""
    if _none(conn):
        return []
    return sorted((path for (path,) in store_roots.natives(conn, conn.execute(
        "SELECT p.path FROM faces_pending f JOIN photos p ON p.id = f.photo_id").fetchall(), 0)), key=paths.key)


def count(conn):
    """How many photos are marked (whose rows are there). At once when none is: each page's
    Needs attention and each index ask."""
    if _none(conn):
        return 0
    return conn.execute("SELECT COUNT(*) FROM faces_pending f JOIN photos p ON p.id = f.photo_id").fetchone()[0]

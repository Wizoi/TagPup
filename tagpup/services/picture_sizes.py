"""Fill the picture size (photos.width, height) of the photos a library indexed before migration 39: a ONE-OFF, run by
scripts/fill_picture_sizes.py, and deleted with it after its run on the three libraries (DECISIONS 2026-11-09, "MIGRATION
39: A PHOTO'S PICTURE SIZE", item 4; #2804). The steady state needs nothing of this: the index records the size of every
photo it reads (store.photos.record_indexed), and a row with no size is one not read.

A photo whose row has no size (kind 'photo', width NULL) is looked at through tagpup.files.shares: its stamp first, by a
patient walk (shares.Walk: a share asleep is waited for once and asked again; one that still does not answer is away, its
photos after it counted away at once), then its header, read within shares.READ_WAIT (shares.read_bounded), of the file
open only while it is the one stamped. The size is images.picture_size's, the indexer's own reader: the stored pixels, the
frame of the face boxes, nothing decoded or turned. Each look ends in one of OUTCOMES:
- `sized`: the header gave a width and a height above 0;
- `unreadable`: the header gave (0, 0), or the file is there and cannot be read: the row stays NULL;
- `away`: its network share did not answer: the row stays NULL, a run later fills it;
- `gone`: no file is at the row's path: a sync's to settle, not this;
- `stale`: the file is not the one the row describes (its mtime or size differs): the index reads it again, sizing it.

Only a row still NULL and still stamped as when its file was read is written (store.photos.record_picture_sizes), a chunk
at a time in a transaction of its own (db.write_with_connection): an interruption keeps every chunk written, and a run again
goes on from the rows still NULL. `wrote` is the rows whose columns changed, not the photos read. Counts only: no path.
"""
import collections
import os
import statistics
import time
from contextlib import closing

from tagpup.files import images, lock_owners, shares
from tagpup.services import batches, segment_census
from tagpup.store import db
from tagpup.store import photos as photo_rows

SIZED, UNREADABLE, AWAY, GONE, STALE = "sized", "unreadable", "away", "gone", "stale"
#: What one photo's look ends in, in the order a run says them.
OUTCOMES = (SIZED, UNREADABLE, AWAY, GONE, STALE)
#: Why a read did not happen (shares.why_not_read's words), as an outcome.
_NOT_READ = {shares.NOT_ANSWERED: AWAY, shares.GONE: GONE, shares.CHANGED: STALE, shares.UNREADABLE: UNREADABLE}
#: The photos read between two commits.
CHUNK = batches.CHUNK
#: The rows asked of the library at a time.
PAGE = 500
#: How many photos a dry run reads, timed, to say how long --apply takes; drawn with SEED (segment_census.drawn).
SAMPLE = 200
SEED = 39


def never():
    return False


def _header(path, stamp):
    """(width, height) of the picture at `path` from its header (images.picture_size), read from the file open only while
    it is the one stamped `stamp`; raises shares.FileChanged for another, and the OSError of the open or the read. Opened
    sharing delete as well as read and write, so a rename of the file meanwhile is not refused (lock_owners.opened)."""
    with lock_owners.opened(path, lock_owners.ALL_SHARING) as handle:
        found = os.fstat(handle.fileno())
        if not photo_rows.describes(found.st_mtime, found.st_size, stamp):
            raise shares.FileChanged("The file changed since it was looked at.")
        return images.picture_size(handle)


def look(walk, path, mtime, size):
    """(outcome, (width, height) or None) of the photo at `path` whose row is stamped (`mtime`, `size`): OUTCOMES' words.
    Never raises for the file: what could not be read is counted."""
    stamp = walk.stamp_of(path)
    if stamp is shares.UNANSWERED:
        return AWAY, None
    if stamp is None:
        return GONE, None
    if stamp is shares.CANNOT_READ:
        return UNREADABLE, None
    if not photo_rows.describes(mtime, size, stamp):
        return STALE, None
    state, found = shares.read_bounded(path, lambda: _header(path, stamp))
    if state == "ok":
        return (SIZED, found) if found[0] > 0 and found[1] > 0 else (UNREADABLE, None)
    return _NOT_READ[shares.why_not_read(path, found if state == "error" else None)], None


def _rows(db_path, limit=None):
    """(id, native path, mtime, size) of the photos with no size, by id, at most `limit`: pages of PAGE, asked as the run
    gets to them, so a row filled meanwhile by another writer is not asked for again."""
    after, left = 0, limit
    with closing(db.connect(db.readonly_uri(db_path), uri=True)) as conn:
        while left is None or left > 0:
            page = photo_rows.unsized(conn, after, PAGE if left is None else min(PAGE, left))
            if not page:
                return
            yield from page
            after = page[-1][0]
            left = None if left is None else left - len(page)


class Filled:
    """What a run did: `counts` {outcome: photos}, `wrote` the rows whose size it recorded, `read` the photos looked at,
    `stopped` whether it was asked to stop before its end, `away` the shares that did not answer (shares.share_of)."""

    def __init__(self):
        self.counts = collections.Counter({outcome: 0 for outcome in OUTCOMES})
        self.wrote = self.read = 0
        self.stopped = False
        self.away = []


def fill(db_path, limit=None, stop=never, heard=None):
    """Fill the size of the photos of the library at `db_path` with none, at most `limit` looked at, a CHUNK at a time:
    `stop()` is asked before each photo, and a stop writes the chunk read so far; `heard(filled)` after each chunk
    written. A Filled."""
    filled, sized = Filled(), []
    walk = shares.Walk(patient=True, stop=stop)

    def write():
        if sized:
            filled.wrote += db.write_with_connection(
                db_path, lambda conn: photo_rows.record_picture_sizes(conn, sized), label="picture sizes")
            sized.clear()
            if heard is not None:
                heard(filled)

    for photo_id, path, mtime, size in _rows(db_path, limit):
        if stop():
            filled.stopped = True
            break
        outcome, found = look(walk, path, mtime, size)
        filled.counts[outcome] += 1
        filled.read += 1
        if found:
            sized.append((found[0], found[1], photo_id, mtime, size))
        if len(sized) >= CHUNK:
            write()
    write()
    filled.away = list(walk.away)
    return filled


def dry_run(db_path, sample=SAMPLE, limit=None):
    """What `fill` would do, writing nothing: {"counts": the rows' (picture_size_counts), "sample": {outcome: photos}
    of `sample` photos with no size drawn and read (nothing written), "ms": the read's time a photo (median, p90, mean,
    of the photos whose share answered), "estimate_s": the mean times the photos to fill (or `limit`)}."""
    with closing(db.connect(db.readonly_uri(db_path), uri=True)) as conn:
        counts = photo_rows.picture_size_counts(conn)
    drawn = segment_census.drawn(list(_rows(db_path)), sample, SEED)
    walk, outcomes, times = shares.Walk(patient=True), collections.Counter({outcome: 0 for outcome in OUTCOMES}), []
    for _photo_id, path, mtime, size in drawn:
        began = time.perf_counter()
        outcome, _found = look(walk, path, mtime, size)
        outcomes[outcome] += 1
        if outcome != AWAY:
            times.append((time.perf_counter() - began) * 1000)
    to_fill = counts["to fill"] if limit is None else min(limit, counts["to fill"])
    mean = statistics.fmean(times) if times else None
    ordered = sorted(times)
    return {"counts": counts, "sample": dict(outcomes), "away": list(walk.away),
            "ms": {"timed": len(times), "median": round(statistics.median(ordered), 1),
                   "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1),
                   "mean": round(mean, 1)} if times else {"timed": 0},
            "estimate_s": None if mean is None else round(to_fill * mean / 1000)}

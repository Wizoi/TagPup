"""The thumbnail cache: a photo's small picture, made once and kept (docs/ARCHITECTURE.md, phase 9a-2).

Browsing the whole library shows thousands of photos at once, and a thumbnail made on every request
(`photos.page_copy`, still there for the folder view) decoded each photo's file again every time. Here
a photo's thumbnail is a file in `<library folder>/cache/<library>/thumbs` (`Library.thumbs`), made on
the first ask and served from then on. DERIVED: the owner decided it is not bounded (about 20-40 KB
a photo, 1.5-3 GB for photo_index), and nothing in it is the only copy of anything.

* **What an entry is keyed by** (tagpup.files.thumbs): the photo's id, a short hash of the path its ROW
  holds -- so a photo moved to another root place (the map changed, no row did) keeps its entries, and an id
  handed out again after a restore never serves another photo's picture -- and the stamp (modified time and
  size) of the file it was made from, read from the file at each ask. A file changed is a new entry and the
  old one is deleted as it is made; a photo renamed likewise.
* **Made on first ask**, by the request that asked, or ahead of time by `warm` (the CLI's `thumbs warm`).
  Two requests for one photo at once make it once: one waits for the other's lock and finds the entry.
  Another process making it too -- warm while the server runs -- is harmless: each writes the whole
  entry to a temporary file and renames it over the name.
* **Never by a schedule.** An entry goes when the code that takes its photo away says so (`forget`, from
  the services that delete a photo or remove a folder: event-driven, as everything here is), and `sweep`,
  called by what removes photos in bulk, takes any that remain for a photo that is no longer there or is
  somewhere else.
* **A damaged photo** (damaged_files: its record, while the file has the stamp it was found with) is
  answered a placeholder without its file being decoded, and nothing is kept for it. One that does not decode
  though no record says so is answered the same, and remembered in this process for as long as its
  stamp does not change, so a damaged file is not decoded again for each of the thousand cards that
  ask. Nothing is written to the library for it: recording it is the indexer's finding, and a false one
  would refuse writes to a good photo.
* **A cache that cannot be written** (folder missing and not makeable, read-only, disk full) costs
  only the cache: the thumbnail is made and answered, not kept, and the first time is logged. A file that cannot
  be read is `Unavailable`, a sentence, never a traceback and never a placeholder that looks like a verdict.
"""
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

from tagpup.core import paths
from tagpup.core.result import NotFound
from tagpup.files import images, thumbs
from tagpup.services import damaged_photos
from tagpup.store import damaged_files, db
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: What a thumbnail's entry is, for the page to read.
OK, DAMAGED, UNREADABLE, LAST_KNOWN = "ok", "damaged", "unreadable", "last known"

#: What a thumbnail of a photo is when it is the cache's own.
KINDS = (OK, DAMAGED, UNREADABLE, LAST_KNOWN)

#: What warming says of a thumbnail it made and could not keep.
UNWRITABLE = "unwritable"

#: What a thumbnail an average entry weighs when nothing is cached yet to say, in bytes: the owner's estimate.
ASSUMED_BYTES = 30_000

#: Photos `warm` reads from the library at once, and decodes at once (threads: Pillow decodes outside the GIL).
BATCH = 500
WORKERS = 4


class Unavailable(RuntimeError):
    """The thumbnail cannot be answered just now, and why, as a sentence: the file is not reachable."""


class Thumb:
    """One answer: `content` (JPEG bytes), `kind` (one of KINDS), `etag` (names this picture: None for a
    placeholder), `mtime` (the file's, None when it is not there) and `kept` -- whether it is in the cache."""

    def __init__(self, content, kind, etag=None, mtime=None, kept=False):
        self.content, self.kind, self.etag, self.mtime, self.kept = content, kind, etag, mtime, kept


# ---- Where a photo's entry is ---------------------------------------------------------------

def _hash_of(row_path):
    """The hash of a row's path, as the cache keys it: without case where the filesystem has none."""
    return thumbs.short_hash(store_roots.path_order(row_path))


def url(photo_id, mtime=None):
    """The URL a page asks a photo's thumbnail at, relative to the library (the page puts the library in front
    of /api/): by id, with the stamp the browser may cache it by (`v`: the photo's modified time as the row
    holds it)."""
    return "/api/photo-thumb?id=%d" % photo_id + ("&v=%s" % repr(float(mtime)) if mtime is not None else "")


def _look(library):
    return db.connect(db.readonly_uri(library.path), uri=True)


# ---- Making one ---------------------------------------------------------------------------------

#: Locks, one for each of a few dozen stripes of the entries: two requests for one photo wait for one another
#: and the second finds what the first made.
_locks = [threading.Lock() for _ in range(64)]

#: {(photo id, mtime ms, size): the kind of placeholder} of photos found not to decode, as the process
#: has them: bounded, so a library of broken files does not grow it without end.
_undecodable = {}
_undecodable_guard = threading.Lock()
UNDECODABLE_MOST = 5000

#: The cache folders this process has said it could not write.
_warned = set()


def _remember_undecodable(key, kind):
    with _undecodable_guard:
        if len(_undecodable) >= UNDECODABLE_MOST:
            _undecodable.clear()
        _undecodable[key] = kind


def _warn_once(root, why):
    if root not in _warned:
        _warned.add(root)
        logger.warning("The thumbnail cache %s cannot be written (%s): thumbnails are made for each request until it can.",
                       root, why)


def _make(root, photo_id, row_path, path, stamp):
    """The Thumb of the photo at `path` whose file has `stamp` (mtime, size): from the cache, else made now and
    kept. Raises Unavailable when the file cannot be read."""
    path_hash = _hash_of(row_path)
    mtime, size = stamp
    etag = thumbs.etag_of(photo_id, path_hash, mtime, size)
    found = thumbs.read(root, photo_id, path_hash, mtime, size)
    if found is not None:
        return Thumb(found, OK, etag, mtime, kept=True)
    key = (photo_id, thumbs.stamp_key(mtime), size)
    with _undecodable_guard:
        known = _undecodable.get(key)
    if known is not None:
        return Thumb(thumbs.placeholder(), known)
    with _locks[photo_id % len(_locks)]:
        found = thumbs.read(root, photo_id, path_hash, mtime, size)
        if found is not None:
            return Thumb(found, OK, etag, mtime, kept=True)
        try:
            made = thumbs.render(path)
        except images.Unreadable:
            _remember_undecodable(key, DAMAGED)
            return Thumb(thumbs.placeholder(), DAMAGED)
        except OSError as why:
            raise Unavailable("The photo's file could not be read just now (%s): try again in a moment." % (
                why.strerror or type(why).__name__)) from why
        except Exception as why:   # a picture Pillow gives up on in some way that is not damage: no verdict
            logger.warning("Could not make a thumbnail of photo %d: %s", photo_id, why)
            _remember_undecodable(key, UNREADABLE)
            return Thumb(thumbs.placeholder(), UNREADABLE)
        # The file changed while it was decoded: what was made is a picture of neither state, and is not kept.
        again = damaged_photos.stamp_of(path)
        kept = False
        if again == stamp:
            try:
                thumbs.write(root, photo_id, path_hash, mtime, size, made)
                kept = True
            except OSError as why:
                _warn_once(root, why.strerror or type(why).__name__)
        return Thumb(made, OK, etag if kept else None, mtime, kept=kept)


def _file_stamp(path):
    """The file's (mtime, size), or None when it is not there; raises Unavailable for a share that did not
    answer."""
    stamp = damaged_photos.stamp_of(path)
    if stamp is damaged_photos.UNANSWERED:
        raise Unavailable("The folder this photo is in did not answer just now (a network share that is away?): "
                          "try again in a moment.")
    return stamp


def serve(library, photo_id):
    """The Thumb of photo `photo_id` of `library`. Raises NotFound -- with its sentence -- for an id the
    library has no photo of, or one whose file is not there and was never shown; Unavailable when the file cannot
    be reached; paths.RootsError for a photo under a root this machine does not place."""
    conn = _look(library)
    try:
        row = store_photos.thumb_row(conn, photo_id)
    finally:
        conn.close()
    if row is None:
        raise NotFound("There is no photo %d in this library." % photo_id)
    root = library.thumbs
    stamp = _file_stamp(row.path)
    if stamp is None:
        last = thumbs.newest(root, photo_id, _hash_of(row.row_path))
        if last is not None:
            return Thumb(last, LAST_KNOWN)
        raise NotFound("The file of photo %d (%s) is not there, and no thumbnail of it was kept." % (
            photo_id, os.path.basename(row.path)))
    if row.damaged is not None and row.damaged.kind != damaged_files.INCOMPLETE \
            and damaged_photos.describes((row.damaged.mtime, row.damaged.size), stamp):
        return Thumb(thumbs.placeholder(), DAMAGED)
    return _make(root, photo_id, row.row_path, row.path, stamp)


# ---- Taking one away -----------------------------------------------------------------------------

def forget(library, photo_ids):
    """Delete the entries of the photos `photo_ids`, which have left the library: called by the services that
    take a photo away, with the ids read before the rows went. Never raises: a cache that cannot be cleared
    holds files nothing will serve (the id's rows are gone), which `sweep` takes. Returns the entries deleted."""
    removed = 0
    for photo_id in photo_ids:
        try:
            removed += thumbs.remove(library.thumbs, photo_id)[0]
        except OSError as why:
            logger.warning("Could not delete the thumbnail of photo %d: %s", photo_id, why)
    return removed


def ids_of(library, photo_paths=(), folder=None):
    """The ids of the photos at `photo_paths` -- or under `folder` -- as the library holds them now: read before
    a delete, to say to `forget` afterwards. A library that cannot be read has none to name."""
    try:
        conn = _look(library)
    except Exception:
        return []
    try:
        found = list(store_photos.ids_at(conn, photo_paths)) if photo_paths else []
        if folder:
            found += store_photos.ids_under(conn, folder)
        return found
    except Exception as why:
        logger.warning("Could not find the photos whose thumbnails go: %s", why)
        return []
    finally:
        conn.close()


def sweep(library):
    """Delete every entry of a photo the library does not hold, or holds at another path, and the
    temporary files a crashed writer left: what is called after photos are removed in bulk. One read
    of every photo's id and path. Returns {"removed", "bytes", "temporaries"}; zeros for a cache that is
    not there."""
    root = library.thumbs
    if not os.path.isdir(root):
        return {"removed": 0, "bytes": 0, "temporaries": 0}
    conn = _look(library)
    try:
        held = store_photos.row_paths(conn)
    finally:
        conn.close()
    hashes = {}
    removed = size = 0
    for photo_id, path_hash, _mtime, _size, entry, length in list(thumbs.entries(root)):
        row_path = held.get(photo_id)
        if row_path is not None:
            if photo_id not in hashes:
                hashes[photo_id] = _hash_of(row_path)
            if hashes[photo_id] == path_hash:
                continue
        try:
            os.remove(entry)
            removed, size = removed + 1, size + length
        except OSError:
            continue
    return {"removed": removed, "bytes": size, "temporaries": thumbs.sweep_temporaries(root)}


def clear(library):
    """Delete the whole cache of `library`: it has been deleted, or made again from nothing. Returns whether
    a cache was there."""
    root = library.thumbs
    if not os.path.isdir(root):
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True


# ---- Filling it ahead of time ------------------------------------------------------------------

def warm(library, folder=None, limit=None, apply=False, progress=None, workers=WORKERS):
    """Make the thumbnails of the library's photos -- those under `folder`, at any depth, or all -- that have none for
    their file as it is now: a dry run unless `apply`, which only counts. `limit` stops after that many are
    made (a run can be repeated: what is there is found there, and the walk goes on from it). Reads the library
    and the files, writes only the cache.

    Returns {"photos": looked at, "present": already cached, "to_make": without one, "made", "damaged": not
    decoded (recorded damaged, or found not to), "missing": file not there, "failed": file not reachable,
    "unwritable": made and not kept (the cache cannot be written), "bytes_cached": what the cache holds before, "bytes_made", "estimate": bytes the missing ones would take
    (from the average entry, ASSUMED_BYTES when none is cached), "stopped": the limit was reached}. `progress`
    is called with the counts so far after each batch."""
    root = library.thumbs
    held, bytes_cached = thumbs.usage(root)
    average = bytes_cached // held if held else ASSUMED_BYTES
    counts = {"photos": 0, "present": 0, "to_make": 0, "made": 0, "damaged": 0, "missing": 0, "failed": 0,
              "unwritable": 0, "bytes_cached": bytes_cached, "bytes_made": 0, "estimate": 0, "stopped": False, "average": average}
    conn = _look(library)
    try:
        recorded = {paths.key(each.path): each for each in damaged_files.every(conn)}
        after = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while not counts["stopped"]:
                batch = store_photos.thumb_rows(conn, folder, after, BATCH)
                if not batch:
                    break
                after = batch[-1][0]
                jobs = []
                for photo_id, row_path, path in batch:
                    counts["photos"] += 1
                    stamp = damaged_photos.stamp_of(path)
                    if stamp is damaged_photos.UNANSWERED:
                        counts["failed"] += 1
                        continue
                    if stamp is None:
                        counts["missing"] += 1
                        continue
                    record = recorded.get(paths.key(path))
                    if record is not None and record.kind != damaged_files.INCOMPLETE \
                            and damaged_photos.describes((record.mtime, record.size), stamp):
                        counts["damaged"] += 1
                        continue
                    path_hash = _hash_of(row_path)
                    if os.path.isfile(thumbs.entry_path(root, photo_id, path_hash, *stamp)):
                        counts["present"] += 1
                        continue
                    counts["to_make"] += 1
                    counts["estimate"] += average
                    if apply and (limit is None or counts["made"] + len(jobs) < limit):
                        jobs.append((photo_id, row_path, path, stamp))
                for result in pool.map(lambda job: _warm_one(root, *job), jobs):
                    if result is None:
                        counts["failed"] += 1
                    elif result == UNWRITABLE:
                        counts["unwritable"] += 1
                    elif result in (DAMAGED, UNREADABLE):
                        counts["damaged"] += 1
                        counts["to_make"] -= 1
                    else:
                        counts["made"] += 1
                        counts["bytes_made"] += result
                if limit is not None and counts["made"] >= limit:
                    counts["stopped"] = True
                if progress is not None:
                    progress(dict(counts))
    finally:
        conn.close()
    if apply:
        thumbs.sweep_temporaries(root)
    return counts


def _warm_one(root, photo_id, row_path, path, stamp):
    """Make one entry for `warm`: its size in bytes, or DAMAGED / UNREADABLE for a picture that does not decode,
    or None when the file could not be read."""
    try:
        made = _make(root, photo_id, row_path, path, stamp)
    except Unavailable:
        return None
    if made.kind != OK:
        return made.kind
    return len(made.content) if made.kept else UNWRITABLE


def stats(library):
    """(entries, bytes) the library's cache holds now."""
    return thumbs.usage(library.thumbs)


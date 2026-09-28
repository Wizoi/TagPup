"""The photos a library's indexer found damaged, remembered so that nothing reads them again
for nothing, and listed so the owner can restore them (docs/findings.md, #407).

The indexer records a photo whose picture does not decode, and one that decodes but may be
an incomplete copy (tagpup.store.damaged_files), each with the stamp its file had when it
was read. Sync does not queue a photo that does not decode again while it keeps that
stamp (tagpup.services.sync), and the indexer passes it over. A record is shown only
while its file still has the stamp it was found with: a file replaced or changed has left
the list at once, and is read again -- a good copy is indexed, and its record forgotten;
a damaged one is found again. A record whose file is gone from a folder that is still
there is forgotten by the next sync; one in a folder that is not there (a drive
unplugged, a share offline) is kept, and not shown.

Found once, a photo is logged at WARNING once: found again with the same stamp, it is not.

The stamp alone cannot say a damaged file was replaced: a copy that keeps the modified
time (robocopy, Explorer) over a zero-filled file of the same size leaves the stamp as it
was. So a recorded photo is read again, whatever its stamp, when the folder watcher is
told its file was written (tagpup.jobs.watching), and when the owner asks, Check again
(check_again): read whole, it is forgotten and indexed again.

A record forgotten because its file changed, or reads whole now, takes with it what was
made of the damaged file (forget_to_reindex): a photo that may have been an incomplete
copy was indexed from a picture grey below a line, so its vectors go, and its faces --
unless one carries a decision (a name, a "nobody", an exclusion), when they are kept and
not detected again, and it is said so -- and its folder is to be indexed again. No path
re-detects a photo's faces keeping the decided ones by where they are.
"""
import logging
import os
import threading
import time

from tagpup.core import paths
from tagpup.files import images
from tagpup.store import damaged_files, db
from tagpup.store import embeddings as store_embeddings
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos

logger = logging.getLogger(__name__)

#: The kind of a photo that decodes and may be an incomplete copy (store.damaged_files).
INCOMPLETE = damaged_files.INCOMPLETE

#: What the owner is told of a photo that may be an incomplete copy.
INCOMPLETE_REASON = ("possibly an incomplete copy: the file ends in zero bytes, as an interrupted copy leaves it, "
                     "and the picture may be grey below a line")


def reason(kind):
    """What the owner is told of a photo recorded as `kind`."""
    return INCOMPLETE_REASON if kind == INCOMPLETE else images.DAMAGE.get(kind, kind)


#: How long a request waits to hear from a folder on a network share (a UNC path),
#: in seconds, and how long a share that did not answer in time is taken as not there.
SHARE_WAIT = 1.0
SHARE_AWAY = 30.0

#: {folder key: when it did not answer in time (time.monotonic)}: a share away.
_away = {}
_away_lock = threading.Lock()


def _on_a_share(path):
    return str(path).startswith(("\\\\", "//"))


def _stamps_in(folder):
    """{paths.key(file): (mtime, size)} of the files directly in `folder`, one listing of it
    (the stamps come with the listing on Windows), or None when it is not there."""
    try:
        with os.scandir(folder) as entries:
            found = {}
            for entry in entries:
                try:
                    if entry.is_file():
                        stat = entry.stat()
                        found[paths.key(entry.path)] = (stat.st_mtime, stat.st_size)
                except OSError:
                    continue
            return found
    except OSError:
        return None


def _folder_stamps(folder):
    """_stamps_in(folder) -- within SHARE_WAIT for a folder on a network share, which is
    taken as not there (None) for SHARE_AWAY when it did not answer in time: a page's
    request never waits on a share gone away."""
    if not _on_a_share(folder):
        return _stamps_in(folder)
    key = paths.key(folder)
    with _away_lock:
        since = _away.get(key)
    if since is not None and time.monotonic() - since < SHARE_AWAY:
        return None
    answer = {}
    reader = threading.Thread(target=lambda: answer.update(found=_stamps_in(folder)),
                              name="DamagedPhotosShareRead", daemon=True)
    reader.start()
    reader.join(SHARE_WAIT)
    if "found" not in answer:
        logger.info("%s did not answer within %s s; its damaged photos are not shown for %s s.",
                    folder, SHARE_WAIT, SHARE_AWAY)
        with _away_lock:
            _away[key] = time.monotonic()
        return None
    with _away_lock:
        _away.pop(key, None)
    return answer["found"]


def _current(found):
    """The records of `found` whose file still has the stamp it was found with: each
    record's folder listed once -- a folder that is not there, or a share that does not
    answer in time, passes over its records without looking at each."""
    by_folder = {}
    for each in found:
        by_folder.setdefault(paths.key(os.path.dirname(each.path)), []).append(each)
    current = []
    for held in by_folder.values():
        stamps = _folder_stamps(os.path.dirname(held[0].path))
        if stamps is None:
            continue
        current += [each for each in held
                    if describes((each.mtime, each.size), stamps.get(paths.key(each.path)))]
    return sorted(current, key=lambda each: paths.key(each.path))


def _look(library):
    return db.connect(db.readonly_uri(library.path), uri=True)


def _stamp(photo_path):
    """(mtime, size) of the file now, or None when it cannot be read."""
    try:
        stat = os.stat(photo_path)
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def describes(record, stamp):
    """Does `record` (mtime, size), as found, describe a file whose stamp is `stamp`? As a
    row describes its file (store.photos.describes): the size, and the time within the
    folder scan's tolerance. A walk and a stat need not spell a time alike to the last bit."""
    return store_photos.describes(record[0], record[1], stamp)


def records(library):
    """[store.damaged_files.Record] of everything recorded, current or not. [] for a library
    behind this version's schema."""
    conn = _look(library)
    try:
        return damaged_files.every(conn)
    finally:
        conn.close()


def unreadable(records_found):
    """{paths.key: (mtime, size)} of the records of photos that do not decode: what sync
    and the indexer pass over while a file keeps its stamp."""
    return {paths.key(each.path): (each.mtime, each.size) for each in records_found if each.kind != INCOMPLETE}


def remember(library, found, run=None):
    """Record what the indexer found: `found`, [(path, stamp, kind, detail, zero_tail)], each
    stamp the file's (mtime, size) as it was read. Returns the paths found for the first
    time (or with a new stamp or kind), each logged at WARNING -- once: found again as it
    was, a photo is not logged again."""
    if not found:
        return []

    def write(conn):
        return [path for path, stamp, kind, detail, zeros in found
                if damaged_files.record(conn, path, stamp, kind, detail, zeros, run)]

    new = db.write_with_connection(library.path, write, label="damaged photos found")
    for path, _stamp_found, kind, detail, _zeros in found:
        if path in new:
            logger.warning("%s: %s -- %s (%s)", "Possibly an incomplete copy" if kind == INCOMPLETE
                           else "A photo that cannot be read", path, reason(kind), detail)
    return new


def forget(library, photo_paths):
    """Forget the records of `photo_paths`: each has been read whole since. Returns records
    removed."""
    if not photo_paths:
        return 0
    return db.write_with_connection(library.path, lambda conn: damaged_files.forget(conn, photo_paths),
                                    label="damaged photos read whole")


def forget_to_reindex(library, found):
    """Forget each of `found` ([Record], each as it was read: a record made again meanwhile
    is kept) -- its file changed since, or reads whole now -- and take from the photo what
    was made of the damaged file: its vectors, and its faces unless one carries a decision.
    Returns {"forgotten", "vectors", "faces", "kept": [paths whose decided faces were kept],
    "folders": [the folders of the photos forgotten, to be indexed again]}."""
    there = [each for each in found if _stamp(each.path) is not None]
    gone = [each for each in found if each not in there]

    def write(conn):
        done = {"forgotten": damaged_files.forget_as_found(conn, gone), "vectors": 0, "faces": 0,
                "kept": [], "folders": []}
        for each in there:
            if not damaged_files.forget_one_as_found(conn, each):
                continue
            done["forgotten"] += 1
            done["folders"].append(os.path.dirname(each.path))
            done["vectors"] += store_embeddings.forget(conn, each.path)
            if not store_faces.count_for_photo(conn, each.path):
                continue
            if store_faces.decided_for_photo(conn, each.path):
                done["kept"].append(each.path)
            else:
                done["faces"] += store_faces.remove_for_photo(conn, each.path)
        return done

    if not found:
        return {"forgotten": 0, "vectors": 0, "faces": 0, "kept": [], "folders": []}
    done = db.write_with_connection(library.path, write, label="damaged photos to index again")
    for path in done["kept"]:
        logger.info("%s is indexed again, and its faces are kept, not detected again: one carries a name or "
                    "a decision. They were found in the damaged copy.", path)
    folders = {}
    for folder in done["folders"]:
        folders.setdefault(paths.key(folder), folder)
    done["folders"] = sorted(folders.values(), key=paths.key)
    return done


def check_again(library, photo_paths=None):
    """Read the photos recorded damaged -- those of `photo_paths` that are, or every one --
    again now, decoding each whole picture whatever its stamp says. One that reads whole
    is forgotten and indexed again (forget_to_reindex); one that does not stays recorded,
    with its file's stamp now; one that cannot be reached is left as it is. Returns
    {"checked", "whole": [paths], "still": [paths], "unreachable", "folders": [the
    folders to index], "kept": [paths whose decided faces were kept]}."""
    found = records(library)
    if photo_paths is not None:
        wanted = {paths.key(path) for path in photo_paths}
        found = [each for each in found if paths.key(each.path) in wanted]
    whole, still, again, unreachable = [], [], [], 0
    for each in found:
        stamp = _stamp(each.path)
        if stamp is None:
            unreachable += 1
            continue
        try:
            zeros = images.opened(each.path, upright=False).info.get(images.ZERO_TAIL_INFO, 0)
        except images.Unreadable as damage:
            still.append(each.path)
            if (damage.kind, stamp) != (each.kind, (each.mtime, each.size)):
                again.append((each.path, stamp, damage.kind, damage.detail, damage.zero_tail))
            continue
        except OSError:
            unreachable += 1
            continue
        if zeros >= images.ZERO_TAIL and each.kind == INCOMPLETE:
            still.append(each.path)
            if stamp != (each.mtime, each.size):
                again.append((each.path, stamp, INCOMPLETE, "the last %d bytes are zeros" % zeros, zeros))
            continue
        # Whole -- or, recorded as not decoding, decoding now: the indexer flags it again
        # if it may be an incomplete copy.
        whole.append(each)
    remember(library, again)
    done = forget_to_reindex(library, whole)
    return {"checked": len(found), "whole": [each.path for each in whole], "still": still,
            "unreachable": unreachable, "folders": done["folders"], "kept": done["kept"]}


def checked_counts(done):
    """What Check again answers of check_again's answer (with the runtime's `queued`):
    counts, never a path."""
    return {"checked": done["checked"], "whole": len(done["whole"]), "still": len(done["still"]),
            "unreachable": done["unreachable"], "queued": done.get("queued", 0), "kept_faces": len(done["kept"])}


def _entry(each):
    """A record as the pages show it, with `reason`."""
    return {"path": each.path, "name": os.path.basename(each.path), "folder": os.path.dirname(each.path),
            "kind": each.kind, "reason": reason(each.kind), "detail": each.detail, "zero_tail": each.zero_tail,
            "indexed": each.kind == INCOMPLETE, "size": each.size, "mtime": each.mtime,
            "found": each.found, "seen": each.seen, "run": each.run}


def listed(library, folder=None):
    """The damaged photos of `library` -- under `folder`, at any depth, when given -- whose
    file still has the stamp it was found with, as the pages show them: each {"path",
    "name", "folder", "kind", "reason", "detail", "zero_tail", "indexed", "size", "mtime",
    "found", "seen", "run"}, by path. One listing of each folder holding one (_current)."""
    conn = _look(library)
    try:
        found = damaged_files.under(conn, folder) if folder else damaged_files.every(conn)
    finally:
        conn.close()
    return [_entry(each) for each in _current(found)]


def among(library, photo_paths):
    """The photos of `photo_paths` recorded damaged or possibly incomplete whose files still
    have the stamp they were found with, as listed() gives each. One read of the records
    (a handful), and a listing of the folder of each that is asked about."""
    wanted = {paths.key(path) for path in photo_paths}
    if not wanted:
        return []
    return [_entry(each) for each in _current([each for each in records(library) if paths.key(each.path) in wanted])]


def counts(library):
    """{"unreadable", "incomplete"}: how many photos listed do not decode, and how many
    may be incomplete copies."""
    shown = listed(library)
    incomplete = sum(1 for each in shown if each["kind"] == INCOMPLETE)
    return {"unreadable": len(shown) - incomplete, "incomplete": incomplete}


def prune(library):
    """Forget the records that no longer describe a file: one changed since it was found,
    or gone from a folder that is still there. A file in a folder that is not there -- a
    drive unplugged, a share offline -- keeps its record. A changed file's photo is to be
    indexed again for real (forget_to_reindex). Returns forget_to_reindex's answer."""
    stale = []
    for each in records(library):
        stamp = _stamp(each.path)
        if stamp is None:
            if os.path.isdir(os.path.dirname(each.path)):
                stale.append(each)
        elif not describes((each.mtime, each.size), stamp):
            stale.append(each)
    return forget_to_reindex(library, stale)

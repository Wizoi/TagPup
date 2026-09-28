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
"""
import logging
import os

from tagpup.core import paths
from tagpup.files import images
from tagpup.store import damaged_files, db
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
    "found", "seen", "run"}, by path. A stat of each file recorded: a handful."""
    conn = _look(library)
    try:
        found = damaged_files.under(conn, folder) if folder else damaged_files.every(conn)
    finally:
        conn.close()
    return [_entry(each) for each in found if describes((each.mtime, each.size), _stamp(each.path))]


def counts(library):
    """{"unreadable", "incomplete"}: how many photos listed do not decode, and how many
    may be incomplete copies."""
    shown = listed(library)
    incomplete = sum(1 for each in shown if each["kind"] == INCOMPLETE)
    return {"unreadable": len(shown) - incomplete, "incomplete": incomplete}


def prune(library):
    """Forget the records that no longer describe a file: one changed since it was found,
    or gone from a folder that is still there. A file in a folder that is not there -- a
    drive unplugged, a share offline -- keeps its record. Returns records removed."""
    stale = []
    for each in records(library):
        stamp = _stamp(each.path)
        if stamp is None:
            if os.path.isdir(os.path.dirname(each.path)):
                stale.append(each)
        elif not describes((each.mtime, each.size), stamp):
            stale.append(each)
    if not stale:
        return 0
    return db.write_with_connection(library.path, lambda conn: damaged_files.forget_as_found(conn, stale),
                                    label="damaged photos changed or gone")

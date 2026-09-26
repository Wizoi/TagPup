"""Keeping a library in step with its folders (docs/ARCHITECTURE.md, phase 8).

A row changes only when an app writes the photo or someone indexes its folder again, so
a library drifts: files added outside the apps are missing from it, a file edited
elsewhere keeps a row describing what it used to hold, and a file moved or deleted
leaves a row naming nothing. `sync` walks each indexed folder once and compares what is
on disk with the rows, by path, size and modified time (store.photos.describes, the
folder scan's rule), and sorts what differs:

- new files -- on disk, no row: their folders are queued for indexing, through the
  queue the caller hands over (tagpup.jobs.indexing, which runs the CLI's `index`), not
  indexed here;
- changed files -- a row whose stamp no longer describes its file: the row re-read from
  the file, as refresh_rows does (refresh_rows.reread, edits_for);
- moved files -- a row whose file is gone, and a new file holding its DocumentID (or,
  renamed in its folder by TagPup, its PreservedFileName): the row follows the file, and
  its faces with it, as relink does (relink_photos.claims_of, pair, edits_for);
- missing files -- a row whose file is gone and was not found elsewhere: reported, never
  removed. A folder on an unplugged drive looks the same as a deleted one, so removing
  rows stays the owner's choice (TagTuner's Remove Folder), and the report says which
  folders are wholly gone.

Which folders: every folder the library holds photos in, walked from the topmost of them
(a folder under another is walked with it, as indexing walked it), or the one folder
asked for. A scan that finds nothing costs one walk and no file reads
(tagpup.files.images.stamps_under): ExifTool is started only for a changed file, or to
read the new files' identities when a row is missing too.

On the maintenance scaffold (tagpup.services.maintenance): a dry run by default, which
reads and rehearses and writes nothing -- neither a row nor a photo file (the files are
only ever read: no DocumentID is minted); applied, the re-read rows and the moved rows are
one change of the journal, `sync`, undoable. Then the new files' folders are queued, and
the run is recorded in the library (tagpup.store.sync_runs): what it found and what it
changed, and whether it left the library in step -- nothing new, changed or moved left
over; missing files do not count against it -- which is the pages' "last in step".
"""
import os

from tagpup.core import paths
from tagpup.files import images
from tagpup.services import maintenance, refresh_rows, relink_photos
from tagpup.store import db, sync_runs
from tagpup.store import photos as store_photos

#: What the change is recorded as.
OPERATION = "sync"

#: The kinds of row the change writes: re-read from its file, or following its file.
KINDS = ("from_files", "relinked")


def walk_roots(folders):
    """The folders to walk to see every one of `folders`: each not under another of them,
    in the spelling first given, sorted by key."""
    spelled = {}
    for folder in folders:
        spelled.setdefault(paths.key(folder), paths.stored(folder))
    roots = []
    for key, folder in sorted(spelled.items()):
        parent, current = os.path.dirname(folder), folder
        while parent != current:
            if paths.key(parent) in spelled:
                break
            parent, current = os.path.dirname(parent), parent
        else:
            roots.append(folder)
    return roots


def _missing_by_folder(missing):
    """[(folder, rows, whether it is gone)] of the missing rows' folders, the most first."""
    by_folder = {}
    for _photo_id, path in missing:
        folder = os.path.dirname(path)
        by_folder.setdefault(paths.key(folder), [folder, 0])[1] += 1
    listed = [(folder, count, not os.path.isdir(folder)) for folder, count in by_folder.values()]
    return sorted(listed, key=lambda entry: (-entry[1], paths.key(entry[0])))


def look(library, folder=None, exiftool_path=None):
    """What differs between the library and its folders (or `folder`): a Plan (maintenance)
    whose `work` holds the edits and the folders of new files. Reads the rows, walks the
    folders, and reads with ExifTool only the changed files and, where a row is missing,
    the new files' identities. Writes nothing."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = store_photos.stamps(conn, folder)
        by_key = {}
        for photo_id, path, mtime, size in rows:
            by_key.setdefault(paths.key(path), (photo_id, path, mtime, size))
        roots = [paths.stored(folder)] if folder else walk_roots(
            {os.path.dirname(path) for _id, path, _m, _s in by_key.values()})
        on_disk, roots_gone = {}, []
        for root in roots:
            if os.path.isdir(root):
                on_disk.update(images.stamps_under(root))
            else:
                roots_gone.append(root)

        changed, missing, never_stamped = {}, [], 0
        for key, (photo_id, path, mtime, size) in by_key.items():
            stamp = on_disk.get(key)
            if stamp is None:
                missing.append((photo_id, path))
            elif not store_photos.describes(mtime, size, stamp[1:]):
                changed[path] = photo_id
                never_stamped += mtime is None or size is None
        new = {key: stamp for key, stamp in on_disk.items() if key not in by_key}

        # Moved: a missing row whose file turns up among the new ones. Only then are the
        # new files read, and only for their identities.
        moves = []
        if missing and new:
            lookup, by_identity = relink_photos.claims_of(sorted(path for path, _m, _s in new.values()),
                                                          exiftool_path)
            live = set(by_key) - {paths.key(path) for _id, path in missing}
            pairs, _unmatched = relink_photos.pair([path for _id, path in missing], lookup, by_identity,
                                                   store_photos.identities(conn), live)
            moves = relink_photos.moves_with_faces(conn, pairs)
        moved_from = {paths.key(move["from"]) for move in moves}
        moved_to = {paths.key(move["to"]) for move in moves}
        missing = [(photo_id, path) for photo_id, path in missing if paths.key(path) not in moved_from]
        # A moved file whose stamp is not its row's is read again by the next sync, once
        # the row names it.
        moved_changed = sum(1 for move in moves
                            if not store_photos.describes(by_key[paths.key(move["from"])][2],
                                                          by_key[paths.key(move["from"])][3],
                                                          on_disk[paths.key(move["to"])][1:]))
        new = {key: stamp for key, stamp in new.items() if key not in moved_to}

        # Changed: re-read as the refresh re-reads, each row held to what it was when read.
        found = {}
        for path in changed:
            tags, _people, captions, raw, mtime, size, _doc = store_photos.row_as_recorded(conn, path)
            found[path] = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
        records, to_write, fields, unreadable, _shown, identities = refresh_rows.reread(
            conn, {path: ["mtime/size"] for path in changed}, exiftool_path)
    finally:
        conn.close()

    edits = refresh_rows.edits_for(records, to_write, {}, found, identities, changed)
    moved_edits, occupied = relink_photos.edits_for(library, moves)
    by_folder = _missing_by_folder(missing)
    new_paths = sorted((path for path, _m, _s in new.values()), key=paths.key)
    # Indexing a folder takes its subfolders: each is queued once, under the outermost.
    new_folders = walk_roots({os.path.dirname(path) for path in new_paths})
    return maintenance.Plan(
        size=len(edits) + len(moved_edits),
        counts={"rows": len(by_key), "files": len(on_disk), "folders_walked": len(roots) - len(roots_gone),
                "new": len(new_paths), "new_folders": len(new_folders),
                "changed": len(changed), "never_stamped": never_stamped, "to_write": len(to_write),
                "fields": dict(fields.most_common()), "unreadable": len(unreadable),
                "moved": len(moves), "moved_faces": sum(m["faces"] for m in moves),
                "moved_named": sum(m["named"] for m in moves), "moved_changed": moved_changed,
                "occupied": len(occupied), "missing": len(missing), "missing_folders": len(by_folder),
                "folders_gone": sum(1 for _f, _n, gone in by_folder if gone), "roots_gone": len(roots_gone)},
        ids={"changed": sorted(changed.values()), "to_write": sorted(changed[p] for p in to_write),
             "unreadable": sorted(changed[p] for p in unreadable),
             "moved": sorted(by_key[paths.key(m["from"])][0] for m in moves),
             "missing": sorted(photo_id for photo_id, _path in missing)},
        reveal={"new": new_paths, "new_folders": new_folders, "moves": moves, "occupied": occupied,
                "missing_folders": [{"folder": f, "rows": n, "gone": gone} for f, n, gone in by_folder],
                "roots_gone": roots_gone},
        work={"edits": edits + moved_edits, "new_folders": new_folders})


def in_step(counts, result=None):
    """Is the library in step with its folders by what a sync found (`counts`) and, once
    applied, did (`result`)? Nothing new, nothing changed or moved left unwritten, nothing
    unreadable. Missing files do not count: they are reported, and removing their rows is
    the owner's choice."""
    if counts.get("new") or counts.get("unreadable") or counts.get("moved_changed") or counts.get("occupied"):
        return False
    if result is None:
        return not (counts.get("to_write") or counts.get("moved"))
    return result.ok and not result.skipped


def sync(library, folder=None, apply=False, exiftool_path=None, queue=None):
    """Bring `library` in step with its folders (or with `folder`), reading changed files
    with the ExifTool at `exiftool_path`; a dry run unless `apply`. A Result on the
    maintenance scaffold: `changed` is the rows the change changed (details["changed"]:
    re-read "from_files", "relinked"), read from the writes; details["counts"] what was
    found (see look), ["ids"] the photo ids, ["reveal"] the paths.

    Applied, the folders holding new files are handed to `queue(folders)`, which returns
    the index queue's Result (tagpup.jobs.indexing.IndexQueue.start): details["queued"] is
    the folders it queued. Without a queue they are reported, not queued. Then the run is
    recorded (tagpup.store.sync_runs). details["in_step"] says whether the library is in
    step: for a dry run, as found; applied, after the write.
    """
    started = sync_runs.now()
    held = {}

    def plan(found_library):
        held["plan"] = look(found_library, folder, exiftool_path)
        return held["plan"]

    result = maintenance.run(library, OPERATION, plan, lambda planned: planned.work["edits"],
                             apply=apply, kinds=KINDS)
    planned = held.get("plan")
    counts = result.details["counts"]
    if not apply:
        result.details["in_step"] = in_step(counts)
        return result

    result.details.setdefault("changed", {kind: 0 for kind in KINDS})
    new_folders = planned.work["new_folders"] if planned is not None else []
    queued = 0
    if new_folders and queue is not None and not result.refused:
        try:
            outcome = queue(new_folders)
            queued = outcome.changed
            for what, why in outcome.skipped:
                result.skip(what, why)
        except Exception as e:
            result.fail("queueing the new files' folders", "%s: %s" % (type(e).__name__, e))
    result.details["queued"] = queued
    result.details["in_step"] = in_step(counts, result)
    try:
        result.details["record"] = sync_runs.record(
            library.path, started, whole=folder is None, in_step=result.details["in_step"],
            found={what: n for what, n in counts.items() if isinstance(n, int)},
            changed={"rows": result.changed, **result.details["changed"], "queued_folders": queued},
            change_id=result.details.get("change"))
    except Exception as e:
        result.fail("recording the sync", "%s: %s" % (type(e).__name__, e))
    return result


def last(library):
    """{"last_run", "last_in_step"} of `library` (tagpup.store.sync_runs.last): counts and
    times, never a path. Reads only; a library from before migration 13 has neither."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return sync_runs.last(conn)
    finally:
        conn.close()

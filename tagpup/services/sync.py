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
- moved files -- a row whose file is gone, and a new file that is the same photo: the
  row follows the file, and its faces with it, as relink does (relink_photos.edits_for).
  Most rows hold no DocumentID, so a file is first matched by its name, size and
  modified time, which the walk and the rows already hold -- only where exactly one file
  matches the row and exactly one row the file; a match that is not one-to-one is
  reported as ambiguous, never guessed, and the folders of its files are not queued.
  What that leaves is matched by the DocumentID (or, renamed in its folder by TagPup,
  the PreservedFileName) read from those new files alone (relink_photos.claims_of, pair);
- damaged files -- a new file the indexer found does not decode, whose file still has
  the stamp it had then (tagpup.services.damaged_photos): not new, and not queued. Queued,
  it failed again, and each run loaded the photo index and CLIP for nothing -- the watcher
  and the catch-up sync queued it over and over (docs/findings.md, #407). Counted as
  `unreadable_files`, and the library is in step all the same: restoring the file is the
  owner's, and once the file changes it is new again;
- pictures changed -- a changed file whose SIZE differs from its row's: its picture may have been edited
  elsewhere, and its vector and face boxes are then the old picture's (docs/findings.md, #336). COUNTED
  (`size_changed`) and shown by the CLI, nothing more: a keyword or caption write changes a file's size too, and
  of the 1,229 size changes photo_index's journal holds every one was a metadata write. The row is re-read like
  any changed row (tags, captions, raw metadata, stamp); its vector and faces are left as they are, and the owner
  fixes a real picture edit by hand (owner, 2026-10-10: the command that took them away on a setting is gone);
- folders marked -- when a file is missing or moved, in a sync of any scope (a folder's, the watcher's, included),
  and the library has marked folders (`folder-ids mark`, tagpup.services.folder_ids), a marked folder among the
  rows' folders that is gone and found again by its marker in a folder where files were found new or moved to, or
  beside it, is followed exactly: its rows, its id and the settings and folders added that name it, as a
  journaled change of its own (`follow_folder_markers`), after the sync's own and before the new files' folders are
  queued, so the indexer never reads a followed folder's files as new. A dry run only says what it would follow;
  the report (`details["folder_markers"]`) carries its counts. A library that has marked nothing is not touched;
- missing files -- a row whose file is gone and was not found elsewhere: reported, never
  removed. A folder on an unplugged drive looks the same as a deleted one, so removing
  rows stays the owner's choice (TagTuner's Remove Folder), and the report says which
  folders are wholly gone.

Which folders: the library's root folders (its settings, tagpup.services.settings) and
every folder it holds photos in, each walked once from the topmost, or the one folder
asked for. A library with no roots set yet walks the folders it holds, and a new subfolder
of one is a folder to review as under a root, never queued unasked (#395). New files in a folder the library holds photos in are queued with that folder
alone (indexed without its subfolders); a folder under a root that holds photos and no
indexed photo is not indexed on its own: it is listed as a folder to review (`review`),
which the page offers to include (`include`: indexed with its subfolders) or to ignore
(its path added to the library's ignored folders), and a folder under an ignored one is
passed over. A scan that finds nothing costs one walk and no file reads
(tagpup.files.images.stamps_under): ExifTool is started only for a changed file, or to
read the identities of the new files a missing row's stamp did not settle.

On the maintenance scaffold (tagpup.services.maintenance): a dry run by default, which
reads and rehearses and writes nothing -- neither a row nor a photo file (the files are
only ever read: no DocumentID is minted); applied, the re-read rows and the moved rows are
one change of the journal, `sync`, undoable. Then the new files' folders are queued, and
the run is recorded in the library (tagpup.store.sync_runs): what it found and what it
changed, and whether it left the library in step -- nothing new, changed or moved left
over; missing files do not count against it -- which is the pages' "last in step".
"""
import logging
import os
import threading
import time

from tagpup.core import paths, runs, validation
from tagpup.core.result import Result
from tagpup.files import images
from tagpup.services import damaged_photos, folder_ids, maintenance, refresh_rows, relink_photos
from tagpup.services import roots as roots_service
from tagpup.store import damaged_files, db, generations, schema, sync_runs
from tagpup.store import folders as store_folders
from tagpup.store import photos as store_photos

logger = logging.getLogger(__name__)

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


def pair_by_stamp(missing, new):
    """(pairs [(old, new)], ambiguous rows [path], ambiguous files {key}) of the `missing`
    rows [(photo_id, path, mtime, size)] and the `new` files {key: (path, mtime, size)}:
    a row and a file are the same photo when they have one name, one size, and a time
    the row describes (store.photos.describes) -- and only when each is the other's one
    match. A row or file with more than one match is ambiguous. Reads nothing."""
    files = {}
    for key, (path, mtime, size) in new.items():
        files.setdefault((os.path.basename(path), size), []).append((key, path, mtime))
    matches, claimed = {}, {}
    for _photo_id, path, mtime, size in missing:
        found = [(key, file_path) for key, file_path, file_mtime in files.get((os.path.basename(path), size), ())
                 if store_photos.describes(mtime, size, (file_mtime, size))]
        if found:
            matches[path] = found
            for key, _file_path in found:
                claimed[key] = claimed.get(key, 0) + 1
    pairs, ambiguous_rows, ambiguous_files = [], [], set()
    for path, found in matches.items():
        if len(found) == 1 and claimed[found[0][0]] == 1:
            pairs.append((path, found[0][1]))
        else:
            ambiguous_rows.append(path)
            ambiguous_files.update(key for key, _file_path in found)
    return pairs, ambiguous_rows, ambiguous_files


def _missing_by_folder(missing):
    """[(folder, rows, whether it is gone)] of the missing rows' folders, the most first."""
    by_folder = {}
    for _photo_id, path in missing:
        folder = os.path.dirname(path)
        by_folder.setdefault(paths.key(folder), [folder, 0])[1] += 1
    listed = [(folder, count, not os.path.isdir(folder)) for folder, count in by_folder.values()]
    return sorted(listed, key=lambda entry: (-entry[1], paths.key(entry[0])))


def _under_any(folder, folders):
    """Is `folder` one of `folders` ({key: spelling}), or under one?"""
    key = paths.key(folder)
    return key in folders or any(paths.is_under(folder, other) for other in folders.values())


def _scan(conn, folder, roots, library_folders):
    """What is on disk against the rows: (by_key {key: (id, path, mtime, size)} of the rows
    looked at, on_disk {key: (path, mtime, size)}, the folders walked, those not there).
    The whole library walks every root and every folder of the library's
    (`library_folders`, tagpup.store.folders: those with rows, and those added), each once
    from the topmost; a folder, that folder alone."""
    by_key = {}
    for photo_id, path, mtime, size in store_photos.stamps(conn, folder):
        by_key.setdefault(paths.key(path), (photo_id, path, mtime, size))
    walked = [paths.stored(folder)] if folder else walk_roots(set(roots) | set(library_folders.walked()))
    on_disk, gone = {}, []
    for root in walked:
        if os.path.isdir(root):
            on_disk.update(images.stamps_under(root))
        else:
            gone.append(root)
    return by_key, on_disk, walked, gone


def _pass_over_damaged(conn, new):
    """(the new files but those found not to decode and unchanged since, {key: path} of
    those). One read of the records: a handful."""
    known = damaged_photos.unreadable(damaged_files.every(conn))
    damaged = {key: path for key, (path, mtime, size) in new.items()
               if key in known and damaged_photos.describes(known[key], (mtime, size))}
    return {key: stamp for key, stamp in new.items() if key not in damaged}, damaged


def _missing_elsewhere(conn, by_key, new):
    """The rows outside a folder-limited sync that a new file in it may have moved from:
    another folder's row with the new file's name and size whose file is gone, as
    [(photo_id, path, mtime, size)]. One read of the rows' stamps, and a look on disk for
    each candidate alone: a file moved in from another folder keeps its row."""
    wanted = {(os.path.basename(path), size) for path, _m, size in new.values()}
    found = []
    for photo_id, path, mtime, size in store_photos.stamps(conn):
        if (os.path.basename(path), size) in wanted and paths.key(path) not in by_key and not os.path.exists(path):
            found.append((photo_id, path, mtime, size))
    return found


def _pair_moves(conn, by_key, missing, new, exiftool_path, read=True):
    """(pairs [(old, new)], ambiguous rows [path], ambiguous files {key}) of the missing
    rows [(photo_id, path, mtime, size)] and the new files: by name, size and time first,
    reading nothing; then, for what that left and only when `read`, by the identity read
    from those new files alone."""
    pairs, ambiguous_rows, ambiguous_files = pair_by_stamp(missing, new)
    settled = {paths.key(old) for old, _new in pairs} | {paths.key(new_path) for _old, new_path in pairs}
    left_rows = [path for _id, path, _m, _s in missing if paths.key(path) not in settled]
    left_files = sorted(path for key, (path, _m, _s) in new.items() if key not in settled)
    if read and left_rows and left_files:
        lookup, by_identity = relink_photos.claims_of(left_files, exiftool_path)
        live = set(by_key) - {paths.key(path) for _id, path, _m, _s in missing}
        by_id, _unmatched = relink_photos.pair(left_rows, lookup, by_identity, store_photos.identities(conn), live)
        pairs += by_id
        found_by_id = {paths.key(old) for old, _new in by_id} | {paths.key(new_path) for _old, new_path in by_id}
        ambiguous_rows = [path for path in ambiguous_rows if paths.key(path) not in found_by_id]
        ambiguous_files -= found_by_id
    return pairs, ambiguous_rows, ambiguous_files


def _sort_new(new, by_key, stops, ignored, ambiguous_files, roots, library_folders):
    """Where each new file goes: ({indexed folder: new files}, the folders its new files
    are queued for; {folder to review: photos}; photos under an ignored folder; {folder
    held back: files}; photos in no folder the library holds and under none of `roots`).
    With no `roots` (none set yet), a new subfolder of a held folder is a folder to review, as
    under a root: never queued unasked.
    A new file in a folder the library holds photos in is indexed
    with that folder alone. One in a folder holding none is under a folder to review: the
    topmost above it, below a root (`stops`, keys), that holds no indexed photo at any
    depth -- unless it is under an ignored folder. A folder holding a file that may be a
    copy of a missing photo (`ambiguous_files`) is held back from both.

    Which folders the library holds is `library_folders`' (tagpup.store.folders): those
    with rows and those added -- a new subfolder of a folder added with its subfolders is
    the library's, queued, not to review -- but never an ignored one."""
    held_by_key = {}

    def is_held(folder):
        key = paths.key(folder)
        if key not in held_by_key:
            held_by_key[key] = library_folders.holds(folder)
        return held_by_key[key]

    holding = set()
    for folder in library_folders.walked():
        current = folder
        while paths.key(current) not in holding:
            holding.add(paths.key(current))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    tops, queued, review, held_back, ignored_files, outside = {}, {}, {}, {}, 0, 0
    ambiguous_folders = {paths.key(os.path.dirname(new[key][0])) for key in ambiguous_files}
    for key, (path, _mtime, _size) in new.items():
        folder = os.path.dirname(path)
        folder_key = paths.key(folder)
        if is_held(folder):
            if folder_key in ambiguous_folders:
                held_back[folder] = held_back.get(folder, 0) + 1
            else:
                queued.setdefault(folder_key, [folder, 0])[1] += 1
            continue
        if folder_key not in tops:
            top = folder
            while True:
                parent = os.path.dirname(top)
                if parent == top or paths.key(top) in stops or paths.key(parent) in holding:
                    break
                top = parent
            tops[folder_key] = top
        top = tops[folder_key]
        # A library with no roots (none set yet) offers a new subfolder of a folder it holds for review, as it does
        # under a root, and never queues it unasked (#395); nothing is "outside" the roots it has not got.
        if roots and not _under_any(folder, roots):
            # Beside a folder the library holds outside every root (a folder indexed by
            # hand, elsewhere): kept in step itself, and nothing new beside it offered.
            outside += 1
        elif ignored and _under_any(folder, ignored):
            ignored_files += 1
        elif any(paths.key(os.path.dirname(new[other][0])) == folder_key or
                 paths.is_under(new[other][0], top) for other in ambiguous_files):
            held_back[top] = held_back.get(top, 0) + 1
        else:
            review.setdefault(paths.key(top), [top, 0])[1] += 1
    return ({spelling: count for spelling, count in queued.values()},
            {spelling: count for spelling, count in review.values()}, ignored_files, held_back, outside)


def look(library, folder=None, exiftool_path=None, roots=(), ignored=()):
    """What differs between the library and its folders (or `folder`): a Plan (maintenance)
    whose `work` holds the edits and the folders of new files. Reads the rows, walks the
    roots and the folders the library holds photos in, and reads with ExifTool only the
    changed files and, where a row is missing, the identities of the new files its stamp
    did not settle. Writes nothing.

    New files in a folder the library holds photos in are queued with that folder; a
    folder under a root holding no indexed photo is listed to review (`review`), never
    indexed on its own; one under an `ignored` folder is passed over.

    Refused for a `folder` the rules do not take as one (tagpup.core.validation), and for
    one the library holds no photo under that is under none of its roots."""
    roots = [paths.stored(root) for root in roots]
    ignored_by_key = {paths.key(f): paths.stored(f) for f in ignored}
    if folder is not None:
        problem = validation.problem("folder", folder)
        if problem:
            return maintenance.Plan(refused=problem)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        library_folders = store_folders.of(conn, list(ignored_by_key.values()))
        by_key, on_disk, walked, roots_gone = _scan(conn, folder, roots, library_folders)
        if (folder is not None and not by_key and not library_folders.holds(folder)
                and not _under_any(folder, {paths.key(r): r for r in roots})):
            return maintenance.Plan(refused=(
                "The library holds no photo under that folder, and it is under none of the library's root "
                "folders: sync keeps the library's folders in step. To add it, add a root folder or index it."))

        changed, missing, never_stamped, resized = {}, [], 0, set()
        for key, (photo_id, path, mtime, size) in by_key.items():
            stamp = on_disk.get(key)
            if stamp is None:
                missing.append((photo_id, path, mtime, size))
            elif not store_photos.describes(mtime, size, stamp[1:]):
                changed[path] = photo_id
                never_stamped += mtime is None or size is None
                if size is not None and size != stamp[2]:
                    resized.add(path)
        new, damaged = _pass_over_damaged(conn, {key: stamp for key, stamp in on_disk.items() if key not in by_key})

        # Moved: a missing row whose file turns up among the new ones. A folder alone also
        # looks for the rows of files moved in from elsewhere.
        elsewhere = _missing_elsewhere(conn, by_key, new) if folder is not None and new else []
        moves, ambiguous_rows, ambiguous_files = [], [], set()
        if (missing or elsewhere) and new:
            pairs, ambiguous_rows, ambiguous_files = _pair_moves(conn, by_key, missing + elsewhere, new, exiftool_path)
            moves = relink_photos.moves_with_faces(conn, pairs)
        stamp_of = {paths.key(path): (mtime, size) for _id, path, mtime, size in missing + elsewhere}
        id_of = {paths.key(path): photo_id for photo_id, path, _m, _s in missing + elsewhere}
        moved_from = {paths.key(move["from"]) for move in moves}
        moved_to = {paths.key(move["to"]) for move in moves}
        missing = [(photo_id, path) for photo_id, path, _m, _s in missing if paths.key(path) not in moved_from]
        # A moved file whose stamp is not its row's is read again by the next sync, once
        # the row names it.
        moved_changed = sum(1 for move in moves
                            if not store_photos.describes(*stamp_of[paths.key(move["from"])],
                                                          on_disk[paths.key(move["to"])][1:]))
        new = {key: stamp for key, stamp in new.items() if key not in moved_to}

        # Changed: re-read as the refresh re-reads, each row held to what it was when read.
        found = {}
        for path in changed:
            tags, _people, captions, raw, mtime, size, _doc = store_photos.row_as_recorded(conn, path)
            found[path] = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
        records, to_write, fields, unreadable, _shown, identities = refresh_rows.reread(
            conn, {path: ["mtime/size"] for path in changed}, exiftool_path)
        # A file ExifTool could not read is left for the next sync: its faces are not thrown away unseen.
        resized -= set(unreadable)
    finally:
        conn.close()

    edits = refresh_rows.edits_for(records, to_write, found, identities, changed)
    moved_edits, occupied = relink_photos.edits_for(library, moves)
    by_folder = _missing_by_folder(missing)
    stops = {paths.key(root) for root in walked + roots}
    queued, review, ignored_files, held_back, outside = _sort_new(
        new, by_key, stops, ignored_by_key, ambiguous_files, {paths.key(r): r for r in roots}, library_folders)
    new_folders = sorted(queued, key=paths.key)
    new_paths = sorted((path for path, _m, _s in new.values()
                        if paths.key(os.path.dirname(path)) in {paths.key(f) for f in queued}), key=paths.key)
    listed = [{"path": top, "photos": review[top]} for top in sorted(review, key=paths.key)]
    # What the marked folders that moved are told by (tagpup.services.folder_ids): the folders of the rows missing or
    # moved, and where files were found new or moved to. Nothing is looked for beyond them.
    row_folders = {paths.key(os.path.dirname(path)): os.path.dirname(path)
                   for path in [path for _id, path in missing] + [move["from"] for move in moves]}
    marker_places = {paths.key(folder): folder for folder in new_folders}
    marker_places.update({paths.key(os.path.dirname(move["to"])): os.path.dirname(move["to"]) for move in moves})
    return maintenance.Plan(
        size=len(edits) + len(moved_edits),
        counts={"rows": len(by_key), "files": len(on_disk), "folders_walked": len(walked) - len(roots_gone),
                "new": sum(queued.values()), "new_folders": len(new_folders),
                "review_folders": len(review), "review_photos": sum(review.values()),
                "ignored_files": ignored_files, "outside_roots_files": outside,
                "changed": len(changed), "never_stamped": never_stamped, "to_write": len(to_write),
                "size_changed": len(resized),
                "fields": dict(fields.most_common()), "unreadable": len(unreadable),
                "moved": len(moves), "moved_faces": sum(m["faces"] for m in moves),
                "moved_named": sum(m["named"] for m in moves), "moved_changed": moved_changed,
                "occupied": len(occupied), "ambiguous_rows": len(ambiguous_rows),
                "ambiguous_files": len(ambiguous_files), "held_back_folders": len(held_back),
                "unreadable_files": len(damaged),
                "missing": len(missing), "missing_folders": len(by_folder),
                "folders_gone": sum(1 for _f, _n, gone in by_folder if gone), "roots_gone": len(roots_gone)},
        ids={"changed": sorted(changed.values()), "to_write": sorted(changed[p] for p in to_write),
             "unreadable": sorted(changed[p] for p in unreadable),
             "moved": sorted(id_of[paths.key(m["from"])] for m in moves),
             "missing": sorted(photo_id for photo_id, _path in missing),
             "ambiguous": sorted(id_of[paths.key(path)] for path in ambiguous_rows)},
        reveal={"new": new_paths, "new_folders": new_folders, "review": listed, "moves": moves,
                "occupied": occupied,
                "ambiguous": {"rows": ambiguous_rows, "files": sorted(new[key][0] for key in ambiguous_files),
                              "held_back_folders": sorted(held_back, key=paths.key)},
                "missing_folders": [{"folder": f, "rows": n, "gone": gone} for f, n, gone in by_folder],
                "roots_gone": roots_gone, "unreadable_files": sorted(damaged.values(), key=paths.key)},
        work={"edits": edits + moved_edits, "new_folders": new_folders,
              "row_folders": list(row_folders.values()), "marker_places": list(marker_places.values()),
              "marker_trees": sorted(review, key=paths.key)})


def review(library, roots=(), ignored=()):
    """The folders to review: each folder under a root that holds photos and no indexed
    photo, and is not ignored, with how many photos it holds -- what sync lists and the
    page offers to include or ignore. One walk, and no file read: a file that moved there
    is told by its name, size and time alone. {"folders": [{"path", "photos"}], "photos"}."""
    roots = [paths.stored(root) for root in roots]
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        library_folders = store_folders.of(conn, [paths.stored(f) for f in ignored])
        by_key, on_disk, walked, _gone = _scan(conn, None, roots, library_folders)
        missing = [(photo_id, path, mtime, size) for key, (photo_id, path, mtime, size) in by_key.items()
                   if key not in on_disk]
        new, _damaged = _pass_over_damaged(conn, {key: stamp for key, stamp in on_disk.items() if key not in by_key})
        ambiguous_files = set()
        if missing and new:
            pairs, _rows, ambiguous_files = _pair_moves(conn, by_key, missing, new, None, read=False)
            moved_to = {paths.key(new_path) for _old, new_path in pairs}
            new = {key: stamp for key, stamp in new.items() if key not in moved_to}
    finally:
        conn.close()
    stops = {paths.key(root) for root in walked + roots}
    _queued, found, _ignored, _held, _outside = _sort_new(
        new, by_key, stops, {paths.key(f): paths.stored(f) for f in ignored},
        ambiguous_files, {paths.key(r): r for r in roots}, library_folders)
    listed = [{"path": top, "photos": found[top]} for top in sorted(found, key=paths.key)]
    return {"folders": listed, "photos": sum(found.values())}


def include(library, folder, roots, queue, ignored=()):
    """Index a folder to review, with its subfolders: `queue([folder])` (the index queue's
    start). Refused for a folder the rules do not take as one, one not on disk, and one
    under none of the library's `roots` -- or, a library with no roots yet, under none of the
    folders it holds (never an ignored one: `ignored`, the library's, as look has it). A Result: `changed` 1 when it was queued."""
    result = Result(attempted=1)
    problem = validation.problem("folder", folder)
    if problem:
        result.refuse(problem)
        return result
    folder = paths.stored(folder)
    if not os.path.isdir(folder):
        result.refuse("That folder is not on disk.")
        return result
    if ignored and _under_any(folder, {paths.key(f): paths.stored(f) for f in ignored}):
        result.refuse("That folder is one the library ignores (or under one).")
        return result
    if roots:
        if not _under_any(folder, {paths.key(r): paths.stored(r) for r in roots}):
            result.refuse("That folder is under none of the library's root folders.")
            return result
    else:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            walked = store_folders.of(conn, [paths.stored(f) for f in ignored]).walked()
        finally:
            conn.close()
        if not _under_any(folder, {paths.key(f): f for f in walked}):
            result.refuse("That folder is under none of the folders the library holds.")
            return result
    outcome = queue([folder])
    result.changed = outcome.changed
    for what, why in outcome.skipped:
        result.skip(what, why)
    if outcome.refused:
        result.refuse(outcome.refused)
    return result


def in_step(counts, result=None):
    """Is the library in step with its folders by what a sync found (`counts`) and, once
    applied, did (`result`)? Nothing new, nothing changed or moved left unwritten, nothing
    unreadable. Missing files do not count: they are reported, and removing their rows is
    the owner's choice; nor do damaged files passed over (`unreadable_files`): restoring
    them is. A sync refused found nothing to say so."""
    if not counts:
        return False
    if (counts.get("new") or counts.get("unreadable") or counts.get("moved_changed") or counts.get("occupied")
            or counts.get("ambiguous_rows")):
        return False
    if result is None:
        return not (counts.get("to_write") or counts.get("moved"))
    return result.ok and not result.skipped


@roots_service.canonical_args("folder")
def sync(library, folder=None, apply=False, exiftool_path=None, queue=None, roots=(), ignored=()):
    """Bring `library` in step with its folders (or with `folder`), reading changed files
    with the ExifTool at `exiftool_path`; a dry run unless `apply`. A Result on the
    maintenance scaffold: `changed` is the rows the change changed (details["changed"]:
    re-read "from_files", "relinked"), read from the writes; details["counts"] what was
    found (see look), ["ids"] the photo ids, ["reveal"] the paths.

    `roots` and `ignored` are the library's root folders and ignored folders (its
    settings): see look.

    Applied, the folders holding new files are handed to `queue(folders)`, which returns
    the index queue's Result (tagpup.jobs.indexing.IndexQueue.start): details["queued"] is
    the folders it queued. Without a queue they are reported, not queued. Then the run is
    recorded (tagpup.store.sync_runs) -- unless it was refused or its write failed, when
    nothing is queued or recorded; a record that cannot be written is one of
    details["warnings"], not a failure of the write. details["in_step"] says whether the
    library is in step: for a dry run, as found; applied, after the write.
    """
    started = sync_runs.now()
    # Every line the sync logs carries its tag, made from what its record keeps (the
    # library and when it started): the Activity page's "Logs for this run".
    try:
        # One map for the whole pass: a place moved meanwhile (TagTuner's Roots) changes nothing
        # until it ends, and another process changing the library's roots stops it, cleanly.
        with runs.running(runs.sync_tag(library.name, started)), roots_service.pinned(library):
            return _sync(library, folder, apply, exiftool_path, queue, roots, ignored, started)
    except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
        return _not_run(stop, apply)


def _not_run(stop, apply):
    """A sync that did not run -- the library's roots changed under it, or this machine does
    not place one: a refused Result with what the callers read of any, and why."""
    return not_run(roots_service.stopped(stop), apply)


def not_run(why, apply):
    """A sync that did not run, `why` said: a refused Result with what the callers read of any."""
    result = Result()
    result.refuse(why)
    result.details.update(counts={}, in_step=False, changed={kind: 0 for kind in KINDS}, queued=0, warnings=[],
                          dry_run=not apply)
    return result


def _sync(library, folder, apply, exiftool_path, queue, roots, ignored, started):
    held = {}

    def plan(found_library):
        held["plan"] = look(found_library, folder, exiftool_path, roots, ignored)
        return held["plan"]

    result = maintenance.run(library, OPERATION, plan, lambda planned: planned.work["edits"], apply=apply, kinds=KINDS)
    planned = held.get("plan")
    counts = result.details["counts"]
    # The marked folders that moved (folder_ids): only when the walk met a file missing or moved, and only for a
    # library that has marked a folder; a dry run says what it would follow.
    result.details["folders_marked"] = folder_ids.marked_count(library) if counts else 0
    result.details["folder_markers"] = None
    # Whatever the scope: the watcher syncs the folder a folder was renamed in, and that sync is the one that would
    # otherwise queue the renamed folder as new.
    # A folder queued as new that carries a marker of a marked folder recorded elsewhere is the folder moved into a
    # folder the library holds, where this sync sees nothing missing or moved: followed as well (lost_among).
    work = dict(planned.work) if planned is not None and planned.work else None
    if work and result.details["folders_marked"] and work["new_folders"]:
        try:
            work["row_folders"] = list(work["row_folders"]) + folder_ids.lost_among(library, work["new_folders"])
        except Exception as e:
            logger.warning("Marked folders among the new folders were not looked at (%s: %s)", type(e).__name__, e)
    follow_markers = bool(counts and work and result.details["folders_marked"]
                          and (counts.get("missing") or counts.get("moved") or work["row_folders"]))
    if not apply:
        if follow_markers:
            result.details["folder_markers"] = _follow_markers(library, exiftool_path, work, False)
        result.details["in_step"] = in_step(counts)
        return result

    result.details.setdefault("changed", {kind: 0 for kind in KINDS})
    result.details["queued"] = 0
    result.details["warnings"] = []
    if not result.ok:
        # Refused, or the write failed: nothing was synced, so nothing is queued -- a
        # moved file's folder indexed before its row follows it would get a second row --
        # and nothing is recorded.
        result.details["in_step"] = False
        return result
    new_folders = list(planned.work["new_folders"]) if planned is not None and planned.work else []
    if follow_markers:
        # After the sync's own change and before anything is queued: the files of a folder followed are not new.
        report = result.details["folder_markers"] = _follow_markers(library, exiftool_path, work, True)
        if report["error"]:
            result.details["warnings"].append(report["error"])
        followed_to = [new for _old, new in report["followed"]]
        new_folders = [each for each in new_folders if not any(paths.same(each, new) for new in followed_to)]
    try:
        # A damaged photo replaced, changed or deleted is no longer one (damaged_photos): a
        # changed one is indexed again for real, its folder queued with the new files'.
        pruned = damaged_photos.prune(library)
        result.details["damaged_forgotten"] = pruned["forgotten"]
        known = {paths.key(folder) for folder in new_folders}
        new_folders += [folder for folder in pruned["folders"] if paths.key(folder) not in known]
    except Exception as e:
        result.details["warnings"].append("The damaged photos found before were not checked (%s: %s)."
                                          % (type(e).__name__, e))
    queued = 0
    if new_folders and queue is not None:
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
        # The rows are written and the folders queued; only the record of it is missing.
        result.details["warnings"].append("The sync was not recorded (%s: %s); the next sync records its own."
                                          % (type(e).__name__, e))
    return result


def _follow_markers(library, exiftool_path, work, apply):
    """{"counts", "changed" (rows written), "followed" [(old, new)], "error"} of following the library's marked
    folders that moved (tagpup.services.folder_ids.follow), over what the sync found (its plan's `work`). A failure
    is reported in "error" and never stops the sync: its own change is written, and the next sync follows again."""
    try:
        followed = folder_ids.follow(library, work["marker_places"], work["marker_trees"], work["row_folders"], apply,
                                     exiftool_path)
    except Exception as e:
        return {"counts": {}, "changed": 0, "followed": [], "error": "Marked folders were not followed (%s: %s)."
                % (type(e).__name__, e)}
    error = None
    if followed.refused or followed.errors:
        error = "Marked folders were not followed: %s" % followed.message()
    return {"counts": followed.details.get("counts", {}), "changed": followed.changed,
            "followed": list(followed.details.get("followed", [])) if apply else [], "error": error}


#: {library key: (photos generation, the folders it holds photos in)}: watch_folders is
#: asked every 30 s, and reading every photo's path (68,387 on photo_index) each time for
#: an answer that changes only with the photos was the watcher's whole cost.
_held_folders = {}
_held_lock = threading.Lock()


def watch_folders(library, roots=()):
    """The folders the always-on process watches for `library` (tagpup.jobs.watching): its
    root folders and every folder of the library's (tagpup.store.folders: with rows, or
    added), each the topmost of those under it -- the folders sync keeps in step. None for
    a library behind this version's schema: it is left alone until an app opens it and
    migrates it, as the recurring jobs leave it. The folders with rows are read again only
    when the photos generation has moved; the added and ignored ones, a handful, each time."""
    if schema.pending(library.path):
        return []
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        generation = generations.value(conn, "photos")
        with _held_lock:
            cached = _held_folders.get(library.key)
        if cached is not None and cached[0] == generation:
            rows = cached[1]
        else:
            rows = store_folders.with_rows(conn)
            with _held_lock:
                _held_folders[library.key] = (generation, rows)
        held = store_folders.of(conn, rows=rows).walked()
    finally:
        conn.close()
    return walk_roots([paths.stored(root) for root in roots] + held)


def synced_whole_within(library, seconds):
    """Did a sync of the whole of `library` finish in the last `seconds`? (Its record is
    kept only for a sync that wrote what it found.)"""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        finished = sync_runs.last_whole(conn)
    finally:
        conn.close()
    if not finished:
        return False
    try:
        at = time.mktime(time.strptime(finished, sync_runs.TIME))
    except (TypeError, ValueError):
        return False
    return time.time() - at < seconds


def last(library):
    """{"last_run", "last_in_step"} of `library` (tagpup.store.sync_runs.last): counts and
    times, never a path. Reads only; a library from before migration 14 has neither."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return sync_runs.last(conn)
    finally:
        conn.close()

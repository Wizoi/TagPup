"""Folder ids: a `.tagpup` marker in each leaf folder, so a renamed or moved folder is followed
exactly (docs/ARCHITECTURE.md, "Folder ids"; owner, 2026-10-08).

Following a renamed folder by what its photos hold (tagpup.services.folder_moves) is evidence,
and it fails where the evidence is thin. A folder that carries an id of its own follows any
rename. It is opt-in: nothing here runs unless the owner runs `folder-ids mark`, and a library
that never does has no marker written for it and no identifier stamped.

**mark** (a dry run unless applied; the maintenance scaffold's counts, names only behind
`reveal`). For every folder the library holds photos in directly (its leaf folders): read the
folder's marker (tagpup.files.folder_marker) and sort it --

- *new*: no entry of this library: a fresh id is written, the other libraries' entries kept
  byte for byte, and recorded;
- *restore*: the library records an id for this folder and the marker lacks it (a deleted
  file, a write that did not reach it): the recorded id is written again;
- *adopt*: the marker holds this library's entry and the library has no row for it (a run
  interrupted between the file and the record, a table restored): recorded, no file written,
  no second id made;
- *already*: marker and library agree;
- left alone and counted: a *copy* (the marker's id is recorded for a folder that is still
  there; never rewritten, a copy of a folder is not that folder), a folder the id of which is
  recorded for a folder that is gone (*moved*: the next sync follows it), a *disagreement*
  (the marker and the library name different ids for this folder), a marker that does not parse
  (*malformed*: hand-edited, or another program's file of that name, never rewritten), a
  marker or folder that cannot be read (*unreadable*: a locked file, a share gone) and a place
  that cannot be written (*unwritable*: a read-only share or file; nothing is ever made
  writable).

The library's own identifier is stamped by the first apply that records an id, in the same
transaction (store.folder_ids.stamp), and a library file in the same folder that carries it
already is a copy: mark is refused, and tools/doctor.py names it. Applying stages every file
under a temporary name beside its marker, records the ids of the files that could be staged
(and the stamp) as ONE journaled change, then renames each into place after checking the
marker is still as it was read; so a crash leaves either nothing, an id recorded whose file the
next run writes (*restore*), or a file the next run records (*adopt*), never a second id. A file
that was changed by another program meanwhile is left (counted `changed_meanwhile`), and the
id recorded for it is written by the next run. Two libraries marking one folder in the same
instant could still replace each other's entry (a rename cannot compare first); the read-back
after the rename counts it (`lost`) and the next run puts the entry back.

**follow** (a sync's, the watcher's -- any scope, a folder's sync included -- and `relink-folders`'): the one
step that decides "this new folder is a marked folder that was lost" before anything is queued. When the sync
found a file missing or moved and the library has marked folders, a row of `folder_ids` whose folder is among
those of the missing or moved rows and is gone, and a marker carrying that id for this library in a folder the
sync found new files or moved files in, or beside the folder that is gone (the same path under each folder in
its parent's listing, one listing and no walk), points the rows directly in the old folder at the new, the
`folder_ids` row, and the library's root and ignored-folder settings and the folders added at it, as one
journaled change, `follow_folder_markers`, with no evidence needed beyond the id. It runs after the sync's own
change and before the folders of new files are queued, so a followed folder is never queued as new. Per photo:
the file of the same name in the new folder when it has no row; else a file with the same DocumentID, or the same
size and Date Taken (folder_moves.pair_by_evidence), for a folder renamed and its photos renamed. A file that
already has a row is never a destination (relink_photos.edits_for). **A folder none of whose rows can go -- its
files already have rows of their own, because they were indexed as new before anything followed, or none of its
files is there -- is left, as it was, and reported (`left`): its id is not moved and it is not said to be followed,
since the rows and the faces named on them would stay missing with nothing to say so.** The copy rule, per
library: of the folders carrying an id the one whose recorded folder is gone takes the link; a copy beside a
recorded folder that is there is reported and nothing follows it; two places for one lost id are ambiguous and
nothing follows; a shared id never moves a row. Only positive evidence moves anything: a folder not found, a
share not reachable, a folder that cannot be listed move nothing. A folder moved to another parent is found when
the sync found its files new there or moved there; `relink-folders` looks beside the folder only.
"""
import collections
import os

from tagpup.core import paths
from tagpup.core.result import Result
from tagpup.files import folder_marker, images
from tagpup.services import folder_moves, maintenance, relink_photos
from tagpup.services import journal as journal_service
from tagpup.services import roots as roots_service
from tagpup.store import added_folders, db, journal
from tagpup.store import folder_ids as store
from tagpup.store import folders as store_folders
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

#: What the change is recorded as.
OPERATION = "mark_folders"
FOLLOW = store.FOLLOW_OPERATION

KINDS = ("marked",)
FOLLOW_KINDS = ("relinked", "followed", "settings")

#: What a marker the library holds no entry of is sorted as, and what is left alone.
WRITES = ("new", "restore")


class _Item:
    """A folder to mark: `kind` (new, restore or adopt), the `folder_id` it holds for this library, and the marker's
    bytes as read (None for no file), which the write is held to."""

    def __init__(self, kind, folder, folder_id, data):
        self.kind, self.folder, self.folder_id, self.data = kind, folder, folder_id, data


def _classify(folder, marker, library_id, by_path, by_id):
    """(kind, folder id) of the folder's marker against the library's records: see the module."""
    if marker.state == folder_marker.MALFORMED:
        return "malformed", None
    if marker.state == folder_marker.UNREADABLE:
        return "unreadable", None
    row = by_path.get(paths.key(folder))
    ours = marker.entry_of(library_id) if library_id else None
    if ours:
        if row:
            return ("already", ours) if row[0] == ours else ("disagree", ours)
        where = by_id.get(ours)
        if where is None:
            return "adopt", ours
        return ("copy" if os.path.isdir(where) else "moved"), ours
    if (marker.state == folder_marker.OK and not marker.writable) or not folder_marker.can_write_into(folder):
        return "unwritable", None
    if row:
        return "restore", row[0]
    return "new", folder_marker.new_id()


def look(library):
    """A Plan: what `folder-ids mark` would do. Reads the library's rows and each leaf folder's marker; writes
    nothing. work = {"identity" (None: not stamped), "items", "edits" (the ids to record)}."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        library_id = store.identity(conn)
        recorded = store.rows(conn)
        folders = store_folders.with_rows(conn)
        ignored = store_folders.ignored(conn)
    finally:
        conn.close()
    twins = store.twins(library.path, library_id)
    if twins:
        return maintenance.Plan(refused=(
            "Another library file in this data folder carries this library's identifier (%s): it is a copy of this one, "
            "and two libraries answering to one entry of a marker would follow each other's folders. Nothing is "
            "marked. Give the copy up, or keep the one library." % ", ".join(twins)))
    by_path = {paths.key(path): (folder_id, path) for folder_id, path, _marked in recorded}
    by_id = {folder_id: path for folder_id, path, _marked in recorded}
    counts = collections.OrderedDict((name, 0) for name in (
        "leaf_folders", "already", "new", "restore", "adopt", "copy", "moved", "disagree", "malformed", "unreadable",
        "unwritable", "gone", "ignored", "shared_with_other_libraries", "recorded"))
    counts["recorded"] = len(recorded)
    items, tidy, reveal = [], [], collections.defaultdict(list)
    for folder, _photos in sorted(folders, key=lambda each: paths.key(each[0])):
        counts["leaf_folders"] += 1
        if store_folders.is_ignored(folder, ignored):
            counts["ignored"] += 1
            continue
        if not os.path.isdir(folder):
            counts["gone"] += 1
            reveal["gone"].append(folder)
            continue
        marker = folder_marker.read(folder)
        kind, folder_id = _classify(folder, marker, library_id, by_path, by_id)
        counts[kind] += 1
        if kind in ("copy", "moved", "disagree", "malformed", "unreadable", "unwritable"):
            reveal[kind].append(folder)
        if marker.state == folder_marker.OK and any(each != library_id for each, _id in marker.entries):
            counts["shared_with_other_libraries"] += 1
        if kind in ("new", "restore", "adopt"):
            items.append(_Item(kind, folder, folder_id, marker.data))
        elif kind == "already":
            tidy.append(folder)
    edits = [store.insert_edit(item.folder, item.folder_id) for item in items if item.kind in ("new", "adopt")]
    counts["would_stamp"] = int(library_id is None and any(item.kind == "new" for item in items))
    return maintenance.Plan(size=len(edits), counts=dict(counts), reveal=dict(reveal),
                            work={"identity": library_id, "items": items, "edits": edits, "tidy": tidy})


def mark(library, apply=False):
    """Mark the library's leaf folders (see the module): a dry run unless `apply`. A Result;
    details["counts"] what was found; applied, `changed` is the marker files written and
    details["changed"] {"markers": files written, "ids": ids recorded}, details["not_written"] what
    was recorded or staged and did not reach a file (refused, changed meanwhile, lost)."""
    try:
        with roots_service.pinned(library):
            if not apply:
                return maintenance.run(library, OPERATION, look, lambda planned: planned.work["edits"], kinds=KINDS)
            return _apply(library)
    except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
        result = Result()
        result.refuse(roots_service.stopped(stop))
        result.details.update(counts={}, changed={"markers": 0, "ids": 0}, dry_run=not apply)
        return result


def _stamp_with(library_id):
    def stamp(conn):
        if store.stamp(conn, library_id) != library_id:
            raise journal.Refusal(["the library was stamped with another identifier meanwhile (another run of "
                                   "folder-ids mark): nothing was recorded; run it again"])
    return stamp


def _apply(library):
    planned = look(library)
    result = Result(attempted=len(planned.work["items"]) if planned.work else 0, details={
        "dry_run": False, "change": None, "counts": dict(planned.counts), "ids": {}, "reveal": dict(planned.reveal),
        "changed": {"markers": 0, "ids": 0}, "not_written": {"refused": 0, "changed_meanwhile": 0, "lost": 0}})
    if planned.refused:
        result.refuse(planned.refused)
        return result
    items = planned.work["items"]
    for folder in planned.work["tidy"]:
        folder_marker.clear_stale(folder)      # a crashed run's leftovers, also where nothing is written now
    if not items:
        return result
    library_id = planned.work["identity"] or folder_marker.new_id()
    not_written = result.details["not_written"]

    # 1. Stage every file under a temporary name: a place that refuses is counted here, before anything is recorded.
    staged = []
    for item in items:
        folder_marker.clear_stale(item.folder)
        if item.kind == "adopt":
            staged.append((item, None))
            continue
        try:
            temp = folder_marker.stage(item.folder, folder_marker.with_entry(item.data, library_id, item.folder_id))
        except OSError as e:
            not_written["refused"] += 1
            result.skip(item.folder, "could not be written (%s): left as it was" % type(e).__name__)
            result.details["reveal"].setdefault("refused", []).append(item.folder)
            continue
        staged.append((item, temp))

    def discard_all():
        for _item, temp in staged:
            if temp:
                folder_marker.discard(temp)

    # 2. Record the ids of the staged files (and adopted markers), and the identifier with the first of them, as one change.
    edits = [store.insert_edit(item.folder, item.folder_id) for item, _t in staged if item.kind in ("new", "adopt")]
    summary = {"counts": dict(planned.counts)}
    applied = None
    if edits:
        try:
            applied = journal.apply(library.path, OPERATION, edits, summary, also=_stamp_with(library_id))
        except journal.Refusal as e:
            discard_all()
            result.refuse("Nothing was recorded or written: %s" % e)
            return result
        except Exception as e:
            discard_all()
            result.fail("the write", "%s: %s" % (type(e).__name__, e))
            return result
        result.details["change"] = applied.change_id
        result.details["changed"]["ids"] = applied.changed
        if not applied.settled:
            result.fail("the people and dates of the photos it touched",
                        "not rebuilt yet; they are, the next time the library is opened")

    # 3. Put each staged file in place, if its marker is still as it was read.
    written = 0
    for item, temp in staged:
        if temp is None:
            continue
        again = folder_marker.read(item.folder)
        if again.state not in (folder_marker.ABSENT, folder_marker.OK) or again.data != item.data:
            folder_marker.discard(temp)
            not_written["changed_meanwhile"] += 1
            result.details["reveal"].setdefault("changed_meanwhile", []).append(item.folder)
            continue
        try:
            folder_marker.publish(temp, item.folder)
        except OSError as e:
            folder_marker.discard(temp)
            not_written["refused"] += 1
            result.skip(item.folder, "could not be put in place (%s): the id is recorded, the next run writes it"
                        % type(e).__name__)
            continue
        if folder_marker.read(item.folder).entry_of(library_id) != item.folder_id:
            not_written["lost"] += 1
            result.details["reveal"].setdefault("lost", []).append(item.folder)
            continue
        written += 1
    result.changed = written
    result.details["changed"]["markers"] = written
    if applied is not None:
        result.details["pruned"] = journal.prune(library.path, keep=lambda: journal_service.kept_operations(library))[0]
    return result


def marked_count(library):
    """How many folders the library has marked (0 for a library behind this version). Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return store.count(conn)
    finally:
        conn.close()


# ---- Following a folder by its marker ----------------------------------------------------------

def _direct_photos(folder):
    """{paths.key: (path, mtime, size)} of the photos directly in `folder`."""
    found = {}
    for photo in images.photos_in(folder):
        try:
            info = os.stat(photo)
        except OSError:
            continue
        found[paths.key(photo)] = (photo, info.st_mtime, info.st_size)
    return found


def _pairs_for(conn, rows, new, exiftool_path):
    """([(row path, file path)], how many by name, how many by evidence, how many rows whose file of
    that name already has a row, how many files in `new` have a row) for `rows` (the photos directly in a folder that was moved) and the
    photos now directly in `new`. A file with a row is no destination: whether it has one is asked of the
    files in `new` alone, a point lookup each."""
    files = _direct_photos(new)
    known = set(store_photos.rows_of(conn, [path for path, _m, _s in files.values()]))
    by_name = {}
    for key, (path, _m, _s) in files.items():
        by_name.setdefault(paths.name_key(os.path.basename(path)), []).append(key)
    pairs, taken, left, occupied = [], set(), [], 0
    for row in rows:
        options = by_name.get(paths.name_key(os.path.basename(row[1])), [])
        if len(options) == 1:
            key = options[0]
            if key in known:
                occupied += 1
            else:
                pairs.append((row[1], files[key][0]))
                taken.add(key)
            continue
        left.append(row)
    free = {key: stamp for key, stamp in files.items() if key not in known and key not in taken}
    matched = folder_moves.pair_by_evidence(left, free, exiftool_path) if left and free else []
    return pairs + matched, len(pairs), len(matched), occupied, len(known)


def _near(lost, gone, known):
    """The folders a folder that is gone may be at, beside it: for each (id, path) of `lost`, the same path
    under each folder directly in the parent of the topmost folder gone (a renamed folder, and one holding it
    renamed: `Trips` to `Trips 2026` puts `Trips/Harbour` at `Trips 2026/Harbour`). One listing of that parent
    and no walk; the same as folder_moves looks beside a folder. A candidate that is the recorded folder of
    another marked folder (`known`, their keys) is not read: it is that folder, with an id of its own, and the
    folder that was renamed has a name nothing records. That keeps it to a read or two, not one for every
    sibling (a marker read cost 4 ms a file on a local disk here)."""
    near, listed = [], {}
    for _folder_id, path in lost:
        top = gone.unit(path)
        parent = os.path.dirname(top)
        if paths.key(parent) not in listed:
            listed[paths.key(parent)] = folder_marker.subfolders(parent)
        relative = os.path.relpath(paths.stored(path), paths.stored(top))
        for sibling in listed[paths.key(parent)]:
            candidate = sibling if relative == "." else os.path.join(sibling, relative)
            if paths.key(candidate) not in known:
                near.append(candidate)
    return near


def look_follow(library, places=(), trees=(), row_folders=None, exiftool_path=None):
    """A Plan: which folders the library has marked are gone and found again by their markers, and
    what following them would write. Reads the rows; writes nothing.

    Which folders are looked at is bounded (docs/ARCHITECTURE.md, "Folder ids"): the marked folders asked
    about are those of `row_folders` (the folders of the rows a sync found missing or moved; every marked folder
    when None, for the explicit command), each stat'ed once; the markers are read in `places` (the folders a
    sync found new files in or moved files to) and under `trees` (the folders it would only review), and, for
    an id still not found, beside the folder that is gone (_near: one listing of its parent). Never a walk of
    a root. Reads ExifTool only for the files of a folder renamed with its photos renamed.
    work = {"edits", "followed": [(old, new)], "added_follow"}."""
    counts = collections.OrderedDict((name, 0) for name in (
        "marked", "gone", "followed", "not_found", "ambiguous", "conflicts", "copies", "left", "photos_moved",
        "by_name", "by_evidence", "occupied", "folders_listed", "unreadable", "malformed", "settings_followed",
        "added_renamed", "added_left"))
    reveal = {"followed": [], "not_found": [], "ambiguous": [], "conflicts": [], "left": [], "occupied": []}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        library_id = store.identity(conn)
        recorded = store.rows(conn)
        counts["marked"] = len(recorded)
        asked = recorded if row_folders is None else [
            each for each in recorded if paths.key(each[1]) in {paths.key(folder) for folder in row_folders}]
        gone = folder_moves.Gone()
        lost = [(folder_id, path) for folder_id, path, _marked in asked
                if not gone.there(path) and gone.unit(path) is not None] if library_id else []
        counts["gone"] = len(lost)
        if not lost:
            return maintenance.Plan(counts=dict(counts), work={"edits": [], "followed": [], "added_follow": []})

        def look_at(found_here, stats_here):
            folder_marker.merge(found, found_here)
            for name in ("unreadable", "malformed"):
                counts[name] += stats_here[name]
            counts["folders_listed"] += stats_here["folders"]

        found = {}
        look_at(*folder_marker.read_in(places, library_id))
        if trees:
            look_at(*folder_marker.find(trees, library_id))
        lost_ids = {folder_id for folder_id, _path in lost}
        wanting = [each for each in lost if each[0] not in found]
        if wanting:
            look_at(*folder_marker.read_in(_near(wanting, gone, {paths.key(each[1]) for each in recorded}),
                                           library_id))
        added = added_folders.every(conn)
        # A root kept in two places on this machine (the server's master and a local mirror) holds the same
        # marker at the same place under the root: one folder, not a copy. Places are told apart as the
        # library holds them, and spelled as this machine's first place for the root spells them.
        for folder_id, here in list(found.items()):
            distinct = {}
            for place in here:
                row_form = store_roots.to_row(conn, place)
                distinct.setdefault(paths.key(row_form), store_roots.from_row(conn, row_form))
            found[folder_id] = list(distinct.values())
        recorded_paths = {paths.key(path): folder_id for folder_id, path, _marked in recorded}
        # A copy: an id found in a folder other than the one recorded for it, whose recorded folder is there.
        for folder_id, here in found.items():
            if folder_id not in lost_ids:
                counts["copies"] += sum(1 for place in here if recorded_paths.get(paths.key(place)) != folder_id)
        pairs, written, folder_edits = [], [], []
        for folder_id, old in sorted(lost, key=lambda each: paths.key(each[1])):
            here = found.get(folder_id, [])
            if not here:
                counts["not_found"] += 1
                reveal["not_found"].append(old)
                continue
            if len(here) > 1:
                counts["ambiguous"] += 1
                reveal["ambiguous"].append({"id_of": old, "places": here})
                continue
            new = here[0]
            other = recorded_paths.get(paths.key(new))
            if other is not None and other != folder_id:
                counts["conflicts"] += 1
                reveal["conflicts"].append(new)
                continue
            rows = [row for row in store_photos.evidence_under(conn, old) if paths.same(os.path.dirname(row[1]), old)]
            got, by_name, by_evidence, occupied, held = _pairs_for(conn, rows, new, exiftool_path)
            if rows and not got:
                # Nothing of it can go to the folder: its files already have rows of their own (indexed as new
                # before anything followed), or none of them is there. Following would move the folder's id and
                # leave every row, and the faces named on them, missing for good, and say it was followed.
                counts["left"] += 1
                counts["occupied"] += occupied
                reveal["left"].append({"from": old, "to": new, "rows": len(rows), "files_with_rows": held})
                continue
            counts["followed"] += 1
            counts["by_name"] += by_name
            counts["by_evidence"] += by_evidence
            counts["occupied"] += occupied
            pairs += got
            written.append((old, new))
            reveal["followed"].append({"from": old, "to": new, "photos": len(got)})
            folder_edits.append(store.move_edit(folder_id, old, paths.stored(new)))
        moves = relink_photos.moves_with_faces(conn, pairs) if pairs else []
    finally:
        conn.close()
    edits = []
    if pairs:
        edits, occupied = relink_photos.edits_for(library, moves)
        counts["occupied"] += len(occupied)
        reveal["occupied"] = occupied
    counts["photos_moved"] = len(edits)
    setting_edits, followed = folder_moves.settings_edits_for(library, written)
    counts["settings_followed"] = len(followed)
    added_follow, added_left = folder_moves.plan_added(added, written)
    counts["added_renamed"], counts["added_left"] = len(added_follow), len(added_left)
    edits = edits + folder_edits + setting_edits
    return maintenance.Plan(size=len(edits), counts=dict(counts), reveal=reveal,
                            work={"edits": edits, "followed": written, "added_follow": added_follow})


def follow(library, places=(), trees=(), row_folders=None, apply=False, exiftool_path=None, rehearse=False):
    """Follow the marked folders that moved (see the module): a dry run unless `apply`, which
    only reads unless `rehearse`. A Result on the maintenance scaffold; details["counts"] what was
    found, ["changed"] the rows written by kind (applied), ["added_followed"] the folders added
    pointed at the new folders. `places`, `trees` and `row_folders` bound what is looked at
    (look_follow). Even a dry run reads files through ExifTool, for a folder whose photos were renamed too:
    that is how it says which photos would follow."""
    held = {}

    def plan(found):
        held["plan"] = look_follow(found, places, trees, row_folders, exiftool_path)
        return held["plan"]

    try:
        with roots_service.pinned(library):
            if apply or rehearse:
                result = maintenance.run(library, FOLLOW, plan, lambda planned: planned.work["edits"], apply=apply,
                                         kinds=FOLLOW_KINDS)
            else:
                planned = plan(library)
                result = Result(attempted=planned.size, details={
                    "dry_run": True, "change": None, "counts": dict(planned.counts), "ids": {},
                    "reveal": dict(planned.reveal)})
    except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
        result = Result()
        result.refuse(roots_service.stopped(stop))
        result.details.update(counts={}, changed={kind: 0 for kind in FOLLOW_KINDS}, dry_run=not apply)
        return result
    result.details["added_followed"] = 0
    planned = held.get("plan")
    if apply and result.ok and result.changed and planned is not None and planned.work["added_follow"]:
        followed = planned.work["added_follow"]

        def follow_added(conn):
            return sum(added_folders.follow(conn, old, new) for old, new in followed)
        try:
            result.details["added_followed"] = db.write_with_connection(library.path, follow_added)
        except Exception as e:
            result.fail("the folders added", "%s: %s" % (type(e).__name__, e))
    if apply and planned is not None:
        result.details["followed"] = list(planned.work["followed"])
    return result

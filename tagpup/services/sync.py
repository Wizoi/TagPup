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
  the file (refresh_rows.reread, edits_for);
- moved files -- a row whose file is gone, and a new file that is the same photo: the
  row follows the file, and its faces with it, as relink does (relink_photos.edits_for).
  Most rows hold no DocumentID, so a file is first matched by its name, size and
  modified time, which the walk and the rows already hold (a file moved to another folder),
  then, for the rows and files that left, by its folder, size and modified time (a file
  renamed in its own folder: a rename in Explorer keeps the modified time) (pair_by_stamp)
  -- each only where exactly one file matches the row and exactly one row the file; a match
  that is not one-to-one is reported as ambiguous, never guessed, and the folders of its
  files are not queued. A file both moved and renamed, or saved again after a rename, is
  not matched by its stamp.
  What that leaves is matched by the DocumentID (or, renamed in its folder by TagPup,
  the PreservedFileName) read from those new files alone (relink_photos.claims_of, pair);
- work the index owes -- a photo with no vector under the library's photo model, one whose vector's stamp no longer
  describes its file (photo_fingerprints.same_picture, the one rule; owner, DECISIONS 2026-10-27), whether or not its
  row does (a row an earlier sync re-read keeps the vector's old stamp), one never examined for faces by the
  library's detector, and one whose current vector's picture was never fingerprinted, whose file the index's bake
  writes from the library: photo_fingerprints.owed, the one answer the index asks too. Its folder is queued for the
  index as a new file's is (`to_check`, counted by reason: `owed_no_vector`, `owed_stale`, `owed_unexamined`,
  `owed_no_fingerprint`), and the index tells a metadata write (the same picture: the vector kept) from an edited
  picture (computed again). Sync decodes nothing and leaves the vector, the faces and the file as they are;
- missing files -- a row whose file is gone and was not found elsewhere. A sync of the
  whole library removes it (`to_remove`; owner, DECISIONS 2026-10-28: a file not looked at
  is not a file not found) when the walk looked for it and did not find it: every folder
  was looked at (nothing "not looked at", below), the row is not held back as a possible
  copy (ambiguous), and its root is confirmed there -- the first place of the root it is
  under (for a library with no roots, the topmost folder walked above it) is a folder
  (shares.folder_state) and the walk found a file of that root's rows. So an unplugged
  drive, a root gone, or a root repointed at an empty folder removes nothing, and a folder
  wholly gone under a root that is there is removed with its rows. Two looks: when the walk
  leaves a row to remove, the whole library is walked a second time, and a row is removed only
  when that walk too looked at every folder, did not find its file, paired it with no file as a
  move nor held it back, and confirmed its root -- a file dragged from a folder the first walk
  had not listed yet into one it had is in neither of that walk's listings, and is found by the
  second (#2153). A library with nothing to remove is walked once. A folder renamed in
  Explorer is paired as a move where its files' stamps or identities match; what is left
  unmatched is removed. A sync of one folder never removes: a file moved out of it may not
  have been looked for where it went. The removal is a skippable delete of the change
  (kind "removed"): the journal records the photo's faces, vectors and suggestions with
  it, so an undo puts the row back under its id; a row another sync or Remove Folder took
  meanwhile is skipped. The report also says which folders are wholly gone (on a local
  drive, connected or not, or a share that answers: shares.folder_state).
- not looked at -- a row under a folder the walk could not look at: a root on a share that
  does not answer (tagpup.files.shares.folder_state), or a folder whose listing failed or
  dropped part-way (images.stamps_under's `unlisted`). Neither missing nor paired with a
  file elsewhere, and the library is not in step (owner, 2026-10-15; #1356): a share
  asleep once read as a root gone, and an applied sync could take a copy for a move.

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
only ever read: no DocumentID is minted); applied, the re-read rows, the moved rows and the
removed rows are one change of the journal, `sync`, undoable. Then the new files' folders are queued, and
the run is recorded in the library (tagpup.store.job_runs.record_sync, a run of the job "sync-whole" or
"sync-folder"): what it found and what it
changed, and whether it left the library in step -- nothing new, changed or moved left
over, no row left to remove and no folder left unlooked at; a missing file sync cannot
confirm gone does not count against it -- which is the pages' "last in step".
"""
import collections
import contextlib
import contextvars
import logging
import numbers
import os
import threading
import time

from tagpup.core import paths, runs, validation
from tagpup.core.result import SHARE_AWAY, Result
from tagpup.files import field_values, images, shares
from tagpup.services import maintenance, refresh_rows, relink_photos, settings
from tagpup.services import job_runs as recorded_runs
from tagpup.services import roots as roots_service
from tagpup.store import db, generations, job_runs, journal, photo_fingerprints, schema
from tagpup.store import faces as store_faces
from tagpup.store import folders as store_folders
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: What the change is recorded as.
OPERATION = "sync"

#: The kinds of row the change writes: re-read from its file, following its file, or removed (its file confirmed gone).
KINDS = ("from_files", "relinked", "removed")

#: What details["changed"] holds: each kind, and the faces and named faces the removed rows took with them.
CHANGED = KINDS + ("removed_faces", "removed_named")

#: How long a sync waits to hear whether a root, or a missing row's folder, on a network share is there, in seconds:
#: a job, not a page, so longer than shares.SHARE_WAIT; one that does not answer in it is not looked at. A root's share
#: taken as away when the sync starts (the watcher's look as the server started did not answer in its moment) is waited
#: for this long to be back (shares.Walk, patient), once a share, before its roots are not looked at (#2101).
FOLDER_WAIT = 10.0

#: The phases of a sync its progress names (walked): its walk, the second walk only a removal makes, and what
#: follows the walk (the changed files read, the change written).
WALKING, WALKING_AGAIN, AFTER_THE_WALK = "walking the folders", "walking the folders again", "reading what changed"
#: Each, as the run of a sync job records it (tagpup.services.job_runs.PHASES), and back (walked; the words the Activity
#: page shows a run's phase in, tagpup.services.activity.PHASE_SAID).
_RUN_PHASES = {WALKING: "walking", WALKING_AGAIN: "walking_again", AFTER_THE_WALK: "after_the_walk"}
PHASE_SAID = {recorded: said for said, recorded in _RUN_PHASES.items()}

#: A walk logs how far it has got every LOG_FOLDERS folders and LOG_SECONDS seconds, whichever is the rarer: on the
#: share, 2,564 folders in about 12 minutes (photo_index, #1915), never a line a file.
LOG_FOLDERS, LOG_SECONDS = 50, 30

_walking_at = contextvars.ContextVar("tagpup_sync_walking_at", default=None)


@contextlib.contextmanager
def walking_at_into(at):
    """A sync run on this thread meanwhile keeps at["folder"] the folder its walk is at: None before a walk, between its
    walks and after. The one part of how far a walk got kept in memory, this process's -- the Activity page's live
    "at <folder>" (owner, 2026-10-28: live progress shows folder paths, this PC only); its phase and counts are its run's
    row (job_runs.tell; walked), which never holds a path. The folder watcher's (tagpup.jobs.watching)."""
    at["folder"] = None
    token = _walking_at.set(at)
    try:
        yield at
    finally:
        _walking_at.reset(token)


def walked(progress):
    """How far a sync's walk got as its run's row says it -- `progress`, a run's as tagpup.services.activity.under_way
    gives it ({"phase", "counts", "progressed"}), None before the run said or in a library before migration 36 --
    as the Activity page's Now reads it: {"phase" (WALKING, WALKING_AGAIN, AFTER_THE_WALK), "folders" walked, "files"
    (photo files found), "progressed" (when it last said)}, or None -- also for a progress this version does not know
    (a phase it has no name for, counts that are not the two numbers: a row a newer version wrote). Counts only: the
    folder the walk is at is the process's that walks (walking_at_into)."""
    if not isinstance(progress, dict):
        return None
    counts = progress.get("counts")
    phase = PHASE_SAID.get(progress.get("phase"))
    if phase is None or not isinstance(counts, dict):
        return None
    folders, files = counts.get("folders"), counts.get("files")
    if not all(isinstance(each, numbers.Number) and not isinstance(each, bool) for each in (folders, files)):
        return None
    return {"phase": phase, "folders": folders, "files": files, "progressed": progress.get("progressed")}


class _Walked:
    """How far one walk (_scan) has got: told each folder listed (images.stamps_under's `listed`), it keeps the folder
    it is at (walking_at_into), tells the run recorded on this thread -- the sync job's (tagpup.jobs.recurring.run_claimed)
    -- its phase, folders and files (tagpup.services.job_runs.tell: at most every 10 s, never the folder), and logs a line at most every
    LOG_FOLDERS folders and LOG_SECONDS seconds, with the run's tag (tagpup.core.runs): the folder it is at, never a line
    a file."""

    def __init__(self, phase, clock=time.monotonic):
        self.phase, self.folders, self.files, self._clock = phase, 0, 0, clock
        self._began = self._logged = clock()
        self._logged_at = 0
        self._tell(None)

    def _tell(self, folder):
        at = _walking_at.get()
        if at is not None:
            at["folder"] = folder
        recorded_runs.tell(_RUN_PHASES[self.phase], {"folders": self.folders, "files": self.files})

    def listed(self, folder, photos):
        self.folders += 1
        self.files += photos
        self._tell(folder)
        now = self._clock()
        if self.folders - self._logged_at >= LOG_FOLDERS and now - self._logged >= LOG_SECONDS:
            self._logged, self._logged_at = now, self.folders
            logger.info("Sync, %s: %d folder(s) and %d photo file(s) so far, at %s.", self.phase, self.folders,
                        self.files, folder)

    def ended(self):
        """The walk is over: said, when it was long enough to say how far it had got, and the phase after it begins."""
        if self._logged_at:
            logger.info("Sync, %s: %d folder(s) and %d photo file(s) in %d s.", self.phase, self.folders, self.files,
                        self._clock() - self._began)
        self.phase = AFTER_THE_WALK
        self._tell(None)


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
    the row describes (store.photos.describes) -- a file moved to another folder; then, for
    a row no file of its name matched and the files no row matched, when the file is in the
    row's own folder with its size and a time the row describes -- a file renamed in its
    folder (a rename in Explorer keeps the modified time, #2155). Each only when each is the
    other's one match. A row or file with more than one match is ambiguous. Reads nothing."""
    by_name, by_folder = {}, {}
    for key, (path, mtime, size) in new.items():
        by_name.setdefault((os.path.basename(path), size), []).append((key, path, mtime))
        by_folder.setdefault((paths.key(os.path.dirname(path)), size), []).append((key, path, mtime))
    pairs, ambiguous_rows, ambiguous_files, matched, claimed = _one_to_one(
        missing, by_name, lambda path, size: (os.path.basename(path), size))
    in_place = _one_to_one(
        [row for row in missing if row[1] not in matched],
        {where: [file for file in files if file[0] not in claimed] for where, files in by_folder.items()},
        lambda path, size: (paths.key(os.path.dirname(path)), size))
    return pairs + in_place[0], ambiguous_rows + in_place[1], ambiguous_files | in_place[2]


def _one_to_one(missing, files, where):
    """(pairs, ambiguous rows, ambiguous files, the rows that matched any file {path}, the files any row matched {key})
    of the `missing` rows and the `files` {where: [(key, path, mtime)]}, a row matching each file found at
    `where(path, size)` whose time it describes: pair_by_stamp's one rule of a match, for each of its two looks."""
    matches, claimed = {}, {}
    for _photo_id, path, mtime, size in missing:
        found = [(key, file_path) for key, file_path, file_mtime in files.get(where(path, size), ())
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
    return pairs, ambiguous_rows, ambiguous_files, set(matches), set(claimed)


def _missing_by_folder(missing):
    """[(folder, rows, whether it is gone)] of the missing rows' folders, the most first: on a share, gone only when
    the share answers that it is not there (shares.folder_state)."""
    by_folder = {}
    for _photo_id, path in missing:
        folder = os.path.dirname(path)
        by_folder.setdefault(paths.key(folder), [folder, 0])[1] += 1
    listed = [(folder, count, shares.folder_state(folder, FOLDER_WAIT) == "gone") for folder, count in by_folder.values()]
    return sorted(listed, key=lambda entry: (-entry[1], paths.key(entry[0])))


def _under_any(folder, folders):
    """Is `folder` one of `folders` ({key: spelling}), or under one? Its parents are asked of the keys, one each: a
    sync whose listing failed for a thousand folders asks it of every row it did not see (_unseen)."""
    current = paths.stored(folder)
    while folders:
        key = paths.key(current)
        # A share's own folder is keyed without its last separator, and is its parents' dirname with it.
        if key in folders or key.rstrip(os.sep) in folders or key + os.sep in folders:
            return True
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return False


def watch_states(folders, on_back=None):
    """{paths.key(folder): "there", "gone" or "unanswered"} of the folders the watcher watches or would (shares.folder_state,
    each within its SHARE_WAIT): what it asks before it watches under one, a share that does not answer being neither there
    nor gone. `on_back(share)` is told, once, when the share of a folder that did not answer answers again (shares.on_back):
    the watcher looks again then, not at its next RECHECK. The jobs layer asks sync, not tagpup.files (tests/test_layers.py)."""
    states = {}
    for folder in folders:
        states[paths.key(folder)] = state = shares.folder_state(folder)
        if state == "unanswered" and on_back is not None:
            shares.on_back(shares.share_of(folder), on_back)
    return states


def _scan(conn, folder, roots, library_folders, phase=WALKING):
    """What is on disk against the rows: (by_key {key: (id, path, mtime, size)} of the rows
    looked at, on_disk {key: (path, mtime, size)}, the folders walked, those not there,
    those that did not answer, and those under them that would not list).
    The whole library walks every root and every folder of the library's
    (`library_folders`, tagpup.store.folders: those with rows, and those added), each once
    from the topmost; a folder, that folder alone. Each is asked through one patient shares.Walk: a share taken as away
    as the sync starts is waited for, once, up to FOLDER_WAIT, to be back (#2101). How far it got is told as `phase`
    (_Walked)."""
    by_key = {}
    for photo_id, path, mtime, size in store_photos.stamps(conn, folder):
        by_key.setdefault(paths.key(path), (photo_id, path, mtime, size))
    walked = [paths.stored(folder)] if folder else walk_roots(set(roots) | set(library_folders.walked()))
    on_disk, gone, unanswered, unlisted = {}, [], [], []
    walk, told = shares.Walk(patient=True, wait=FOLDER_WAIT), _Walked(phase)
    for root in walked:
        state = walk.folder_state(root, FOLDER_WAIT)
        if state == "there":
            on_disk.update(images.stamps_under(root, unlisted, told.listed))
        elif state == "gone":
            gone.append(root)
        else:
            unanswered.append(root)
    told.ended()
    return by_key, on_disk, walked, gone, unanswered, unlisted


def _unseen(by_key, on_disk, not_looked_at):
    """(missing [(photo_id, path, mtime, size)], not looked at [photo_id]) of the rows whose file the walk did not
    see: not looked at when under a folder the walk could not look at (`not_looked_at`, folders)."""
    folders = {paths.key(f): f for f in not_looked_at}
    missing, unseen = [], []
    for key, (photo_id, path, mtime, size) in by_key.items():
        if key in on_disk:
            continue
        if folders and _under_any(os.path.dirname(path), folders):
            unseen.append(photo_id)
        else:
            missing.append((photo_id, path, mtime, size))
    return missing, unseen


def _removable(conn, candidates, by_key, on_disk, walked):
    """The `candidates` [(photo_id, path)] -- missing rows a whole sync that looked at every folder did not find -- whose
    root is confirmed there: the first place of the root a row is under (a library with no roots: the topmost folder
    walked above it) is a folder (shares.folder_state), and the walk found a file of that root's rows. An unplugged
    drive, a root gone, or a root repointed at an empty or wrong folder confirms nothing. A row under none of the
    library's roots is not removed (none in the three live libraries, 2026-10-28)."""
    if not candidates:
        return []
    roots = store_roots.roots_for(conn)
    firsts = {}

    def place_of(path):
        if roots.identity:
            return next((top for top in walked if paths.is_under(path, top)), None)
        located = roots.locate(path)
        if located is None:
            return None
        if located[0] not in firsts:
            firsts[located[0]] = store_roots.from_row(conn, paths.ROOT_MARK + located[0])
        return firsts[located[0]]

    wanted = {}
    for photo_id, path in candidates:
        place = place_of(path)
        if place is not None:
            wanted.setdefault(paths.key(place), (place, []))[1].append((photo_id, path))
    # A file of each root's rows found: the first one found settles its root, so the rows are read only as far as that.
    unseen = set(wanted)
    for key, (_photo_id, path, _m, _s) in by_key.items():
        if not unseen:
            break
        if key in on_disk:
            place = place_of(path)
            if place is not None:
                unseen.discard(paths.key(place))
    removable = []
    for place_key, (place, rows) in sorted(wanted.items()):
        if place_key not in unseen and shares.folder_state(place, FOLDER_WAIT) == "there":
            removable.extend(rows)
    return sorted(removable)


def _gone_again(conn, candidates, roots, library_folders, fields):
    """The `candidates` [(photo_id, path)] -- rows the first walk of a whole sync would remove -- that a second walk of
    the whole library also looked for and did not find: not on disk, not paired with a new file as a move nor held
    back as a possible copy (_pair_moves, the one pairing), and its root confirmed there by this walk (_removable). A
    file moved between two folders while the first walk ran is in neither of its listings; this walk finds it (#2153).
    None when this walk could not look at every folder."""
    by_key, on_disk, walked, _gone, unanswered, unlisted = _scan(conn, None, roots, library_folders, WALKING_AGAIN)
    if unanswered or unlisted:
        return []
    missing, _unseen_ids = _unseen(by_key, on_disk, ())
    new = {key: stamp for key, stamp in on_disk.items() if key not in by_key}
    settled = set()
    if missing and new:
        pairs, ambiguous_rows, _files = _pair_moves(conn, by_key, missing, new, fields)
        settled = {paths.key(old) for old, _new in pairs} | {paths.key(path) for path in ambiguous_rows}
    still = {photo_id for photo_id, path, _m, _s in missing if paths.key(path) not in settled}
    return _removable(conn, [(photo_id, path) for photo_id, path in candidates if photo_id in still],
                      by_key, on_disk, walked)


def _missing_elsewhere(conn, by_key, new):
    """The rows outside a folder-limited sync that a new file in it may have moved from:
    another folder's row with the new file's name and size whose file is gone, as
    [(photo_id, path, mtime, size)]. One read of the rows' stamps, and a look on disk for
    each candidate alone: a file moved in from another folder keeps its row."""
    wanted = {(os.path.basename(path), size) for path, _m, size in new.values()}
    found = []
    for photo_id, path, mtime, size in store_photos.stamps(conn):
        if ((os.path.basename(path), size) in wanted and paths.key(path) not in by_key
                and shares.stamp_of(path) is None):
            found.append((photo_id, path, mtime, size))
    return found


def _pair_moves(conn, by_key, missing, new, fields, read=True):
    """(pairs [(old, new)], ambiguous rows [path], ambiguous files {key}) of the missing
    rows [(photo_id, path, mtime, size)] and the new files: by name, size and time first,
    reading nothing; then, for what that left and only when `read`, by the identity read
    from those new files alone."""
    pairs, ambiguous_rows, ambiguous_files = pair_by_stamp(missing, new)
    settled = {paths.key(old) for old, _new in pairs} | {paths.key(new_path) for _old, new_path in pairs}
    left_rows = [path for _id, path, _m, _s in missing if paths.key(path) not in settled]
    left_files = sorted(path for key, (path, _m, _s) in new.items() if key not in settled)
    if read and left_rows and left_files:
        lookup, by_identity = relink_photos.claims_of(left_files, fields)
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


def look(library, folder, fields, roots=(), ignored=()):
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
        by_key, on_disk, walked, roots_gone, unanswered, unlisted = _scan(conn, folder, roots, library_folders)
        if (folder is not None and not by_key and not library_folders.holds(folder)
                and not _under_any(folder, {paths.key(r): r for r in roots})):
            return maintenance.Plan(refused=(
                "The library holds no photo under that folder, and it is under none of the library's root "
                "folders: sync keeps the library's folders in step. To add it, add a root folder or index it."))

        not_looked_at = unanswered + unlisted
        missing, unseen = _unseen(by_key, on_disk, not_looked_at)
        changed, never_stamped = {}, 0
        for key, (photo_id, path, mtime, size) in by_key.items():
            stamp = on_disk.get(key)
            if stamp is None:
                continue
            if not store_photos.describes(mtime, size, stamp[1:]):
                changed[path] = photo_id
                never_stamped += mtime is None or size is None
        # The work the index owes: the one answer the index asks too (photo_fingerprints.owed). Nothing decoded.
        from tagpup.services import faces as face_records   # faces reaches file_changes, which reaches settings and sync
        held_settings = settings.on(conn)
        owed = photo_fingerprints.owed(conn, settings.photo_key_on(conn),
                                       face_records.detector_named(held_settings.faces),
                                       on_disk, folder)
        to_check = {found.path: photo_id for photo_id, found in owed.items()}
        owed_by = collections.Counter(reason for found in owed.values() for reason in found.reasons)
        new = {key: stamp for key, stamp in on_disk.items() if key not in by_key}

        # Moved: a missing row whose file turns up among the new ones. A folder alone also
        # looks for the rows of files moved in from elsewhere.
        elsewhere = _missing_elsewhere(conn, by_key, new) if folder is not None and new else []
        moves, ambiguous_rows, ambiguous_files = [], [], set()
        if (missing or elsewhere) and new:
            pairs, ambiguous_rows, ambiguous_files = _pair_moves(conn, by_key, missing + elsewhere, new, fields)
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

        # Removed: a missing row the walk looked for and did not find, its root confirmed there -- only in a sync of
        # the whole library that looked at every folder (a file moved out of one folder is looked for in the others),
        # and only when a second walk, made only then, finds it gone too (_gone_again: a file moved mid-walk).
        to_remove = []
        if folder is None and not not_looked_at:
            held_rows = {paths.key(path) for path in ambiguous_rows}
            to_remove = _removable(conn, [(photo_id, path) for photo_id, path in missing
                                          if paths.key(path) not in held_rows], by_key, on_disk, walked)
            if to_remove:
                to_remove = _gone_again(conn, to_remove, roots, library_folders, fields)
        removing = {photo_id: (0, 0) for photo_id, _path in to_remove}
        removing.update(store_faces.counts_by_photo(conn, list(removing)) if removing else {})

        # Changed: re-read, each row held to what it was when read.
        found = {}
        for path in changed:
            tags, _people, captions, raw, mtime, size, _doc = store_photos.row_as_recorded(conn, path)
            found[path] = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
        records, to_write, differing, unreadable, identities = refresh_rows.reread(conn, changed, fields)
    finally:
        conn.close()

    edits = refresh_rows.edits_for(records, to_write, found, identities, changed)
    moved_edits, occupied = relink_photos.edits_for(library, moves)
    # Each row removed as the plan read it, skippable: one another sync or Remove Folder took meanwhile is left out.
    removed_edits = [journal.delete("photos", (photo_id,),
                                    {"path": path, "mtime": stamp_of[paths.key(path)][0],
                                     "size": stamp_of[paths.key(path)][1]}, kind="removed", skippable=True)
                     for photo_id, path in to_remove]
    by_folder = _missing_by_folder(missing)
    stops = {paths.key(root) for root in walked + roots}
    queued, review, ignored_files, held_back, outside = _sort_new(
        new, by_key, stops, ignored_by_key, ambiguous_files, {paths.key(r): r for r in roots}, library_folders)
    # Work the index owes is the index's, queued with its folder as a new file is; a folder held back (a file in it may
    # be a copy of a missing photo) is not indexed until that is settled.
    to_index = {paths.key(f): f for f in queued}
    held = {paths.key(f) for f in held_back}
    for path in to_check:
        to_index.setdefault(paths.key(os.path.dirname(path)), os.path.dirname(path))
    new_folders = sorted((f for key, f in to_index.items() if key not in held), key=paths.key)
    new_paths = sorted((path for path, _m, _s in new.values()
                        if paths.key(os.path.dirname(path)) in {paths.key(f) for f in queued}), key=paths.key)
    listed = [{"path": top, "photos": review[top]} for top in sorted(review, key=paths.key)]
    return maintenance.Plan(
        size=len(edits) + len(moved_edits) + len(removed_edits),
        counts={"rows": len(by_key), "files": len(on_disk),
                "folders_walked": len(walked) - len(roots_gone) - len(unanswered)
                - len({paths.key(f) for f in walked} & {paths.key(f) for f in unlisted}),
                "new": sum(queued.values()), "new_folders": len(queued),
                "review_folders": len(review), "review_photos": sum(review.values()),
                "ignored_files": ignored_files, "outside_roots_files": outside,
                "changed": len(changed), "never_stamped": never_stamped, "to_write": len(to_write),
                "to_check": len(to_check), "owed_no_vector": owed_by[photo_fingerprints.NO_VECTOR],
                "owed_stale": owed_by[photo_fingerprints.STALE], "owed_unexamined": owed_by[photo_fingerprints.UNEXAMINED],
                "owed_no_fingerprint": owed_by[photo_fingerprints.NO_FINGERPRINT],
                "owed_folders": len({paths.key(os.path.dirname(path)) for path in to_check}),
                "owed_held_back": len({paths.key(os.path.dirname(path)) for path in to_check} & held),
                "fields": dict(differing.most_common()), "unreadable": len(unreadable),
                "moved": len(moves), "moved_faces": sum(m["faces"] for m in moves),
                "moved_named": sum(m["named"] for m in moves), "moved_changed": moved_changed,
                "occupied": len(occupied), "ambiguous_rows": len(ambiguous_rows),
                "ambiguous_files": len(ambiguous_files), "held_back_folders": len(held_back),
                "missing": len(missing), "missing_folders": len(by_folder),
                "to_remove": len(removing), "to_remove_faces": sum(faces for faces, _named in removing.values()),
                "to_remove_named": sum(named for _faces, named in removing.values()),
                "folders_gone": sum(1 for _f, _n, gone in by_folder if gone), "roots_gone": len(roots_gone),
                "not_looked_at": len(unseen), "folders_not_looked_at": len(not_looked_at)},
        ids={"changed": sorted(changed.values()), "to_write": sorted(changed[p] for p in to_write),
             "unreadable": sorted(changed[p] for p in unreadable), "to_check": sorted(to_check.values()),
             "moved": sorted(id_of[paths.key(m["from"])] for m in moves),
             "missing": sorted(photo_id for photo_id, _path in missing),
             "to_remove": sorted(removing),
             "ambiguous": sorted(id_of[paths.key(path)] for path in ambiguous_rows)},
        reveal={"new": new_paths, "new_folders": new_folders, "review": listed, "moves": moves,
                "to_check": sorted(to_check, key=paths.key),
                "occupied": occupied,
                "ambiguous": {"rows": ambiguous_rows, "files": sorted(new[key][0] for key in ambiguous_files),
                              "held_back_folders": sorted(held_back, key=paths.key)},
                "missing_folders": [{"folder": f, "rows": n, "gone": gone} for f, n, gone in by_folder],
                "roots_gone": roots_gone, "not_looked_at": sorted(not_looked_at, key=paths.key)},
        work={"edits": edits + moved_edits + removed_edits, "new_folders": new_folders, "removed": removing})


def review(library, roots=(), ignored=()):
    """The folders to review: each folder under a root that holds photos and no indexed
    photo, and is not ignored, with how many photos it holds -- what sync lists and the
    page offers to include or ignore. One walk, and no file read: a file that moved there
    is told by its name, size and time alone. {"folders": [{"path", "photos"}], "photos"}."""
    roots = [paths.stored(root) for root in roots]
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        library_folders = store_folders.of(conn, [paths.stored(f) for f in ignored])
        by_key, on_disk, walked, _gone, unanswered, unlisted = _scan(conn, None, roots, library_folders)
        missing, _unseen_ids = _unseen(by_key, on_disk, unanswered + unlisted)
        new = {key: stamp for key, stamp in on_disk.items() if key not in by_key}
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
    refusal = shares.folder_refusal(folder)
    if refusal:
        return refusal
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
    # The queue's Result whole, as libraries.add hands it up: its refusal's kind (a share that stopped answering:
    # SHARE_AWAY, so the route answers 503) is the queue's to say.
    return queue([folder])


def in_step(counts, result=None):
    """Is the library in step with its folders by what a sync found (`counts`) and, once
    applied, did (`result`)? Nothing new, nothing changed, moved or to remove left unwritten, nothing
    unreadable, no picture to check, no folder it could not look at. A missing file whose root sync could not confirm
    there (an unplugged drive), or found by a sync of one folder, does not count: it is reported, and removed by the next
    whole sync that confirms it. A sync refused found nothing to say so."""
    if not counts:
        return False
    if (counts.get("new") or counts.get("unreadable") or counts.get("to_check") or counts.get("moved_changed")
            or counts.get("occupied")
            or counts.get("ambiguous_rows") or counts.get("folders_not_looked_at")):
        return False
    if result is None:
        return not (counts.get("to_write") or counts.get("moved") or counts.get("to_remove"))
    return result.ok and not result.skipped


def could_not_look(result):
    """Why a sync whose Result is ok did not do its work: the folders it could not look at (a share that did not answer,
    a folder that would not list), a Failure of the kind SHARE whose sentence names no folder, or None when it looked at
    every one. What ends the daily `sync` job's run failed, so it is tried again in an hour, not a day later
    (tagpup.jobs.recurring, #2103)."""
    folders = ((result.details or {}).get("counts") or {}).get("folders_not_looked_at") or 0
    if not folders:
        return None
    return recorded_runs.Failure(recorded_runs.SHARE, "%d folder%s could not be looked at (a share that did not answer,"
                                 " or a folder that would not list): the library is not in step."
                                 % (folders, "" if folders == 1 else "s"))


@roots_service.canonical_args("folder")
def sync(library, folder=None, apply=False, fields=None, queue=None, roots=(), ignored=()):
    """Bring `library` in step with its folders (or with `folder`), reading changed files
    through `fields` (field_values.opened; None, one of its own over the ExifTool on PATH); a dry run unless `apply`. A Result on the
    maintenance scaffold: `changed` is the rows the change changed (details["changed"]:
    re-read "from_files", "relinked", "removed", read from the writes, and "removed_faces", "removed_named": what the
    removed rows held as the plan read them, less any row skipped); details["counts"] what was
    found (see look), ["ids"] the photo ids, ["reveal"] the paths.

    `roots` and `ignored` are the library's root folders and ignored folders (its
    settings): see look.

    Applied, the folders holding new files are handed to `queue(folders)`, which returns
    the index queue's Result (tagpup.jobs.indexing.IndexQueue.start): details["queued"] is
    the folders it queued. Without a queue they are reported, not queued. Then the run is
    recorded (tagpup.store.job_runs.record_sync) -- unless it was refused or its write failed, when
    nothing is queued or recorded, or it was of one folder and found and changed nothing (worth_recording); a record that cannot be written is one of
    details["warnings"], not a failure of the write. details["in_step"] says whether the
    library is in step: for a dry run, as found; applied, after the write.

    It logs one line as it ends (_said), with its tag; a sync of the whole library one as it starts too.
    """
    began = time.time()
    started = job_runs.stamp(began)
    # Every line the sync logs carries its tag, made from what its record keeps (the
    # library and when it started): the Activity page's "Logs for this run".
    with runs.running(runs.sync_tag(library.name, started)):
        if folder is None:
            # A folder's sync writes only its end: its begin line at INFO would be one each time a folder settles,
            # and at DEBUG the file drops it, so a recorded one's log showed it ending without beginning.
            logger.info("Sync of the whole library began%s.", "" if apply else " (a dry run)")
        try:
            # One map for the whole pass: a place moved meanwhile (TagTuner's Roots) changes nothing
            # until it ends, and another process changing the library's roots stops it, cleanly.
            with roots_service.pinned(library), (field_values.opened(None) if fields is None
                                                 else contextlib.nullcontext(fields)) as fields:
                result = _sync(library, folder, apply, fields, queue, roots, ignored, started)
        except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
            result = _not_run(stop, apply)
        except Exception as e:
            _said(folder, apply, None, began, error=e)
            raise
        _said(folder, apply, result, began)
        return result


def _what_it_looks_at(folder):
    return "the whole library" if folder is None else folder


#: What a sync's closing line counts, when it found any: what it found to do, and what it could not look at or read.
SAID_FOUND = ("new", "changed", "moved", "missing", "to_remove", "unreadable", "to_check", "ambiguous_rows",
              "folders_not_looked_at")


def _said(folder, apply, result, began, error=None):
    """The sync's closing line, the one writer of it (the watcher writes none of its own): what it looked at, what it
    found, what it changed -- the rows it removed with the faces they held and how many of those were named, a removal
    nobody asked for said in the log and not only in History (DECISIONS 2026-10-28) -- and how it ended; a failed one in
    the words of tagpup.services.job_runs.failure (what the kind means, then its sentence; WARNING when the kind asks
    the owner to act). Its library is its run tag's. INFO for a sync of the whole library, one that failed and one worth
    recording (it found, changed or queued something: worth_recording's first test) or recorded, whether or not its
    record could be written; DEBUG for a folder's that found nothing worth a record (the watcher's, each time a folder
    settles)."""
    seconds = time.time() - began
    what = _what_it_looks_at(folder)
    failed = recorded_runs.failure(error=error) if error is not None else (
        recorded_runs.failure(result=result) if not result.ok else could_not_look(result))
    if failed is not None:
        means, _next, asks = recorded_runs.FAILURES[failed.kind]
        logger.log(logging.WARNING if asks else logging.INFO, "Sync of %s failed after %d s: %s %s", what, seconds,
                   means, failed.sentence)
        return
    details = result.details or {}
    counts = details.get("counts") or {}
    found = ", ".join("%s %d" % (name.replace("_", " "), counts[name]) for name in SAID_FOUND if counts.get(name))
    worth = (folder is None or details.get(RECORD) is not None or result.changed or details.get("queued")
             or any(counts.get(what) for what in FOUND_WORTH))
    level = logging.INFO if worth else logging.DEBUG
    removed = details.get("changed") or {}
    logger.log(level, "Sync of %s ended%s in %d s: looked at %d folder(s) and %d photo file(s), found %s; changed %d "
               "row(s)%s, queued %d folder(s) to index; %s.", what, "" if apply else " (a dry run)", seconds,
               counts.get("folders_walked") or 0, counts.get("files") or 0, found or "nothing to do", result.changed,
               ", removed %d row(s) with %d face(s), %d of them named" % (
                   removed["removed"], removed.get("removed_faces") or 0, removed.get("removed_named") or 0)
               if removed.get("removed") else "",
               details.get("queued") or 0, "in step" if details.get("in_step") else "not in step")


def _not_run(stop, apply):
    """A sync that did not run -- the library's roots changed under it by another process (TagPup's own work, which the
    next sync settles: waited), or this machine does not place one -- a refused Result with what the callers read of
    any, and why."""
    return not_run(roots_service.stopped(stop), apply, waited=isinstance(stop, roots_service.RootsChanged))


def not_run(why, apply, waited=False):
    """A sync that did not run, `why` said: a refused Result with what the callers read of any. `waited`: TagPup's own
    work was in the way, and the next sync settles it (job_runs.WAITED, never one of Needs you)."""
    result = Result()
    result.refuse(why)
    result.details.update(counts={}, in_step=False, changed={what: 0 for what in CHANGED}, queued=0, warnings=[],
                          dry_run=not apply)
    if waited:
        result.details[recorded_runs.FAILURE] = recorded_runs.WAITED
    return result


#: Why a new files' folder the walk found was not queued: the queue found it gone, or its share away, when asked.
GONE_BEFORE_QUEUED = "gone or away before it was queued"


def _sync(library, folder, apply, fields, queue, roots, ignored, started):
    held = {}

    def plan(found_library):
        held["plan"] = look(found_library, folder, fields, roots, ignored)
        return held["plan"]

    result = maintenance.run(library, OPERATION, plan, lambda planned: planned.work["edits"], apply=apply, kinds=KINDS)
    planned = held.get("plan")
    counts = result.details["counts"]
    if not apply:
        result.details["in_step"] = in_step(counts)
        return result

    changed = result.details.setdefault("changed", {})
    for what in CHANGED:
        changed.setdefault(what, 0)
    if result.ok and planned is not None and planned.work:
        # What the removed rows held, as the plan read it, less the rows the journal skipped (named as it names them).
        skipped = {what for what, _why in result.skipped}
        taken = [held for photo_id, held in planned.work["removed"].items()
                 if journal.row_name("photos", (photo_id,)) not in skipped]
        changed["removed_faces"] = sum(faces for faces, _named in taken)
        changed["removed_named"] = sum(named for _faces, named in taken)
    result.details["queued"] = 0
    result.details["warnings"] = []
    if not result.ok:
        # Refused, or the write failed: nothing was synced, so nothing is queued -- a
        # moved file's folder indexed before its row follows it would get a second row --
        # and nothing is recorded.
        result.details["in_step"] = False
        return result
    new_folders = list(planned.work["new_folders"]) if planned is not None and planned.work else []
    queued = 0
    if new_folders and queue is not None:
        try:
            outcome = queue(new_folders)
            queued = outcome.changed
            for what, why in outcome.skipped:
                result.skip(what, why)
            for gone in () if outcome.refused else outcome.details.get("invalid", ()):
                # Some folders were queued and these were not: said, not dropped (#2229). A refusal says it already.
                result.skip(gone, GONE_BEFORE_QUEUED)
            if outcome.refused_kind == SHARE_AWAY:
                # The share stopped answering between the walk and the queue: the new files are not indexed, and the
                # sync says so with the share's sentence, not done (its run a failure of a share: job_runs.SHARE).
                result.fail("queueing the new files' folders", outcome.refused)
                result.details[recorded_runs.FAILURE] = recorded_runs.SHARE
            elif outcome.refused:
                # The new files' folders were gone between the walk and the queue: skipped, and the next sync removes
                # their rows.
                result.skip("queueing the new files' folders", outcome.refused)
        except Exception as e:
            result.fail("queueing the new files' folders", "%s: %s" % (type(e).__name__, e))
    result.details["queued"] = queued
    result.details["in_step"] = in_step(counts, result)
    try:
        if worth_recording(library, folder is None, result.details["in_step"], counts, result.changed, queued):
            result.details[RECORD] = job_runs.record_sync(
                library.path, started, whole=folder is None, in_step=result.details["in_step"],
                found={what: n for what, n in counts.items() if isinstance(n, int)},
                changed={"rows": result.changed, **result.details["changed"], "queued_folders": queued},
                change_id=result.details.get("change"))
    except Exception as e:
        # The rows are written and the folders queued; only the record of it is missing.
        result.details["warnings"].append("The sync was not recorded (%s: %s); the next sync records its own."
                                          % (type(e).__name__, e))
    return result


#: The id of the sync's record among a Result's details: tagpup.services.job_runs.RECORD, its one spelling.
RECORD = recorded_runs.RECORD

#: What a sync of one folder can find that makes it worth a record by itself (the Activity page's "found_..." counts).
FOUND_WORTH = ("new", "missing", "moved", "changed")


def worth_recording(library, whole, in_step, counts, changed, queued):
    """Is a sync, just applied, recorded? The whole library's always. A folder's when it found (FOUND_WORTH), changed or queued
    something; when it found nothing, only when it says otherwise than the newest record of whether the library is in step
    (none yet: when it says not), so that neither "in step" nor "not in step" is left standing on an older one. A folder
    settling on nothing that the newest record already speaks for is not: the watcher syncs a folder each time one settles,
    and a record of each pushed the daily whole sync out of the old table (the Files owner's finding), and, while the index
    owed a folder its bake (not in step), 200 records of it in 28 minutes, the job's whole history (2026-10-28). A read
    that fails records."""
    if whole or changed or queued or any(counts.get(what) for what in FOUND_WORTH):
        return True
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            newest = job_runs.newest_sync(conn)
        finally:
            conn.close()
    except Exception:
        return True
    if newest is None:
        return not in_step
    return bool(newest["in_step"]) != bool(in_step)


def followed(library, notified):
    """Do `library`'s rows already describe each photo file named in `notified` (the paths a folder's notifications named) as it
    is now: there, with a row that describes it (store.photos.describes, the folder scan's rule), or not there and without a
    row? Then the folder holds nothing of theirs for a sync to follow. The one answer to "was that TagPup's own write", for
    every writer in every process -- the page, the indexer's bake and its identities, the CLI: each records the file's stamp
    in its row in the transaction that marks the file written (file_changes.write_fields; a rename moves the row with the
    file, which keeps its stamp), so a write it made is described by the time its notifications settle. A file another
    program added, changed, renamed or deleted is not, and its folder is synced. A look that cannot answer -- a share that
    does not answer, a file that cannot be read, a library that cannot be read -- is False, and the folder is synced."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = store_photos.stamps_of(conn, notified)
    finally:
        conn.close()
    for path in notified:
        stamp = shares.stamp_of(path)
        row = rows.get(paths.key(path))
        if stamp is None:
            if row is not None:
                return False    # a photo of the library's gone: deleted, or moved away
            continue
        if not isinstance(stamp, tuple) or row is None or not store_photos.describes(row[2], row[3], stamp):
            return False
    return True


#: {library key: (photos generation, the folders it holds photos in)}: watch_folders is
#: asked every 30 s, and reading every photo's path (68,387 on photo_index) each time for
#: an answer that changes only with the photos was the watcher's whole cost.
_held_folders = {}
_held_lock = threading.Lock()


def watch_folders(library, roots=()):
    """The folders the server watches for `library` (tagpup.jobs.watching): its
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
    """Did a sync of the whole of `library` that left it in step finish in the last `seconds`? (Its record is kept only
    for a sync that wrote what it found.) One that could not look at every folder -- a share that did not answer --
    does not count: the watcher's catch-up at start is skipped only for a library in step (#2109: photo_index's run 92,
    a server restarted within the hour after it would have left the library not in step until the daily job)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        finished = job_runs.last_in_step(conn)
    finally:
        conn.close()
    if not finished:
        return False
    try:
        at = job_runs.seconds(finished)
    except (TypeError, ValueError):
        return False
    return time.time() - at < seconds


def last(library):
    """{"last_run", "last_in_step"} of `library` (tagpup.store.job_runs.last_sync): counts and
    times, never a path. Reads only; a library from before migration 13 has neither."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return job_runs.last_sync(conn)
    finally:
        conn.close()

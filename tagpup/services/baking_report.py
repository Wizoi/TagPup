"""A bake of a library's photos, folder by folder (`bake-photos`; docs/PHASE6_DESIGN.md, section 9): its dry run, which
plans every photo of a library, or of the folders given, as a bake plans it and counts them -- to bake, already current,
cannot be baked and why -- writing nothing; and `apply`, which writes each folder's photos to bake as one journaled
change (tagpup.services.file_changes.write_fields). Both plan through one Walk: tagpup.services.baking's plan of each
photo, a folder at a time, each folder on a read-only connection of its own, so that a folder's plan sees what the write
of the folder before it left.

A folder's files are read (their bake fields, through one ExifTool read of the folder) only when the first photo of it
the library can plan asks for them: a folder of photos never examined, or with no vector, opens no file. A dry run
decodes a sample of the photos (SAMPLE, every Nth by photo id, the same photos each run) whole, to take its fingerprint,
when its plan reaches its file: the plan of a sampled photo compares the Fingerprint its file holds as well, and its
fingerprint is compared with the row photo_fingerprints holds of it, if any; the others are planned without decoding,
as a share is slow to read. An apply plans each photo without decoding first and decodes only a photo to bake: a photo
already current is not decoded again, so a run stopped part-way goes on from where it stopped at the cost of one read
of each folder it did.

A read ExifTool does not answer in its deadline (a share or drive gone away) stops the run at that folder: a later
folder's read would wait the deadline again, and every photo after would read as not answered.

The dry run opens the library read-only (no write lock, no migration, no journal) and only reads the files. An apply
first finishes the changes of photo files a stopped run left (file_changes.settle, the one owner), saying which; takes
one snapshot of the library before its first write (snapshots.before_a_long_write: a daily, unless today's stands);
writes in the one ExifTool session it reads in; and stops at a folder whose write is refused or that a share stopped: in
its read (nothing of the folder is written), between its plan and its write, or in its write. A Ctrl+C keeps what was
written, applied and undoable, and is counted.
"""
import collections
import dataclasses
import os

from tagpup.core import paths
from tagpup.files import bake, exiftool_session, field_values, images
from tagpup.services import baking, file_changes
from tagpup.services import snapshots as snapshot_service
from tagpup.store import db, file_journal, inspection, photo_fingerprints
from tagpup.store import roots as store_roots

#: How many photos of a run are decoded for their fingerprint.
SAMPLE = 200


@dataclasses.dataclass
class Tally:
    """What a dry run found of the photos of one folder (`folder`), or of a whole run (folder None)."""
    folder: object = None
    photos: int = 0
    to_bake: int = 0
    current: int = 0
    cannot: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: {extension: photos to bake}, lower case (".jpg").
    formats: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    #: Photos ExifTool did not answer for in its deadline (field_values.NotAnswered: a share gone away): not planned.
    not_answered: int = 0
    sampled: int = 0
    #: Of the sampled: how many photo_fingerprints holds a row of, and how many of those the decode matched.
    had_row: int = 0
    matched: int = 0

    def add(self, other):
        for name in ("photos", "to_bake", "current", "not_answered", "sampled", "had_row", "matched"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.cannot.update(other.cannot)
        self.formats.update(other.formats)

    def count(self, planned):
        """Count one photo a Walk planned (Planned)."""
        self.photos += 1
        if planned.sampled:
            self.sampled += 1
            if planned.row is not None:
                self.had_row += 1
                self.matched += (planned.row.key, bytes(planned.row.hash)) == (images.FINGERPRINT_KEY, planned.digest)
        if isinstance(planned.plan, baking.ToBake):
            self.to_bake += 1
            self.formats[os.path.splitext(planned.path)[1].lower()] += 1
        elif isinstance(planned.plan, baking.Current):
            self.current += 1
        else:
            self.cannot[planned.plan.kind] += 1


@dataclasses.dataclass
class DryRun:
    """The folders' Tallies, in path order, the whole run's, whether it was stopped before its last photo, and the folder
    whose read ExifTool did not answer in its deadline, which ended the run there (None: every read answered)."""
    folders: list
    total: Tally
    stopped: bool
    stalled: object = None


def photos_of(conn, folders=None):
    """[(id, native path)] of the library's photos, or of those under any of `folders` (any depth)."""
    if not folders:
        return list(inspection.ids_and_paths(conn))
    return [row for folder in folders for row in inspection.under(conn, folder)]


def sample_of(ids, size=SAMPLE):
    """The ids decoded for their fingerprint: every Nth of `ids` in id order, at most `size`."""
    ordered = sorted(ids)
    return set(ordered[::max(1, len(ordered) // size)][:size])


def dry_run(library, exiftool_path, detector, folders=None, stop=None, heard=None, sample=SAMPLE):
    """DryRun of a bake of `library`'s photos (under `folders`, when given). `detector`, the name the library's face
    detector records its detections under (tagpup.services.faces.detector_of); `stop`, a threading.Event the run asks
    before each folder's read and each photo; `heard(tally, number, folders)`, told each folder's Tally as it is
    done, with its number and how many folders the run has."""
    found, total, stalled = [], Tally(), None
    with field_values.session(exiftool_path) as session:
        walk = Walk(library, exiftool_session.StaysDead(session), detector, folders, stop, sample=sample)
        for number, folder in enumerate(walk, 1):
            tally = Tally(folder=folder.folder, not_answered=folder.not_answered)
            for planned in folder.photos:
                tally.count(planned)
            if tally.photos or tally.not_answered:
                found.append(tally)
                total.add(tally)
                if heard is not None:
                    heard(tally, number, len(walk))
            if folder.stalled:
                stalled = tally.folder
    return DryRun(found, total, walk.stopped, stalled)


@dataclasses.dataclass
class Baked(Tally):
    """What an apply did with the photos of one folder (or of the run, folder None): the Tally of their plan, `current`
    taking in a photo found holding the bake when its write read it, and `not_answered` a photo to bake not written
    because the share stopped answering before its write (taken out of `photos` and `to_bake`: nothing is known of its file);
    `change`, the folder's change in History (None: nothing written; a run's total leaves it None); `written`, the files
    the journal holds done; `conflicts`, photo ids changed outside since they were read; `failed`, [(photo id, the write's
    sentence)] of those not written for another reason."""
    change: object = None
    written: int = 0
    conflicts: list = dataclasses.field(default_factory=list)
    failed: list = dataclasses.field(default_factory=list)

    def add(self, other):
        super().add(other)
        self.written += other.written
        self.conflicts += other.conflicts
        self.failed += other.failed


@dataclasses.dataclass
class Applied:
    """An apply: its folders' Baked, in path order, and the whole run's; `snapshot`, the Result of the one snapshot it
    took before its first write (None: it wrote nothing; it stays when the share stops before the first write); `stopped`,
    asked to stop before a folder; `stalled`, the folder whose read or write ExifTool did not answer for in its deadline
    (or whose share was found away at its write), which ended the run; `refused`, the sentence of the
    write that refused or stopped, ending the run; `interrupted`, a Ctrl+C's: {"change", "written", "left"} of the folder
    under way (each None when it stopped before that folder's write)."""
    folders: list
    total: Baked
    snapshot: object = None
    stopped: bool = False
    stalled: object = None
    refused: object = None
    interrupted: object = None


def apply(library, exiftool_path, detector, folders=None, stop=None, heard=None, told=None):
    """Bake `library`'s photos (under `folders`, when given), one journaled change of each folder: an Applied. `stop`, a
    threading.Event asked before each folder; `heard(baked, number, folders)`, told each folder's Baked once its write is
    done; `told(sentence)`, what the run does besides: the changes it finishes first, the snapshot. A Ctrl+C is caught and
    counted (Applied.interrupted); a crash raises, its change settled by the next run, which says so."""
    told = told or (lambda said: None)
    _settle(library, exiftool_path, told)
    run = Applied([], Baked())
    under_way = None
    with field_values.session(exiftool_path) as session:
        et = exiftool_session.StaysDead(session)   # the reads and the writes: timed out once, it is not asked again
        walk = Walk(library, et, detector, folders, stop, values=True, whole_folders=True)
        try:
            for number, folder in enumerate(walk, 1):
                under_way = Baked(folder=folder.folder, not_answered=folder.not_answered)
                for planned in folder.photos:
                    under_way.count(planned)
                to_bake = [planned for planned in folder.photos if isinstance(planned.plan, baking.ToBake)]
                if folder.stalled:
                    run.stalled = folder.folder        # its read was not answered part-way: written whole or not at all
                elif to_bake:
                    if run.snapshot is None:
                        run.snapshot = _snapshot(library, told)
                    result, share_stopped = _write(library, exiftool_path, et, folder, to_bake, under_way)
                    run.refused = result.refused
                    if share_stopped:
                        run.stalled = folder.folder
                done, under_way = under_way, None
                _counted(run, done)
                if heard is not None:
                    heard(done, number, len(walk))
                if run.refused or run.stalled:
                    break
        except KeyboardInterrupt as stopped:
            # A Ctrl+C in the write carries its change; one after it, while the counts are read, finds it in the folder's.
            change = getattr(stopped, "change", None) or (under_way.change if under_way is not None else None)
            written = getattr(stopped, "written", None)
            if written is None and change:
                written = sum(1 for row in file_journal.files_of(library.path, change) if row.state == "done")
            run.interrupted = {"change": change, "written": written, "left": getattr(stopped, "left", None)}
            if under_way is not None:
                under_way.change = run.interrupted["change"]
                under_way.written = run.interrupted["written"] or 0
                _counted(run, under_way)
        run.stopped = walk.stopped
    return run


def _counted(run, baked):
    if baked.photos or baked.not_answered:
        run.folders.append(baked)
        run.total.add(baked)


def _settle(library, exiftool_path, told):
    """Finish the changes of photo files a run that stopped left unfinished (#1924), before planning: the one owner,
    file_changes.settle, settles them by what each file holds, and the run says which it finished and which it left. A
    change another program still running carries out is its own (file_journal.owner_alive), and is left to it."""
    found = file_journal.unfinished(library.path)
    if not found:
        return
    left_behind = []
    for change in found:
        if change.owner and file_journal.owner_alive(change.owner):
            told("Change %d (%s) is being written by another program still running: it is left to it."
                 % (change.id, change.operation))
            continue
        told("Finishing change %d (%s), left unfinished by a run that stopped." % (change.id, change.operation))
        left_behind.append(change)
    if not left_behind:
        return
    file_changes.settle(library, exiftool_path)
    still = {change.id for change in file_journal.unfinished(library.path)}
    for change in left_behind:
        if change.id in still:
            told("Change %d is left unfinished; the log says why, and the next run tries again." % change.id)
        else:
            told("Change %d is finished." % change.id)


def _snapshot(library, told):
    """The one snapshot of the run, before its first write: a daily, unless today's stands. A snapshot that fails raises:
    nothing is written without it."""
    taken = snapshot_service.before_a_long_write(library)
    if taken.details.get("today"):
        told("Today's snapshot of the library (%s) stands; none was taken." % taken.details["today"])
    else:
        told("Took a snapshot of the library (%s) before the first write." % ", ".join(taken.details.get("taken", [])))
    return taken


def _write(library, exiftool_path, et, folder, to_bake, baked):
    """Write the photos of `folder` to bake as one change (file_changes.write_fields, in `et`, a StaysDead), and count in
    `baked` what the journal holds of it after: (the write's Result, whether the share stopped answering). Each file is
    read again before its write: one changed outside since the plan is a conflict, and one found holding the bake
    already is current. The share stopped when the write says so, when its read or write timed out (`et` dead), or when a
    photo's file was found away (file_changes.AWAY_WHY): every photo to bake it did not write is then not answered."""
    values = {paths.key(planned.path): planned.plan.values for planned in to_bake}
    ids = {paths.key(planned.path): planned.photo_id for planned in to_bake}
    result = file_changes.write_fields(
        library, baking.OPERATION, exiftool_path, [planned.path for planned in to_bake], list(bake.WRITTEN.values()),
        lambda path, held: file_changes.Plan(after=values[paths.key(path)]), summary={"folder": folder.stored}, et=et)
    baked.change = result.details.get("change")
    rows = file_journal.files_of(library.path, baked.change) if baked.change else []
    in_change = {paths.key(row.path) for row in rows}
    baked.written = sum(1 for row in rows if row.state == "done")
    done = {paths.key(row.path) for row in rows if row.state == "done"}
    conflicted = {paths.key(row.path) for row in rows if row.state == "conflict"}
    baked.conflicts = sorted(ids[key] for key in conflicted if key in ids)
    current = {paths.key(path) for path in result.details.get("written", {})} - in_change
    for planned in to_bake:
        if paths.key(planned.path) in current:   # held the bake when its write read it: current, not baked
            _not_to_bake(baked, planned.path)
            baked.current += 1
    share_stopped = bool((result.details.get("stopped") or {}).get("share")) or et.dead or any(
        why == file_changes.AWAY_WHY for _path, why in result.errors)
    unanswered = set()
    if share_stopped:
        unanswered = {key for key in ids if key not in done | conflicted | current}
        for planned in to_bake:
            if paths.key(planned.path) in unanswered:
                _not_to_bake(baked, planned.path)
                baked.photos -= 1
                baked.not_answered += 1
    baked.failed = sorted((ids[paths.key(path)], why) for path, why in result.errors
                          if paths.key(path) in ids and paths.key(path) not in conflicted | unanswered)
    return result, share_stopped


def _not_to_bake(baked, path):
    """Take the photo at `path` out of `baked`'s photos to bake and their formats (no format left at none)."""
    baked.to_bake -= 1
    ext = os.path.splitext(path)[1].lower()
    baked.formats[ext] -= 1
    if not baked.formats[ext]:
        del baked.formats[ext]


#: One photo a Walk planned: its plan (baking.ToBake, Current or Cannot), and the digest of its upright decode (None: not
#: decoded); `sampled`, decoded for a dry run's sample, and then `row` the Fingerprint photo_fingerprints held of it.
Planned = collections.namedtuple("Planned", "photo_id path plan digest row sampled")


@dataclasses.dataclass
class FolderPlan:
    """The photos of one folder (`folder`, native; `stored`, as the library holds it) a Walk planned, in path order
    (Planned), and how many ExifTool did not answer for (not planned); `stalled`, its read was not answered in ExifTool's
    deadline, and the walk ends with it."""
    folder: str
    stored: str
    photos: list
    not_answered: int = 0
    stalled: bool = False


class Walk:
    """The one plan of a bake, folder by folder: iterated, a FolderPlan of each folder of `library`'s photos (under
    `folders`, when given), in path order, its files read in `et` (one session for the run, kept dead once it times out).

    `stop` is asked before each folder, and also before each photo and during a folder's read unless `whole_folders`
    (an apply's: a folder is planned and written whole or not at all); `stopped` is set when it ended the walk. With
    `values` (an apply's), every photo to bake is planned with its decode, so its ToBake holds the values to write; else a
    dry run's `sample` of the photos is decoded. Each folder is planned on a read-only connection of its own, opened after
    the folder before it was handed on: its plan sees what that folder's write left."""

    def __init__(self, library, et, detector, folders=None, stop=None, sample=SAMPLE, values=False, whole_folders=False):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            self.keys = baking.keys(conn, detector)
            rows = photos_of(conn, folders)
        finally:
            conn.close()
        self.library, self.et, self.stop, self.values, self.whole_folders = library, et, stop, values, whole_folders
        self.sampled = set() if values else sample_of([photo_id for photo_id, _path in rows], sample)
        by_folder = collections.defaultdict(list)
        for photo_id, path in rows:
            by_folder[paths.key(os.path.dirname(path))].append((photo_id, path))
        self.groups = sorted(by_folder.values(), key=lambda group: store_roots.path_order(os.path.dirname(group[0][1])))
        self.stopped = False

    def __len__(self):
        return len(self.groups)

    def _asked(self):
        return self.stop is not None and self.stop.is_set()

    def __iter__(self):
        for group in self.groups:
            if self._asked():
                self.stopped = True
                return
            folder = self._folder(group)
            yield folder
            if folder.stalled or self.stopped:
                return

    def _folder(self, group):
        name = os.path.dirname(group[0][1])
        read = _FolderRead(self.et, [path for _id, path in group], None if self.whole_folders else self.stop)
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            folder = FolderPlan(name, store_roots.to_row(conn, name), [])
            for photo_id, path in group:
                if not self.whole_folders and self._asked():
                    self.stopped = True
                    break
                self._photo(conn, folder, read, photo_id, path)
        finally:
            conn.close()
        folder.stalled = read.stalled
        return folder

    def _photo(self, conn, folder, read, photo_id, path):
        """Plan the photo at `path` into `folder`. A photo whose file ExifTool did not answer for is counted apart, not
        as unreadable: nothing is known of its file, not even that it cannot be read. A photo is decoded only when its
        plan asks (plan_photo, after the library's checks and the file's read)."""
        given, decoded, shaped = [], [], []

        def held():
            given.append(read.of(path))
            return given[-1]

        def shape():
            if not shaped:
                width, height, _oriented, turn = images.shown_shape(path)
                shaped.append((width, height, turn))
            return shaped[0]

        def fingerprint():
            decoded.append(images.fingerprint(images.opened(path, upright=True)))
            return decoded[-1]
        sampled = photo_id in self.sampled
        plan = baking.plan_photo(conn, path, self.keys, held, shape, fingerprint if sampled else None)
        if self.values and isinstance(plan, baking.ToBake):
            plan = baking.plan_photo(conn, path, self.keys, held, shape, fingerprint)
        if given and isinstance(given[-1], _Stopped):
            return
        if isinstance(plan, baking.Cannot) and given and isinstance(given[-1], field_values.NotAnswered):
            folder.not_answered += 1
            return
        digest = decoded[-1] if decoded else None
        row = photo_fingerprints.get(conn, photo_id) if sampled and decoded else None
        folder.photos.append(Planned(photo_id, path, plan, digest, row, sampled and bool(decoded)))


class _Stopped(Exception):
    """The run was asked to stop while a folder's files were read: a photo the read did not reach was not asked of the share,
    and is counted nowhere."""


class _FolderRead:
    """The bake fields of a folder's files, read in one ExifTool read the first time a photo of it asks."""

    def __init__(self, et, photo_paths, stop):
        self.et, self.photo_paths, self.stop, self.found = et, photo_paths, stop, None

    def of(self, photo_path):
        """What field_values.read gave for `photo_path`: its fields, or the Exception its read gave (NotAnswered for a
        photo the read did not reach, _Stopped for one a stop left unread)."""
        if self.found is None:
            self.found = field_values.read(self.et, self.photo_paths, list(bake.WRITTEN.values()), stop=self.stop)
        held = self.found.get(paths.key(photo_path))
        if held:
            return held
        if self.stop is not None and self.stop.is_set():
            return _Stopped("Its file was not read: the run was stopped.")
        return field_values.NotAnswered("Its file was not read.")

    @property
    def stalled(self):
        """Did ExifTool not answer the read in its deadline (field_values.read's NotAnswered)?"""
        return any(isinstance(held, field_values.NotAnswered) for held in (self.found or {}).values())

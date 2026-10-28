"""The bake the index writes on its own (docs/PLAN_KEEP_IN_STEP.md, cut 2): after an index has recorded its batches and
let the graphics card go, every photo of each folder it recorded a photo of that tagpup.services.baking.plan_photo marks
ToBake is written, the folder as one change of file_changes.write_fields, the one writer of photo files.

A bake is the index's values stamped on the file, never an operation the owner sees or undoes (owner, decision 2): its
change is internal (file_journal.INTERNAL), the record a crash needs while the write is under way -- the next start
settles a half-written file by what it holds -- and erased once the folder is finished. Where the index's rows and the
file's bake differ the rows win (decision 3): a face deleted in TagPup is gone from the file at the folder's next index.

A folder is planned twice, on a read-only connection of its own: first every photo without its decode (the bake fields
of the folder's files read in one ExifTool read, the first time a photo asks), then only the photos to bake with their
fingerprint, the index's own decode's when it handed one over (`handed`) and the file is still at the stamp the index
recorded it under, so a photo it decoded is not decoded again; a file changed since is decoded. Its files are looked at
through one patient walk (baking.walk): a share slow to wake is waited for once, and only one that stays away has its
photos counted not answered -- nothing is called unreachable, and the next index bakes them.
Each file is read again just before its write (write_fields with no `held`): one changed outside since is a conflict,
never overwritten. A read or a write ExifTool does not answer in its deadline (a share gone away) ends the run at that
folder; the folders after it are baked by the next index. One snapshot of the library is taken before the first write
(snapshots.before_a_long_write: none when today's stands); if it fails nothing is written.

A photo with no fingerprint row (photo_fingerprints.NO_FINGERPRINT: what sync owes the index for a current vector whose
picture was never fingerprinted) is planned with its decode however its file reads. The one rule of its row (_adopt): it
is recorded only for a file that holds the library's current bake, or whose write no retry changes. A written file's
follows its write (baking.follower); one whose file was planned holding a current bake already, or whose write failed
on a file marked read-only (file_changes' `read_only`), is given the decode's (photo_fingerprints.adopt), so sync does
not queue its folder again for a write that cannot change. Every other photo keeps none and the next sync owes its
folder to the index again: a write that failed otherwise (a file another program holds open; a packet ExifTool refuses
even with -m, which has no live instance since DECISIONS 2026-10-27 and is retried at each sync), a conflict, a photo
not answered (a share that stopped answering while its folder was read or written), or not reached.

A run that ends early -- asked to stop, a share that stopped answering, the roots changed, or an exception (a Ctrl+C
included) -- forgets the rows the index recorded this run (`handed`) of the photos of the folders it did not finish and
did not write (_owe_again): left, they would keep a new photo's unbaked file from being owed until something else in its
folder changed. A process killed or a power cut runs nothing: its photos stay unbaked until their folder's next index
(by design; no live instance).
"""
import collections
import dataclasses
import logging
import os

from tagpup.core import paths
from tagpup.files import bake, exiftool_session, field_values, images, shares
from tagpup.services import baking, file_changes, settings
from tagpup.services import snapshots as snapshot_service
from tagpup.store import db, inspection, photo_fingerprints
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: The start of each line the index's bake says (Baked.said, and the CLI's when it stops): the index queue keeps it in
#: the job's message (tagpup.services.indexing).
SAID = "Bake: "

#: A folder whose write the library's roots, changed by another process, stopped (the run ends there).
ROOTS_CHANGED = "the library's roots were changed by another process"


@dataclasses.dataclass
class Baked:
    """What a run did, counted from what each write reported: photos planned to bake, files written, found holding
    their bake already (planned or at the write's read), conflicts, failed, not answered (a share away), and the photos
    the library cannot plan, by baking's kind of Cannot; `refused`, the folders whose write was refused, by its sentence
    (counts only: too many photos, a batch ahead, the roots changed); `stalled`, a share stopped answering and ended the
    run; `roots_changed`, the library's roots were changed by another process and ended it."""
    planned: int = 0
    written: int = 0
    current: int = 0
    adopted: int = 0
    conflicts: int = 0
    failed: int = 0
    not_answered: int = 0
    cannot: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    refused: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    stalled: bool = False
    roots_changed: bool = False

    def said(self):
        """The run in one line, counts only (the paths name people)."""
        line = SAID + "%d file(s) written, %d already held it" % (self.written, self.current)
        more = [("%d fingerprint(s) recorded with no write" % self.adopted) if self.adopted else "",
                ("%d conflict(s), left as they are" % self.conflicts) if self.conflicts else "",
                ("%d failed" % self.failed) if self.failed else "",
                ("%d not answered (a share away)" % self.not_answered) if self.not_answered else "",
                ", ".join("%d %s" % (n, kind) for kind, n in sorted(self.cannot.items())),
                ", ".join("%d folder(s) refused: %s" % (n, why.rstrip(".")) for why, n in sorted(self.refused.items()))]
        line += "".join("; " + each for each in more if each)
        if self.stalled:
            line += "; ended at a folder whose share stopped answering, the rest are baked by the next index"
        if self.roots_changed:
            line += "; ended where the library's roots changed, the rest are baked by the next index"
        return line + "."


def bake_folders(library, exiftool_path, detector, folders, handed, stop=None, told=None):
    """Bake the photos of each of `folders` (native folder paths, the photos directly in each) that plan ToBake, a
    folder at a time: Baked. `detector`, the name the library's face detector records its looks under
    (tagpup.services.faces.detector_named); `handed`, {paths.key(path): ((mtime, size), digest of the upright decode)}
    the index took of the photos it recorded, with the stamp it recorded each under; `stop()`, asked before each folder; `told(line)`, a line of what the run did.
    A run that ends early forgets the rows of `handed`'s photos it did not finish (_owe_again)."""
    baked, snapshot, walk = Baked(), [], baking.walk(stop)
    # The folders not finished, the first the one under way; `under_way`, its photos still to bake once it is planned.
    unfinished, under_way = list(folders), None
    try:
        with field_values.session(exiftool_path) as session:
            et = exiftool_session.StaysDead(session)
            for folder in folders:
                under_way = None
                if stop is not None and stop():
                    break
                to_bake, stored, unfingerprinted = _planned(library, et, detector, folder, handed, baked, walk)
                under_way, written = set(to_bake), None
                if to_bake and not baked.stalled:
                    if not snapshot:
                        snapshot.append(snapshot_service.before_a_long_write(library))
                    written = _write(library, exiftool_path, et, stored, to_bake, baked)
                    under_way -= {paths.key(path) for path in written.details.get("written", {})}
                _adopt(library, unfingerprinted, to_bake, written, baked)
                if baked.stalled or et.dead:
                    baked.stalled = True
                    break
                if baked.roots_changed:
                    break
                unfinished.pop(0)
    except (Exception, KeyboardInterrupt):
        try:
            _owe_again(library, unfinished, under_way, handed)
        except Exception:
            logger.exception("The rows of the photos the bake did not reach were not forgotten")
        raise
    _owe_again(library, unfinished, under_way, handed)
    if told is not None:
        told(baked.said())
    return baked


def _owe_again(library, unfinished, under_way, handed):
    """Forget the fingerprint rows the index recorded this run (`handed`'s photos) in the folders of `unfinished` that
    the bake did not write: of the first, the one under way, those of `under_way` (its photos to bake not written) or,
    not planned yet (None), all of them; of the rest, all. sync then owes them (NO_FINGERPRINT) and the next index bakes
    them, a decode each. Deleting a row only makes a photo owed: a writer racing it loses nothing."""
    if not unfinished or not handed:
        return
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        ids = []
        for n, folder in enumerate(unfinished):
            for photo_id, path in inspection.under(conn, folder):
                key = paths.key(path)
                if paths.key(os.path.dirname(path)) == paths.key(folder) and key in handed and (
                        n or under_way is None or key in under_way):
                    ids.append(photo_id)
    finally:
        conn.close()
    if ids:
        db.write_with_connection(library.path, lambda conn: photo_fingerprints.forget_photos(conn, ids),
                                 label="the fingerprints of the photos a bake that ended early did not reach")


def _planned(library, et, detector, folder, handed, baked, walk):
    """({paths.key(path): (path, values)} of the photos of `folder` to bake, the folder as the library stores it, and
    {paths.key(path): (photo id, _Decode)} of those planned that have no fingerprint row, current or to bake), counting
    into `baked` the photos current, not answered and that cannot be planned."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        keys = baking.keys(conn, detector)
        stored = store_roots.to_row(conn, folder)
        group = [(photo_id, path) for photo_id, path in inspection.under(conn, folder)
                 if paths.key(os.path.dirname(path)) == paths.key(folder)]
        fingerprinted = photo_fingerprints.fingerprinted(conn, [photo_id for photo_id, _path in group])
        read = baking.FolderRead(et, [path for _id, path in group])
        to_bake, unfingerprinted = {}, {}
        for photo_id, path in group:
            photo, decode = baking.PhotoAt(path, read, walk), _Decode(path, handed)
            plan = photo.plan(conn, keys)
            if isinstance(plan, baking.ToBake) or (isinstance(plan, baking.Current) and photo_id not in fingerprinted):
                plan = photo.plan(conn, keys, decode)
            if photo_id not in fingerprinted and decode.digest is not None and isinstance(
                    plan, (baking.ToBake, baking.Current)):
                unfingerprinted[paths.key(path)] = (photo_id, decode)
            if isinstance(plan, baking.ToBake):
                to_bake[paths.key(path)] = (path, plan.values)
            elif isinstance(plan, baking.Current):
                baked.current += 1
            elif photo.not_answered:
                baked.not_answered += 1
            else:
                baked.cannot[plan.kind] += 1
    finally:
        conn.close()
    baked.stalled = baked.stalled or read.stalled
    return to_bake, stored, unfingerprinted


class _Decode:
    """The digest of the photo's upright decode, as plan_photo asks it: the index's, handed over, while the file is at
    the stamp the index recorded it under (a file changed since holds another picture, perhaps); or decoded now. Keeps
    the digest and the stamp of the file it was taken of (`digest`, `stamp`; None until asked)."""

    def __init__(self, path, handed):
        self.path, self.handed, self.digest, self.stamp = path, handed, None, None

    def __call__(self):
        stamp, digest = self.handed.get(paths.key(self.path), (None, None))
        now = shares.stamp_of(self.path)
        if digest is None or now != stamp:
            digest = images.fingerprint(images.opened(self.path, upright=True))
        self.stamp, self.digest = now, digest
        return digest


def _adopt(library, unfingerprinted, to_bake, written, baked):
    """Give its decode's fingerprint row (photo_fingerprints.adopt) to each photo of `unfingerprinted` (_planned's) whose
    file holds the library's current bake -- planned Current, not in `to_bake` -- or whose write failed on a file marked
    read-only (`written`, the folder's write's Result, file_changes' `read_only`; None when no write ran). Nothing else:
    a written file's row followed its write, and a photo not written for any other reason stays owed. Counted in
    `baked.adopted`."""
    read_only = {paths.key(path) for path in (written.details.get("read_only", []) if written is not None else [])}
    unfingerprinted = {key: found for key, found in unfingerprinted.items() if key not in to_bake or key in read_only}
    # A file whose look did not answer when it was decoded has no stamp to hold the vector's against: no row.
    unfingerprinted = {key: found for key, found in unfingerprinted.items() if isinstance(found[1].stamp, tuple)}
    if not unfingerprinted:
        return

    def adopt(conn):
        photo_key = settings.photo_key_on(conn)
        return sum(photo_fingerprints.adopt(conn, photo_id, photo_key, decode.stamp,
                                            (images.FINGERPRINT_KEY, decode.digest))
                   for photo_id, decode in unfingerprinted.values())
    baked.adopted += db.write_with_connection(library.path, adopt, label="the fingerprints of the photos whose files hold their bake")


def _write(library, exiftool_path, et, stored, to_bake, baked):
    """Write the photos of one folder to bake as one internal change, each file read again before its write, and count
    in `baked` what the write reported (its Result: the change is erased once it finishes)."""
    result = file_changes.write_fields(
        library, baking.INDEX_OPERATION, exiftool_path, [path for path, _values in to_bake.values()],
        list(bake.WRITTEN.values()), lambda path, _held: file_changes.Plan(after=to_bake[paths.key(path)][1]),
        summary={"folder": stored}, et=et)
    unanswered = {paths.key(path) for path in result.details.get("not_answered", [])}
    conflicts = len(result.details.get("conflicts", []))
    baked.planned += len(to_bake)
    baked.written += result.changed
    baked.current += len(result.details.get("written", {})) - result.changed
    baked.conflicts += conflicts
    baked.not_answered += len(unanswered)
    baked.failed += max(0, len({paths.key(path) for path, _why in result.errors} - unanswered) - conflicts)
    if (result.details.get("stopped") or {}).get("share"):
        baked.stalled = True
    elif result.details.get("roots_stopped"):
        baked.roots_changed = True
        baked.refused[ROOTS_CHANGED] += 1
    elif result.refused:
        baked.refused[result.refused] += 1     # its sentences give counts, never a path (file_changes.too_many, batches.claim)
    return result

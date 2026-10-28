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
recorded it under, so a photo it decoded is not decoded again; a file changed since is decoded.
Each file is read again just before its write (write_fields with no `held`): one changed outside since is a conflict,
never overwritten. A read or a write ExifTool does not answer in its deadline (a share gone away) ends the run at that
folder; the folders after it are baked by the next index. One snapshot of the library is taken before the first write
(snapshots.before_a_long_write: none when today's stands); if it fails nothing is written.
"""
import collections
import dataclasses
import os

from tagpup.core import paths
from tagpup.files import bake, exiftool_session, field_values, images, shares
from tagpup.services import baking, file_changes
from tagpup.services import snapshots as snapshot_service
from tagpup.store import db, inspection
from tagpup.store import roots as store_roots


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
    conflicts: int = 0
    failed: int = 0
    not_answered: int = 0
    cannot: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    refused: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    stalled: bool = False
    roots_changed: bool = False

    def said(self):
        """The run in one line, counts only (the paths name people)."""
        line = "Bake: %d file(s) written, %d already held it" % (self.written, self.current)
        more = [("%d conflict(s), left as they are" % self.conflicts) if self.conflicts else "",
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
    the index took of the photos it recorded, with the stamp it recorded each under; `stop()`, asked before each folder; `told(line)`, a line of what the run did."""
    baked, snapshot = Baked(), []
    with field_values.session(exiftool_path) as session:
        et = exiftool_session.StaysDead(session)
        for folder in folders:
            if stop is not None and stop():
                break
            to_bake, stored = _planned(library, et, detector, folder, handed, baked)
            if to_bake and not baked.stalled:
                if not snapshot:
                    snapshot.append(snapshot_service.before_a_long_write(library))
                _write(library, exiftool_path, et, stored, to_bake, baked)
            if baked.stalled or et.dead:
                baked.stalled = True
                break
            if baked.roots_changed:
                break
    if told is not None:
        told(baked.said())
    return baked


def _planned(library, et, detector, folder, handed, baked):
    """({paths.key(path): (path, values)} of the photos of `folder` to bake, the folder as the library stores it),
    counting into `baked` the photos current, not answered and that cannot be planned."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        keys = baking.keys(conn, detector)
        stored = store_roots.to_row(conn, folder)
        group = [path for _id, path in inspection.under(conn, folder)
                 if paths.key(os.path.dirname(path)) == paths.key(folder)]
        read = baking.FolderRead(et, group)
        to_bake = {}
        for path in group:
            photo = baking.PhotoAt(path, read)
            plan = photo.plan(conn, keys)
            if isinstance(plan, baking.ToBake):
                plan = photo.plan(conn, keys, _fingerprint(path, handed))
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
    return to_bake, stored


def _fingerprint(path, handed):
    """The digest of the photo's upright decode: the index's, handed over, while the file is at the stamp the index
    recorded it under (a file changed since holds another picture, perhaps); or decoded now."""
    def fingerprint():
        stamp, digest = handed.get(paths.key(path), (None, None))
        if digest is not None and shares.stamp_of(path) == stamp:
            return digest
        return images.fingerprint(images.opened(path, upright=True))
    return fingerprint


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

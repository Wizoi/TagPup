"""A bake's dry run (`bake-photos` without --apply; docs/PHASE6_DESIGN.md, section 9): every photo of a library, or of
the folders given, planned by tagpup.services.baking as a bake would plan it, and counted -- to bake, already current,
cannot be baked and why -- folder by folder. It writes nothing: the library is opened read-only (no write lock, no
migration, no journal), and the files are only read, through field_values.session and Pillow.

A folder's files are read (their bake fields, through one ExifTool read of the folder) only when the first photo of it
the library can plan asks for them: a folder of photos never examined, or with no vector, opens no file. A sample of the
photos (SAMPLE, every Nth by photo id, the same photos each run) is decoded whole, to take its fingerprint: the plan of a sampled photo compares the Fingerprint its file holds as well, and its fingerprint is compared
with the row photo_fingerprints holds of it, if any. The others are planned without decoding, as a share is slow to read.
"""
import collections
import dataclasses
import os

from tagpup.core import paths
from tagpup.files import bake, field_values, images
from tagpup.services import baking
from tagpup.store import db, inspection, photo_fingerprints
from tagpup.store import roots as store_roots

#: How many photos of a run are decoded for their fingerprint.
SAMPLE = 200

#: What decoding a sampled photo raises for a file it cannot read: missing, locked, or not a picture Pillow decodes
#: (Pillow raises SyntaxError for some broken headers). It is then not sampled; its plan says why it cannot be baked.
_UNREADABLE = (OSError, ValueError, SyntaxError)


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


@dataclasses.dataclass
class DryRun:
    """The folders' Tallies, in path order, the whole run's, and whether it was stopped before its last photo."""
    folders: list
    total: Tally
    stopped: bool


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
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        keys = baking.keys(conn, detector)
        rows = photos_of(conn, folders)
        sampled = sample_of([photo_id for photo_id, _path in rows], sample)
        by_folder = collections.defaultdict(list)
        for photo_id, path in rows:
            by_folder[paths.key(os.path.dirname(path))].append((photo_id, path))
        groups = sorted(by_folder.values(), key=lambda group: store_roots.path_order(os.path.dirname(group[0][1])))
        found, total, stopped = [], Tally(), False
        with field_values.session(exiftool_path) as et:
            for number, group in enumerate(groups, 1):
                if stop is not None and stop.is_set():
                    stopped = True
                    break
                tally = Tally(folder=os.path.dirname(group[0][1]))
                read = _FolderRead(et, [path for _id, path in group], stop)
                for photo_id, path in group:
                    if stop is not None and stop.is_set():
                        stopped = True
                        break
                    _plan(conn, tally, keys, photo_id, path, read, photo_id in sampled)
                if tally.photos:
                    found.append(tally)
                    total.add(tally)
                    if heard is not None:
                        heard(tally, number, len(groups))
                if stopped:
                    break
        return DryRun(found, total, stopped)
    finally:
        conn.close()


class _FolderRead:
    """The bake fields of a folder's files, read in one ExifTool read the first time a photo of it asks."""

    def __init__(self, et, photo_paths, stop):
        self.et, self.photo_paths, self.stop, self.found = et, photo_paths, stop, None

    def of(self, photo_path):
        """What field_values.read gave for `photo_path`: its fields, or the Exception its read gave (NotAnswered for a
        photo the read did not reach)."""
        if self.found is None:
            self.found = field_values.read(self.et, self.photo_paths, list(bake.WRITTEN.values()), stop=self.stop)
        return self.found.get(paths.key(photo_path)) or field_values.NotAnswered("Its file was not read.")


def _shape(photo_path):
    width, height, _oriented, turn = images.shown_shape(photo_path)
    return width, height, turn


def _plan(conn, tally, keys, photo_id, path, read, in_sample):
    """Plan the photo at `path` and count it in `tally`. A photo whose file ExifTool did not answer for is counted apart,
    not as unreadable: nothing is known of its file, not even that it cannot be read."""
    digest, given = None, []
    if in_sample:
        try:
            digest = images.fingerprint(images.opened(path, upright=True))
        except _UNREADABLE:
            pass

    def held():
        given.append(read.of(path))
        return given[-1]
    plan = baking.plan_photo(conn, path, keys, held, lambda: _shape(path), digest)
    if isinstance(plan, baking.Cannot) and given and isinstance(given[-1], field_values.NotAnswered):
        tally.not_answered += 1
        return
    tally.photos += 1
    if digest is not None:
        tally.sampled += 1
        row = photo_fingerprints.get(conn, photo_id)
        if row is not None:
            tally.had_row += 1
            tally.matched += (row.key, bytes(row.hash)) == (images.FINGERPRINT_KEY, digest)
    if isinstance(plan, baking.ToBake):
        tally.to_bake += 1
        tally.formats[os.path.splitext(path)[1].lower()] += 1
    elif isinstance(plan, baking.Current):
        tally.current += 1
    else:
        tally.cannot[plan.kind] += 1

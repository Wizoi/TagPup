"""The new face model's pass over a library's photos (docs/AI_PIPELINE.md, phase 3, "The pass" and "Merge 3 brief").

The model is handed in (tagpup.ml.scrfd_adaface.ScrfdAdaFace, or a test's stand-in): `key`, `picture(path, max_side=)`,
`picture_bytes`, `detect_and_embed(picture)` and `new_faces`. For each photo it detects the faces again, and

- each old face, named or not, excluded or not, takes the new detection it overlaps most, at SAME_FACE or more, one to one,
  the named faces first (tagpup.core.clustering.pairs; owner, 2026-10-18: a named face beside an unnamed duplicate of it
  takes the detection though the duplicate overlaps it more): it keeps its person, box, crop and decisions, and is given a
  vector under the model's key;
- an old face no detection overlaps is listed (photo id, face id) for review, and nothing of it changes;
- a detection no old face takes is a new face, if the model makes one of it (its `new_faces`, as the index and Suggest
  make theirs: tagpup.ml.scrfd_adaface.NEW_FACE_FLOOR, the crop cut from the picture as stored by its box): unnamed, not
  excluded, its vector under the model's key alone, so every reader keyed by the library's model (FaceNet's until the
  switch) leaves it out (tagpup.store.faces.WITH_VECTOR);
- a detection no old face takes that the model makes no face of (under the floor) is not made, and is counted (`too_small`).

A photo that holds no face, and that the model finds no face in or only detections under the floor (`none_found`), is
recorded as looked at: its `faces_detected` row, under the name the model's detections are recorded under
(tagpup.services.faces.detector_of, as the index and Suggest record theirs and Suggest and a bake read it), with the
number of faces it holds after the floor (0 here: a count with no face row is what `forget_faceless` deletes). Not journaled, as the index's and Suggest's are not: it is a fact of the photo,
which no undo of a change makes untrue; and no change of the journal is opened for it, nor a snapshot taken. Written in
the photo's own transaction, as its faces are, and read again there the same way. It replaces the row of another detector
(one row a photo): the library's MTCNN record of no face, which a library on the new model no longer reads.

A photo is done when one of its faces holds a vector under the key (tagpup.store.faces.vectors_under), or when it holds
no face and the model's row says it was looked at (tagpup.store.faces_detected.faceless_under): a run resumes by what is
held, and a second run writes nothing and looks at nothing. A photo with old faces that the model takes none of and makes
none beside is not recorded: its old faces are listed, and it is looked at again next run. A turned photo
(tagpup.services.photos.box_shape, DECISIONS 2026-10-16: its boxes are not converted) is skipped; a photo whose picture
does not decode or whose model fails is failed. Both are counted, and nothing of them is written, a row of it neither.

Each photo is one transaction (BEGIN IMMEDIATE), in which the photo is read again: its row still there at the path listed,
still not done, its faces as they were read; else nothing is written and it is failed for this run (a row sync removed or
moved meanwhile is not made again at the path listed: a new face's writer makes a row it does not find). Each folder is one journaled change, opened at its first
photo that writes, "face pass (<key>)": the operation History and the MCP `history` show without reveal names no path. The
folder is the summary's `folder`, as the library holds it (store.roots.to_row), behind reveal as every summary's folder is
(tagpup.services.journal.history), and is what a run reads to name the changes a folder holds (`Folder.held`). The
summary's counts are added to in each photo's transaction, so it says what was committed. A change undone, or being undone, while the run goes on is not added to: the folder's next photo opens a new
one. A folder interrupted and resumed is so two changes of one folder, each undone alone.

A dry run (the default) reads all the same and counts all the same, and writes nothing.

The photos are read ahead as the index reads them (tagpup.core.read_ahead): `reading` says how many threads, how many
photos ahead and within how many bytes (the CLI gives the index's), the model's `picture` made on those threads and its
`detect_and_embed`, the graphics card's half, on the caller's, one photo after the other, as every write is. `stop`, asked
before each photo, ends the run there: the photo before it was committed or not begun, never half written
(the CLI's Ctrl+C), and the folder is marked `stopped`.
"""
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from tagpup.core import clustering, paths, read_ahead
from tagpup.files import images
from tagpup.services import faces as face_service
from tagpup.services import photos as photo_service
from tagpup.store import db, faces_detected, journal
from tagpup.store import embeddings as store_embeddings
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

#: The name of a folder's change: the model's key. The folder is in its summary.
OPERATION = "face pass (%s)"

#: Reading one photo at a time: the next one is not read before the model is done with this one.
ONE_AT_A_TIME = {"threads": 1, "ahead": 0, "budget": 0}

#: Why a photo nothing was written for failed.
UNREADABLE = "the picture did not decode or the model failed"
CHANGED = "its row or its faces changed while it was read, or another run did it: looked at again next run"


def _never():
    return False


@dataclass
class Folder:
    """What the pass did, or a dry run would do, in one folder (the photos directly in it)."""
    folder: str
    #: Old faces given a vector, and new faces made; detections no old face took, under the model's floor: not made.
    matched: int = 0
    new: int = 0
    too_small: int = 0
    #: (photo id, face id) of each old face no detection took: listed for review, untouched.
    unmatched: List[Tuple[int, int]] = field(default_factory=list)
    #: Photos the model was asked about; turned (skipped); already done (a face holds the model's vector, or the photo
    #: holds no face and the model's row says it was looked at); holding no face and in which the model found none, or
    #: only detections under the floor (recorded as looked at with `apply`: done next run).
    looked: int = 0
    skipped: int = 0
    done: int = 0
    none_found: int = 0
    #: (photo id, why) of each photo nothing was written for that the model was asked about.
    failed: List[Tuple[int, str]] = field(default_factory=list)
    #: The changes this run wrote, and those of earlier runs of this folder that stand.
    changes: List[int] = field(default_factory=list)
    held: List[int] = field(default_factory=list)
    #: The run was stopped in this folder (`stop`): the photos after the one it stopped before were not looked at.
    stopped: bool = False


def _folders(photos):
    """[(folder, [(photo id, path)])] of `photos` by the folder each is directly in, each in path order."""
    grouped = {}
    for photo_id, path in sorted(photos, key=lambda each: paths.key(each[1])):
        folder = os.path.dirname(path)
        grouped.setdefault(paths.key(folder), (folder, []))[1].append((photo_id, path))
    return [grouped[key] for key in sorted(grouped)]


def run(db_path, model, folder, apply=False, reading=ONE_AT_A_TIME, stop=_never, before_first_write=_never):
    """The pass over the photos under `folder`, at any depth, of the library at `db_path`, with `model`: [Folder], one a
    folder holding photos, as far as the run went (`stop`). Writes only with `apply`; `before_first_write` is called
    before each photo's write and acts on the first (the CLI's snapshot), so a run that writes nothing takes none."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        # The changes the folders hold, read once a run: each folder takes those whose summary names it, in row form.
        held = journal.kept_named(conn, OPERATION % model.key)
        found = []
        for name, photos in _folders(store_faces.pass_photos(conn, folder)):
            if stop():
                break
            found.append(_folder(db_path, conn, model, name, photos, apply, held, reading, stop, before_first_write))
            if found[-1].stopped:
                break
        return found
    finally:
        conn.close()


def _folder(db_path, conn, model, name, photos, apply, held, reading, stop, before_first_write):
    found = Folder(name)
    operation = OPERATION % model.key
    here = paths.key(store_roots.to_row(conn, name))
    found.held = [change_id for change_id, summary in held if paths.key(summary.get("folder")) == here]
    detector = face_service.detector_of(model, None)
    ids = [photo_id for photo_id, _path in photos]
    done = set(store_faces.vectors_under(conn, model.key, ids)) | faces_detected.faceless_under(conn, detector, ids)
    passed = {}

    def begin(photo):
        """In this thread, as a photo enters the read-ahead: None, nothing read, for one done or turned."""
        photo_id, path = photo
        if photo_id in done:
            passed[photo_id] = "done"
        elif photo_service.box_shape(path)["turned"]:
            passed[photo_id] = "skipped"
        else:
            return True
        return None

    def read(photo, _begun, reserve):
        """On a thread of the read-ahead: the photo made ready for the model, nothing on the graphics card."""
        width, height = images.picture_size(photo[1])
        # The library's cap is MTCNN's: the new model's squares are its own (ScrfdAdaFace.picture).
        reserve(model.picture_bytes(width, height, max_side=None))
        return model.picture(photo[1], max_side=None)

    change_id = None
    with read_ahead.ReadAhead(photos, begin, read, **reading) as ahead:
        for (photo_id, path), begun, result in ahead:
            if stop():
                found.stopped = True
                break
            if begun is None:
                setattr(found, passed[photo_id], getattr(found, passed[photo_id]) + 1)
                continue
            found.looked += 1
            # Read before the model is asked, and again in the write: a face deleted, added or its box moved, or the
            # photo's row removed or moved, or a face named or unnamed (a named face pairs first), meanwhile (the server
            # or a sync beside this run) leaves the photo for the next run.
            old = store_faces.boxes_of(conn, photo_id)
            try:
                picture = result.result()
            except Exception:
                # The file could not be opened (gone, or the share not answering): as a picture that does not decode.
                found.failed.append((photo_id, UNREADABLE))
                continue
            detected = model.detect_and_embed(picture) if face_service.ran(picture) else picture
            if not face_service.ran(detected):
                found.failed.append((photo_id, UNREADABLE))
                continue
            change_id = _photo(db_path, model, detector, operation, name, found, change_id, photo_id, path, old,
                               picture, detected, apply, before_first_write)
    return found


def _photo(db_path, model, detector, operation, name, found, change_id, photo_id, path, old, picture, detected, apply,
           before_first_write):
    """One photo the model found `detected` in: counted into `found`, and written with `apply`. The folder's change id."""
    paired = clustering.pairs([box for _face, box, _named in old], [each["box"] for each in detected],
                              first=[i for i, (_face, _box, named) in enumerate(old) if named])
    matched = [(old[i][0], face_service.vector_bytes(detected[j]["embedding"])) for i, j in sorted(paired.items())]
    taken = set(paired.values())
    untaken = [each for j, each in enumerate(detected) if j not in taken]
    big = model.new_faces(untaken, picture)
    new = [(each["box"], float(each["prob"]), face_service.vector_bytes(each["embedding"]), each["crop_image"])
           for each in big]
    unmatched = [(photo_id, face_id) for i, (face_id, _box, _named) in enumerate(old) if i not in paired]
    if not matched and not new:
        found.too_small += len(untaken)
        if old:
            found.unmatched += unmatched   # every old face: listed, and the photo looked at again next run
            return change_id
        if apply:
            stamp = store_embeddings.stamp_of(path)   # the file, read outside the write: a share is not asked under it
            if not db.write_with_connection(
                    db_path, lambda write: _looked(write, model.key, detector, photo_id, path, stamp, len(new)),
                    label="face pass"):
                found.failed.append((photo_id, CHANGED))
                return change_id
        found.none_found += 1
        return change_id
    if apply:
        before_first_write()
        written = db.write_with_connection(
            db_path, lambda write: _write(write, model.key, operation, name, change_id, photo_id, path, old, matched, new),
            label="face pass")
        if written is None:
            found.failed.append((photo_id, CHANGED))
            return change_id
        change_id = written
        if change_id not in found.changes:
            found.changes.append(change_id)
    found.matched += len(matched)
    found.new += len(new)
    found.too_small += len(untaken) - len(big)
    found.unmatched += unmatched
    return change_id


def _write(conn, key, operation, folder, change_id, photo_id, path, old, matched, new):
    """One photo's rows, in one transaction: the id of the folder's change they were recorded in, or None, nothing written,
    when the photo's row is gone or at another path than `path`, the photo is done, or its faces are not as `old` read
    them."""
    db.begin(conn, immediate=True)
    if not _as_read(conn, key, photo_id, path, old):
        return None
    if change_id is None or not journal.kept(conn, change_id):
        change_id = journal.record(conn, operation, [],
                                   {"folder": store_roots.to_row(conn, folder), "matched": 0, "new": 0})
    store_faces.give_vectors(conn, change_id, path, key, matched, new, {"matched": len(matched), "new": len(new)})
    return change_id


def _looked(conn, key, detector, photo_id, path, stamp, count):
    """Record, in one transaction, that `detector` looked at a photo that holds no face and made none, its file at `stamp`
    and `count` detections returned: True, or False, nothing written, when the photo is not as read (_as_read)."""
    db.begin(conn, immediate=True)
    return _as_read(conn, key, photo_id, path, []) and faces_detected.record(conn, path, detector, stamp, count) == 1


def _as_read(conn, key, photo_id, path, old):
    """Is the photo as the pass read it, in its write: its row there at `path`, not done, its faces as `old` read them?"""
    row = store_photos.thumb_row(conn, photo_id)
    if row is None or not paths.same(row.path, path):
        return False
    return not store_faces.vectors_under(conn, key, [photo_id]) and store_faces.boxes_of(conn, photo_id) == old

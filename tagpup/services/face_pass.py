"""The new face model's pass over a library's photos (docs/AI_PIPELINE.md, phase 3, "The pass" and "Merge 3 brief").

The model is handed in (tagpup.ml.scrfd_adaface.ScrfdAdaFace, or a test's stand-in): `key`, `picture(path)` and
`detect_and_embed(picture)`. For each photo it detects the faces again, and

- each old face, named or not, excluded or not, takes the new detection it overlaps most, at SAME_FACE or more, one to one,
  the named faces first (tagpup.core.clustering.pairs; owner, 2026-10-18: a named face beside an unnamed duplicate of it
  takes the detection though the duplicate overlaps it more): it keeps its person, box, crop and decisions, and is given a
  vector under the model's key;
- an old face no detection overlaps is listed (photo id, face id) for review, and nothing of it changes;
- a detection no old face takes is a new face: unnamed, not excluded, its crop cut from the picture as stored by its box
  (images.face_crop_of, as tagpup.ml.faces.FaceModel cuts one), its vector under the model's key alone, so every reader keyed
  by the library's model (FaceNet's until the gate) leaves it out (tagpup.store.faces.WITH_VECTOR).

A photo is done when one of its faces holds a vector under the key (tagpup.store.faces.vectors_under): a run resumes by
what is held, and a second run writes nothing. No `faces_detected` row is written. A photo the model finds no face in
writes nothing and is looked at again next run; a turned photo (tagpup.services.photos.box_shape, DECISIONS 2026-10-16:
its boxes are not converted) is skipped; a photo whose picture does not decode or whose model fails is failed. All three
are counted, and nothing of them is written.

Each photo is one transaction (BEGIN IMMEDIATE), in which the photo is read again: its row still there at the path listed,
still not done, its faces as they were read; else nothing is written and it is failed for this run (a row sync removed or
moved meanwhile is not made again at the path listed: a new face's writer makes a row it does not find). Each folder is one journaled change, opened at its first
photo that writes, "face pass (<key>)": the operation History and the MCP `history` show without reveal names no path. The
folder is the summary's `folder`, as the library holds it (store.roots.to_row), behind reveal as every summary's folder is
(tagpup.services.journal.history), and is what a run reads to name the changes a folder holds (`Folder.held`). The
summary's counts are added to in each photo's transaction, so it says what was committed. A change undone, or being undone, while the run goes on is not added to: the folder's next photo opens a new
one. A folder interrupted and resumed is so two changes of one folder, each undone alone.

A dry run (the default) reads all the same and counts all the same, and writes nothing.
"""
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from tagpup.core import clustering, paths
from tagpup.files import images
from tagpup.services import faces as face_service
from tagpup.services import photos as photo_service
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

#: The name of a folder's change: the model's key. The folder is in its summary.
OPERATION = "face pass (%s)"


@dataclass
class Folder:
    """What the pass did, or a dry run would do, in one folder (the photos directly in it)."""
    folder: str
    #: Old faces given a vector, and new faces made.
    matched: int = 0
    new: int = 0
    #: (photo id, face id) of each old face no detection took: listed for review, untouched.
    unmatched: List[Tuple[int, int]] = field(default_factory=list)
    #: Photos turned (skipped), already done, in which no face was found (looked at again next run).
    skipped: int = 0
    done: int = 0
    none_found: int = 0
    #: (photo id, why) of each photo nothing was written for that the model was asked about.
    failed: List[Tuple[int, str]] = field(default_factory=list)
    #: The changes this run wrote, and those of earlier runs of this folder that stand.
    changes: List[int] = field(default_factory=list)
    held: List[int] = field(default_factory=list)


def _folders(photos):
    """[(folder, [(photo id, path)])] of `photos` by the folder each is directly in, each in path order."""
    grouped = {}
    for photo_id, path in sorted(photos, key=lambda each: paths.key(each[1])):
        folder = os.path.dirname(path)
        grouped.setdefault(paths.key(folder), (folder, []))[1].append((photo_id, path))
    return [grouped[key] for key in sorted(grouped)]


def run(db_path, model, folder, apply=False):
    """The pass over the photos under `folder`, at any depth, of the library at `db_path`, with `model`: [Folder], one a
    folder holding photos. Writes only with `apply`."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return [_folder(db_path, conn, model, name, photos, apply)
                for name, photos in _folders(store_faces.pass_photos(conn, folder))]
    finally:
        conn.close()


def _folder(db_path, conn, model, name, photos, apply):
    found = Folder(name)
    operation = OPERATION % model.key
    found.held = [change_id for change_id, summary in journal.kept_named(conn, operation)
                  if paths.same(store_roots.from_row(conn, summary.get("folder")), name)]
    done = store_faces.vectors_under(conn, model.key, [photo_id for photo_id, _path in photos])
    change_id = None
    for photo_id, path in photos:
        if photo_id in done:
            found.done += 1
            continue
        if photo_service.box_shape(path)["turned"]:
            found.skipped += 1
            continue
        # Read before the model is asked, and again in the write: a face deleted, added or its box moved, or the photo's
        # row removed or moved, or a face named or unnamed (a named face pairs first), meanwhile (the server or a sync
        # beside this run) leaves the photo for the next run.
        old = store_faces.boxes_of(conn, photo_id)
        picture = model.picture(path)
        detected = model.detect_and_embed(picture) if face_service.ran(picture) else picture
        if not face_service.ran(detected):
            found.failed.append((photo_id, "the picture did not decode or the model failed"))
            continue
        if not detected:
            found.none_found += 1
            continue
        paired = clustering.pairs([box for _face, box, _named in old], [each["box"] for each in detected],
                                  first=[i for i, (_face, _box, named) in enumerate(old) if named])
        matched = [(old[i][0], face_service.vector_bytes(detected[j]["embedding"])) for i, j in sorted(paired.items())]
        taken = set(paired.values())
        new = [(each["box"], float(each["prob"]), face_service.vector_bytes(each["embedding"]),
                images.face_crop_of(picture.stored, each["box"]))
               for j, each in enumerate(detected) if j not in taken]
        unmatched = [(photo_id, face_id) for i, (face_id, _box, _named) in enumerate(old) if i not in paired]
        if apply:
            written = db.write_with_connection(
                db_path, lambda write: _write(write, model.key, operation, name, change_id, photo_id, path, old, matched,
                                                    new),
                label="face pass")
            if written is None:
                found.failed.append((photo_id, "its row or its faces changed while it was read, or another run did it:"
                                               " looked at again next run"))
                continue
            change_id = written
            if change_id not in found.changes:
                found.changes.append(change_id)
        found.matched += len(matched)
        found.new += len(new)
        found.unmatched += unmatched
    return found


def _write(conn, key, operation, folder, change_id, photo_id, path, old, matched, new):
    """One photo's rows, in one transaction: the id of the folder's change they were recorded in, or None, nothing written,
    when the photo's row is gone or at another path than `path`, the photo is done, or its faces are not as `old` read
    them."""
    db.begin(conn, immediate=True)
    row = store_photos.thumb_row(conn, photo_id)
    if row is None or not paths.same(row.path, path):
        return None
    if store_faces.vectors_under(conn, key, [photo_id]) or store_faces.boxes_of(conn, photo_id) != old:
        return None
    if change_id is None or not journal.kept(conn, change_id):
        change_id = journal.record(conn, operation, [],
                                   {"folder": store_roots.to_row(conn, folder), "matched": 0, "new": 0})
    store_faces.give_vectors(conn, change_id, path, key, matched, new, {"matched": len(matched), "new": len(new)})
    return change_id

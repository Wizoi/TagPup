"""Planning a bake: what one photo's file is to hold, worked out from what its library knows of it (docs/PHASE6_DESIGN.md,
section 9), and the fingerprint row a bake's write leaves behind it.

`plan_photo` reads the library -- the photo's vector, its faces with their vectors, whether its detector looked -- and only
then the file: the fields it holds (read as write_fields reads them: field_values.read of bake.WRITTEN's fields) and its
shape, and the fingerprint of its upright decode (the costliest read), each handed in as a function the library's
checks must pass before it is called. It says one of: ToBake (the values a write is to leave), Current (the file holds
them already), Cannot (why the library cannot say what the file is to hold, in a plain sentence). Nothing here writes a
file or a row of the library but `follower`: the write is tagpup.services.file_changes.write_fields's, which imports this
module for the follower, so this one never imports it.

A photo is baked whole or not at all (owner, decision 3): one face holding no vector under the library's face model refuses
the photo. A face under images.NEW_FACE_FLOOR on its shorter side, named or not, is not baked and does not refuse the photo (owner,
2026-10-25, #1973); a photo whose only faces are small is baked as a photo with no face. A photo whose detector never ran is "never examined", never "none found": FacesFound 0 is an answer only the
detector gives.
"""
import collections
import dataclasses

import numpy as np

from tagpup.core import fields, orientation
from tagpup.files import bake, images, shares
from tagpup.services import settings
from tagpup.store import embeddings, faces_detected, photo_fingerprints
from tagpup.store import faces as store_faces
from tagpup.store import roots as store_roots

#: The keys a run plans under, read once a run (keys): the photo vectors', the face vectors', and the detector's name
#: (tagpup.services.faces.detector_of, handed in: the ml layer is not read here).
Keys = collections.namedtuple("Keys", "photo face detector")

#: The operation a bake's change of a folder is recorded under in History (tagpup.services.baking_report.apply).
OPERATION = "bake photos"

#: The kinds of Cannot.
GONE = "gone"
UNREADABLE = "unreadable"
NEVER_EXAMINED = "never examined"
NO_VECTOR = "no photo vector"
CHANGED = "changed"
FACES_WITHOUT_VECTORS = "faces without vectors"


@dataclasses.dataclass(frozen=True)
class ToBake:
    """The file is to be written: `values` ({field: [texts]}, bake.values'), None when planned with no fingerprint (a dry
    run's photo outside its sample). How much XMP they make is not estimated: past one packet ExifTool moves FaceVectors
    whole to Extended XMP and keeps the regions in the main one, its detail (owner, 2026-10-25)."""
    values: object


@dataclasses.dataclass(frozen=True)
class Current:
    """The file holds the bake already."""


@dataclasses.dataclass(frozen=True)
class Cannot:
    """The library cannot say what the file is to hold: `kind` (one of the kinds above) and the sentence why."""
    kind: str
    reason: str


def keys(conn, detector):
    """The Keys of a run on `conn`, read once: the library's photo key and face key, and `detector` as given."""
    return Keys(settings.photo_key_on(conn), store_faces.model_key(conn), detector)


def plan_photo(conn, photo_path, keys, held, shape, fingerprint=None):
    """ToBake, Current or Cannot for the photo at `photo_path`. `held()`, the fields its file holds (field_values.read of
    bake.WRITTEN's values), or the Exception that read gave (returned or raised); `shape()`, (stored width, stored height,
    turn) as images.shown_shape gives them, or raising (the picture cannot be opened). None of the three is called for a photo the
    library refuses (GONE, NO_VECTOR, CHANGED, NEVER_EXAMINED, FACES_WITHOUT_VECTORS) or cannot reach (UNREADABLE: a file
    deleted outside the app, a drive or share away; shares.stamp_of is the one bounded look): its file is not opened for nothing.
    `fingerprint()`, the digest of its upright decode (images.fingerprint), called last, only for a photo that reaches the
    file; or None for the function: then nothing is planned to be written (ToBake.values None) and the Fingerprint the
    file holds is not compared. It may raise as `shape` does: the photo is UNREADABLE."""
    if not store_roots.photo_ids_at(conn, [photo_path]):
        return Cannot(GONE, "It is no longer in the library.")
    stored = embeddings.get(conn, photo_path, keys.photo)
    if stored is None:
        return Cannot(NO_VECTOR, "It has no photo vector under the library's photo model.")
    stamp = shares.stamp_of(photo_path)   # bounded: a share that is away is asked once, within a second
    if stamp is None or stamp is shares.UNANSWERED or stamp is shares.CANNOT_READ:
        return Cannot(UNREADABLE, "Its file could not be reached.")
    if (stored.mtime, stored.size) != stamp:
        return Cannot(CHANGED, "Its file changed since its photo vector was made: index it again first.")
    rows = store_faces.in_photo_with_names(conn, photo_path, keys.face)
    if not rows and not faces_detected.detected(conn, photo_path, keys.detector):
        return Cannot(NEVER_EXAMINED, "Its faces were never looked for by the library's face detector.")
    rows = [row for row in rows if images.is_big_enough(images.parse_box(row[1]))]   # a small face is not baked (#1973)
    missing = sum(vector is None for _id, _box, _ref, vector, _excluded in rows)
    if missing:
        return Cannot(FACES_WITHOUT_VECTORS, "%d face(s) hold no vector under the face model: retire-faces first." % missing)
    try:
        stored_w, stored_h, turn = shape()
        file_holds = held()
        digest = fingerprint() if fingerprint is not None and not isinstance(file_holds, Exception) else None
    except Exception as error:
        file_holds = error
    if isinstance(file_holds, Exception):
        return Cannot(UNREADABLE, "Its file could not be read: %s" % file_holds)
    applied = orientation.upright_size(stored_w, stored_h, turn)
    upright = [(face_id, orientation.to_upright(images.parse_box(box), stored_w, stored_h, turn), vector)
               for face_id, box, _ref, vector, _excluded in rows]
    upright.sort(key=lambda face: (-_area(face[1]), face[0]))
    found = [(box, np.frombuffer(vector, np.float32)) for _id, box, vector in upright]
    made = bake.values(np.frombuffer(stored.vector, np.float32), found, applied,
                       digest if digest is not None else bytes(32), keys.photo, keys.face)
    compared = dict(made)
    if digest is None:
        del compared[bake.WRITTEN[bake.FINGERPRINT]]
    if fields.reads_same(file_holds, compared):
        return Current()
    return ToBake(made if digest is not None else None)


def _area(box):
    """The pixels the upright `box` covers: a bake lists its faces largest first, ties by face id, its regions and its
    FaceVectors in that one order (owner, 2026-10-25). Whole pixels, not a share of the picture: two faces of one area and
    two shapes hold the same count, where their fractions can differ in the last bit and be ordered by rounding."""
    x1, y1, x2, y2 = box
    return (x2 - x1) * (y2 - y1)


def follower(row, state):
    """What a file's journaled write of a bake brings in the transaction that marks it `state`
    (tagpup.services.file_changes._followers): on 'done', the photo's fingerprint row, put from the Fingerprint the file
    holds after. None for any other file, for a row whose photo sync took away (photo_id None), and on an undo: the row is
    a fact of the picture, which a bake does not change, so an undo leaves it (#1894)."""
    texts = row.after.get(bake.WRITTEN[bake.FINGERPRINT])
    if state != "done" or not texts or row.photo_id is None:
        return None
    key, digest = bake.fingerprint_of(texts[0])

    def follow(conn):
        photo_fingerprints.put(conn, row.photo_id, key, digest)
    return follow

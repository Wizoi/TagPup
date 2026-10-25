"""Planning a bake: what one photo's file is to hold, worked out from what its library knows of it (docs/PHASE6_DESIGN.md,
section 9), and the fingerprint row a bake's write leaves behind it.

`plan_photo` reads the library -- the photo's vector, its faces with their vectors, whether its detector looked -- and the
fields the file holds (read by the caller, as write_fields reads them: field_values.read of bake.WRITTEN's fields), and
says one of: ToBake (the values a write is to leave, and about how many bytes of XMP they are), Current (the file holds
them already), Cannot (why the library cannot say what the file is to hold, in a plain sentence). Nothing here writes a
file or a row of the library but `follower`: the write is tagpup.services.file_changes.write_fields's, which imports this
module for the follower, so this one never imports it.

A photo is baked whole or not at all (owner, decision 3): one face holding no vector under the library's face model refuses
the photo. A photo whose detector never ran is "never examined", never "none found": FacesFound 0 is an answer only the
detector gives.
"""
import collections
import dataclasses

import numpy as np

from tagpup.core import fields, orientation
from tagpup.files import bake, field_values, images
from tagpup.services import settings
from tagpup.store import embeddings, faces_detected
from tagpup.store import faces as store_faces
from tagpup.store import roots as store_roots

#: The keys a run plans under, read once a run (keys): the photo vectors', the face vectors', and the detector's name
#: (tagpup.services.faces.detector_of, handed in: the ml layer is not read here).
Keys = collections.namedtuple("Keys", "photo face detector")

#: About how many bytes of XMP a bake is: the photo vector's text, and for each face its vector's text and its region.
#: A JPEG's XMP block holds 64 KB (bake.MAX_VALUE): a bake over it is written as Extended XMP, which at these sizes is a
#: photo of 37 faces or more (36 fit: 3,072 + 36 x 1,730 = 65,352).
PHOTO_BYTES = 3072
FACE_BYTES = 1730

#: The kinds of Cannot.
GONE = "gone"
UNREADABLE = "unreadable"
REGIONS = "regions"
NEVER_EXAMINED = "never examined"
NO_VECTOR = "no photo vector"
CHANGED = "changed"
FACES_WITHOUT_VECTORS = "faces without vectors"


@dataclasses.dataclass(frozen=True)
class ToBake:
    """The file is to be written: `values` ({field: [texts]}, bake.values'), None when planned with no fingerprint (a dry
    run's photo outside its sample); `bytes` about how much XMP they are."""
    values: object
    bytes: int

    @property
    def extended(self):
        """Is it more than a JPEG's XMP block holds, written as Extended XMP?"""
        return self.bytes > bake.MAX_VALUE


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
    """ToBake, Current or Cannot for the photo at `photo_path`. `held`, the fields its file holds (field_values.read of
    bake.WRITTEN's values) or the Exception that read gave; `shape`, (stored width, stored height, turn) as
    images.shown_shape gives them; `fingerprint`, the digest of its upright decode (images.fingerprint), or None: then
    nothing is planned to be written (ToBake.values None) and the Fingerprint the file holds is not compared."""
    if not store_roots.photo_ids_at(conn, [photo_path]):
        return Cannot(GONE, "It is no longer in the library.")
    if isinstance(held, field_values.UnreadableRegions):
        return Cannot(REGIONS, "Its file holds regions the writer cannot read: %s" % held)
    if isinstance(held, Exception):
        return Cannot(UNREADABLE, "Its file could not be read: %s" % held)
    stored = embeddings.get(conn, photo_path, keys.photo)
    if stored is None:
        return Cannot(NO_VECTOR, "It has no photo vector under the library's photo model.")
    if (stored.mtime, stored.size) != embeddings.stamp_of(photo_path):
        return Cannot(CHANGED, "Its file changed since its photo vector was made: index it again first.")
    rows = sorted(store_faces.in_photo_with_names(conn, photo_path, keys.face), key=lambda row: row[0])   # by face id
    if not rows and not faces_detected.detected(conn, photo_path, keys.detector):
        return Cannot(NEVER_EXAMINED, "Its faces were never looked for by the library's face detector.")
    missing = sum(vector is None for _id, _box, _ref, vector, _excluded in rows)
    if missing:
        return Cannot(FACES_WITHOUT_VECTORS, "%d face(s) hold no vector under the face model: retire-faces first." % missing)
    stored_w, stored_h, turn = shape
    found = [(orientation.to_upright(images.parse_box(box), stored_w, stored_h, turn), np.frombuffer(vector, np.float32))
             for _id, box, _ref, vector, _excluded in rows]
    made = bake.values(np.frombuffer(stored.vector, np.float32), found, orientation.upright_size(stored_w, stored_h, turn),
                       fingerprint if fingerprint is not None else bytes(32), keys.photo, keys.face)
    compared = dict(made)
    if fingerprint is None:
        del compared[bake.WRITTEN[bake.FINGERPRINT]]
    if fields.reads_same(held, compared):
        return Current()
    return ToBake(made if fingerprint is not None else None, PHOTO_BYTES + FACE_BYTES * len(found))

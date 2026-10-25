"""Planning a bake: what one photo's file is to hold, worked out from what its library knows of it (docs/PHASE6_DESIGN.md,
section 9), and the fingerprint row a bake's write leaves behind it.

`plan_photo` reads the library -- the photo's vector, its faces with their vectors, whether its detector looked -- and only
then the file: the fields it holds (read as write_fields reads them: field_values.read of bake.WRITTEN's fields) and its
shape, each handed in as a function the library's checks must pass before it is called. It says one of: ToBake (the values a write is to leave, and about how many bytes of XMP they are), Current (the file holds
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
from tagpup.files import bake, images
from tagpup.services import settings
from tagpup.store import embeddings, faces_detected, photo_fingerprints
from tagpup.store import faces as store_faces
from tagpup.store import roots as store_roots

#: The keys a run plans under, read once a run (keys): the photo vectors', the face vectors', and the detector's name
#: (tagpup.services.faces.detector_of, handed in: the ml layer is not read here).
Keys = collections.namedtuple("Keys", "photo face detector")

#: How many bytes one XMP packet of a JPEG holds, as ExifTool counts it: past it ExifTool moves the large properties (the
#: vectors) to Extended XMP.
XMP_PACKET_LIMIT = 65502

#: About how many bytes of main XMP packet a bake makes, MEASURED from real writes (2026-10-25: ExifTool 13.x,
#: BakeCase.bake of 0 to 38 faces onto a JPEG holding one keyword, the packet read from its APP1 segment, tests/
#: test_a_bake_is_planned_from_the_library.py): 6,355 bytes with no face (the packet wrapper, the photo vector, the model,
#: fingerprint and FacesFound markup, the keyword) and about 1,712 for each face (its vector's 1,368 characters of base64 and
#: its region's markup, which varies by 100 with the digits of its box: 1,660 to 1,775 between faces); 34 faces were
#: 64,560 bytes in one packet, 35 overflowed to Extended XMP (a 20,136-byte main packet and a 49,061-byte extension).
#: A file already holding more XMP (60 keywords: 2,600 bytes more) overflows sooner (34 faces); that is not known here.
PHOTO_BYTES = 6355
FACE_BYTES = 1712

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
    run's photo outside its sample); `bytes` about how much XMP they are."""
    values: object
    bytes: int

    @property
    def extended(self):
        """Is it more than a JPEG's XMP packet holds, written as Extended XMP?"""
        return self.bytes > XMP_PACKET_LIMIT


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
    turn) as images.shown_shape gives them, or raising (the picture cannot be opened). Neither is called for a photo the
    library refuses (GONE, NO_VECTOR, CHANGED, NEVER_EXAMINED, FACES_WITHOUT_VECTORS): its file is not opened for nothing.
    `fingerprint`, the digest of its upright decode (images.fingerprint), or None: then nothing is planned to be written
    (ToBake.values None) and the Fingerprint the file holds is not compared."""
    if not store_roots.photo_ids_at(conn, [photo_path]):
        return Cannot(GONE, "It is no longer in the library.")
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
    try:
        stored_w, stored_h, turn = shape()
        file_holds = held()
    except Exception as error:
        file_holds = error
    if isinstance(file_holds, Exception):
        return Cannot(UNREADABLE, "Its file could not be read: %s" % file_holds)
    found = [(orientation.to_upright(images.parse_box(box), stored_w, stored_h, turn), np.frombuffer(vector, np.float32))
             for _id, box, _ref, vector, _excluded in rows]
    made = bake.values(np.frombuffer(stored.vector, np.float32), found, orientation.upright_size(stored_w, stored_h, turn),
                       fingerprint if fingerprint is not None else bytes(32), keys.photo, keys.face)
    compared = dict(made)
    if fingerprint is None:
        del compared[bake.WRITTEN[bake.FINGERPRINT]]
    if fields.reads_same(file_holds, compared):
        return Current()
    return ToBake(made if fingerprint is not None else None, PHOTO_BYTES + FACE_BYTES * len(found))


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

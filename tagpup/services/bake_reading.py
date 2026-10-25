"""What the indexer takes from a photo's bake (tagpup.files.bake; docs/PHASE6_DESIGN.md, sections 3, 7 and 8): the photo's
vector and its faces, read from the file instead of worked out on the graphics card.

A bake is read only under the library's current key and its picture's fingerprint. Each side is decided alone: the photo
side when the bake's photo model is the library's photo-model key (tagpup.store.embeddings.model_key), the face side when
its face model is the face model the index was handed (Runtime.faces, the library's `faces.model`) and the library's own
setting; both only when the bake's fingerprint is the fingerprint of the upright decode the index makes anyway
(tagpup.files.images.fingerprint): the card is skipped, the decode is not. A picture edited after it was baked, a bake not
whole (bake.Rejected), one made under another model: that side is worked out as if the file held none, never an error.

The faces read are one more producer of the detections tagpup.services.faces.record_batch records -- a box in the picture
as stored, the vector, no score -- cut and floored by the face model's own `new_faces`, as a detection's are. A bake that
looked and found none is `[]`: the photo is recorded detected, with no face, as a detection finding none records it.
"""
import collections
import dataclasses

from tagpup.core import fields, orientation
from tagpup.files import bake, field_values, images

#: Each photo's verdict: its bake was read (a side of it, at least, was used: the other is worked out when its model is
#: not the library's), stale (its fingerprint is not the picture's), unreadable (bake.Rejected, other than none held),
#: or the file holds none; or whole and the picture's, but made under models neither of which is the library's.
READ, STALE, UNREADABLE, NOT_BAKED, OTHER_MODELS = "read", "stale", "unreadable", "not baked", "other models"


@dataclasses.dataclass
class Taken:
    """What a photo's bake gives the index: `vector`, the photo's vector (a list), or None to work it out; `detected`, its
    faces as a detection gives them ([] when it looked and found none), or None to detect them; and the `verdict`."""
    vector: object = None
    detected: object = None
    verdict: str = NOT_BAKED


def parsed(meta):
    """The Bake, or bake.Rejected, that a record of MetadataExtractor.batch_read(bakes=True) holds."""
    return bake.parse(meta.get("baked") or {})


def holds(found):
    """Is `found` (parsed) a bake, whole: one whose fingerprint the index decodes the picture to compare?"""
    return isinstance(found, bake.Bake)


def gives_vector(found, photo_key):
    """May the bake `found` give the photo's vector under `photo_key`, if its fingerprint is the picture's? Asked as the
    photo enters the read-ahead, before its picture is decoded: CLIP is not loaded for a photo that may need none."""
    return isinstance(found, bake.Bake) and found.photo_model == photo_key


def taken(found, meta, fingerprint, picture, *, photo_key, face_key):
    """What the index takes of the bake `found` (parsed) of the photo of `meta`, whose upright decode has `fingerprint`
    (images.fingerprint) and whose `picture` the face model made ready (None without one; NotDetected when the file
    could not be decoded for it: the file deleted between the two decodes). `photo_key` is the library's photo-model key;
    `face_key` the face model's key when it is the library's own (None: no face is read)."""
    if not isinstance(found, bake.Bake):
        return Taken(verdict=NOT_BAKED if found.reason == bake.NOT_BAKED else UNREADABLE)
    if (found.fingerprint_key, found.fingerprint) != (images.FINGERPRINT_KEY, fingerprint):
        return Taken(verdict=STALE)
    vector = found.photo_vector.tolist() if found.photo_model == photo_key else None
    detected = None
    if face_key is not None and found.face_model == face_key and picture is not None \
            and not getattr(picture, "failed", False):
        detected = detections(found, meta.get("baked") or {}, picture.stored.size)
    return Taken(vector, detected, READ if vector is not None or detected is not None else OTHER_MODELS)


def detections(found, record, stored_size):
    """The faces of the bake `found` as a detection gives them: each region turned from the upright picture it was made
    against into the picture as stored (`stored_size`, the decode's), by the file's Orientation in `record` -- a TIFF's
    counting as 1, its boxes being upright already (orientation.for_boxes) -- with its vector and no score."""
    value = record.get(fields.ORIENTATION)
    turn = orientation.for_boxes(value if value in range(1, 9) else 1,
                                 record.get(field_values.MIME_TYPE) == "image/tiff")
    width, height = stored_size
    return [{"box": orientation.to_stored(face.area, *found.applied, turn, width, height),
             "embedding": face.vector.tolist(), "prob": None} for face in found.faces]


class Tally:
    """What an index took from bakes, for the line the owner reads at its end (`said`)."""

    def __init__(self):
        self.counts = collections.Counter()

    def add(self, verdict, from_file, on_card):
        """One photo: its verdict, whether a side of it was read from its file, and whether one was worked out on the
        graphics card."""
        self.counts[verdict] += 1
        self.counts["from file"] += bool(from_file)
        self.counts["on card"] += bool(on_card)
        self.counts["no card"] += not on_card

    def said(self):
        """The line, or None when no photo of the run held a bake."""
        counts = self.counts
        if not (counts[READ] or counts[STALE] or counts[UNREADABLE]):
            return None
        return ("Baked into the files: %d photo(s) read from their file, %d worked out on the graphics card; %d bake(s) "
                "stale (the picture changed after it was baked), %d unreadable; %d photo(s) needed no graphics card."
                % (counts["from file"], counts["on card"], counts[STALE], counts[UNREADABLE], counts["no card"]))

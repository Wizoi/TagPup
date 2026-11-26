"""What the indexer takes from a photo's bake (tagpup.files.bake; docs/PHASE6_DESIGN.md, sections 3, 7 and 8): the photo's
vector and its faces, read from the file instead of worked out on the graphics card.

A bake is read only under the library's current key and its picture's fingerprint. Each side is decided alone: the photo
side when the bake's photo model is the library's photo-model key (tagpup.store.embeddings.model_key), the face side when
its face model is the face model the index was handed (Runtime.faces, the library's `faces.model`); both only when the
bake's fingerprint is the fingerprint of the upright decode the index makes anyway (tagpup.files.images.fingerprint): the
card is skipped, the decode is not. A picture edited after it was baked, a bake not
whole (bake.Rejected), one made under another model: that side is worked out as if the file held none, never an error.

The faces read are one more producer of the detections tagpup.services.faces.record_batch records -- a box in the picture
as stored, the vector, no score -- cut and floored by the face model's own `new_faces`, as a detection's are. A bake that
looked and found none is `[]`: the photo is recorded detected, with no face, as a detection finding none records it.
"""
import collections
import dataclasses

from tagpup.core import orientation
from tagpup.files import bake, images

#: Each photo's verdict: its bake was read (a side of it, at least, was used: the other is worked out when its model is
#: not the library's), stale (its fingerprint is not the picture's), unreadable (bake.Rejected, other than none held),
#: or the file holds none; or whole and the picture's, but made under models neither of which is the library's.
READ, STALE, UNREADABLE, NOT_BAKED, OTHER_MODELS = "read", "stale", "unreadable", "not baked", "other models"

#: What the line an index ends with when a photo held a bake begins with (Tally.said): the one spelling of it, which
#: tagpup.services.indexing reads whole, before the progress bar cuts it, and ends the folder's message with.
SAID = "Baked into the files: "


@dataclasses.dataclass
class Taken:
    """What a photo's bake gives the index: `vector`, the photo's vector (a list), or None to work it out; `detected`, its
    faces as a detection gives them ([] when it looked and found none), or None to detect them; and the `verdict`."""
    vector: object = None
    detected: object = None
    verdict: str = NOT_BAKED


def parsed(meta):
    """The Bake, or bake.Rejected, that a record of field_values.Fields.whole(bakes=True) holds."""
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
    (images.fingerprint) and whose `picture` the face model made ready (None without one, --skip-faces). A file deleted
    between the two decodes fails here, and its photo is left for the next index, as one deleted before them is.
    `photo_key` is the library's photo-model key;
    `face_key` the key of the face model the index was handed (None, --skip-faces: no face is read)."""
    if not isinstance(found, bake.Bake):
        return Taken(verdict=NOT_BAKED if found.reason == bake.NOT_BAKED else UNREADABLE)
    if (found.fingerprint_key, found.fingerprint) != (images.FINGERPRINT_KEY, fingerprint):
        return Taken(verdict=STALE)
    vector = found.photo_vector.tolist() if found.photo_model == photo_key else None
    detected = None
    # A picture the face model could not decode (it catches every exception: a truncated JPEG, #415) is a NotDetected: no
    # face side from the bake, so the photo's detection is worked out, or fails, exactly as it does without a bake.
    if (face_key is not None and found.face_model == face_key and picture is not None
            and not getattr(picture, "failed", False)):
        detected = detections(found, picture)
    return Taken(vector, detected, READ if vector is not None or detected is not None else OTHER_MODELS)


def detections(found, picture):
    """The faces of the bake `found` as a detection gives them: each region turned from the upright picture it was made
    against into the picture as stored, the box a detection keeps, by what the face model's `picture` read of the photo
    (tagpup.ml.scrfd_adaface.ScrfdPicture: `turn`, images.BOXES_TURN_INFO, orientation.boxes_turn: an XMP
    tiff:Orientation counts, a TIFF's is 1; `stored_size`) -- with its vector and no score."""
    width, height = picture.stored_size
    return [{"box": orientation.to_stored(face.area, *found.applied, picture.turn, width, height),
             "embedding": face.vector.tolist(), "prob": None} for face in found.faces]


class Tally:
    """What an index took from bakes, for the line the owner reads at its end (`said`). `card`: the run's models are on
    the graphics card; else on the processor, and the line says so (a machine with no card works each photo out there)."""

    def __init__(self, card=True):
        self.counts = collections.Counter()
        self.card = card

    def add(self, verdict, from_file, on_card):
        """One photo: its verdict, whether a side of it was read from its file, and whether one was worked out by a model
        (`on_card`: on the graphics card, or on the processor on a machine whose models run there)."""
        self.counts[verdict] += 1
        self.counts["from file"] += bool(from_file)
        self.counts["on card"] += bool(on_card)
        self.counts["no card"] += not on_card

    def said(self):
        """The line, SAID first, or None when no photo of the run held a bake (every verdict NOT_BAKED)."""
        counts = self.counts
        if not (counts[READ] or counts[STALE] or counts[UNREADABLE] or counts[OTHER_MODELS]):
            return None
        where, none = ("on the graphics card", "no graphics card") if self.card else ("on the processor", "no model")
        return SAID + (
            "%d photo(s) read from their file, %d worked out %s; %d bake(s) stale (the picture changed "
            "after it was baked), %d unreadable, %d made under other models; %d photo(s) needed %s."
            % (counts["from file"], counts["on card"], where, counts[STALE], counts[UNREADABLE], counts[OTHER_MODELS],
               counts["no card"], none))

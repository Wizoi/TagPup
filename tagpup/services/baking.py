"""Planning a bake: what one photo's file is to hold, worked out from what its library knows of it (docs/PHASE6_DESIGN.md,
section 9), and the fingerprint row a bake's write leaves behind it.

`plan_photo` reads the library -- the photo's vector, its faces with their vectors, whether its detector looked -- and only
then the file: the fields it holds (read as write_fields reads them: field_values.read of bake.WRITTEN's fields) and its
shape, and the fingerprint of its upright decode (the costliest read), each handed in as a function the library's
checks must pass before it is called. It says one of: ToBake (the values a write is to leave), Current (the file holds
them already, or holds a bake under the library's keys of the picture as it is now, which is never rewritten: owner,
DECISIONS 2026-10-27), Cannot (why the library cannot say what the file is to hold, in a plain sentence). Nothing here writes a
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
from tagpup.core import paths
from tagpup.files import bake, field_values, images, shares
from tagpup.services import settings
from tagpup.store import embeddings, faces_detected, file_journal, photo_fingerprints
from tagpup.store import faces as store_faces
from tagpup.store import roots as store_roots

#: The keys a run plans under, read once a run (keys): the photo vectors', the face vectors', and the detector's name
#: (tagpup.services.faces.detector_of, handed in: the ml layer is not read here).
Keys = collections.namedtuple("Keys", "photo face detector")

#: The operation a folder's bake was recorded under in History, by a command since deleted: a kind the live journals
#: hold (tagpup.services.journal.RETIRED).
OPERATION = "bake photos"
#: The operation of the bake the index writes after each folder (tagpup.services.bake_after_index): internal, in no
#: History and never undone (file_journal.INTERNAL).
INDEX_OPERATION = file_journal.INDEX_BAKE

#: The kinds of Cannot.
GONE = "gone"
UNREADABLE = "unreadable"
NEVER_EXAMINED = "never examined"
NO_VECTOR = "no photo vector"


#: Why a photo whose vector is not of its picture as it is now (photo_fingerprints.same_picture) cannot be baked.
NOT_NOW = "It has no photo vector of its picture as it is now."


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


def plan_photo(conn, photo_path, keys, held, shape, fingerprint=None, stamp_of=shares.stamp_of):
    """ToBake, Current or Cannot for the photo at `photo_path`. `held()`, the fields its file holds (field_values.read of
    bake.WRITTEN's values), or the Exception that read gave (returned or raised); `shape()`, (stored width, stored height,
    turn) as images.shown_shape gives them, or raising (the picture cannot be opened). None of the three is called for a photo the
    library refuses (GONE, NO_VECTOR, NEVER_EXAMINED) or cannot reach (UNREADABLE: a file
    deleted outside the app, a drive or share away; `stamp_of`, the one bounded look: shares.stamp_of, or a run's
    shares.Walk, PhotoAt's): its file is not opened for nothing.
    `fingerprint()`, the digest of its upright decode (images.fingerprint), called last, only for a photo that reaches the
    file; or None for the function: then nothing is planned to be written (ToBake.values None) and the Fingerprint the
    file holds is not compared. It may raise as `shape` does: the photo is UNREADABLE.

    Whether the vector is of the picture the file holds now is photo_fingerprints.same_picture's (the one rule): its
    stamp describing the file, or else the decode's fingerprint equal to the photo's fingerprint row. With no row, or a
    decode that differs, it is NO_VECTOR (the index computes it again); with a row and no `fingerprint`, undecided, and
    planned as ToBake(None) or Current."""
    if not store_roots.photo_ids_at(conn, [photo_path]):
        return Cannot(GONE, "It is no longer in the library.")
    stored = embeddings.get(conn, photo_path, keys.photo)
    if stored is None:
        return Cannot(NO_VECTOR, "It has no photo vector under the library's photo model.")
    stamp = stamp_of(photo_path)   # bounded: a share that is away is asked once, within a second
    if stamp is None or stamp is shares.UNANSWERED or stamp is shares.CANNOT_READ:
        return Cannot(UNREADABLE, "Its file could not be reached.")
    row = photo_fingerprints.NOT_READ
    if photo_fingerprints.same_picture((stored.mtime, stored.size), stamp) != photo_fingerprints.STAMPED:
        row = photo_fingerprints.of_path(conn, photo_path)
        if photo_fingerprints.same_picture((stored.mtime, stored.size), stamp, row) == photo_fingerprints.CHANGED:
            return Cannot(NO_VECTOR, NOT_NOW)
    rows = store_faces.in_photo_with_names(conn, photo_path, keys.face)
    if not rows and not faces_detected.detected(conn, photo_path, keys.detector):
        return Cannot(NEVER_EXAMINED, "Its faces were never looked for by the library's face detector.")
    rows = [row for row in rows if images.is_big_enough(images.parse_box(row[1]))]   # a small face is not baked (#1973)
    try:
        stored_w, stored_h, turn = shape()
        file_holds = held()
        digest = fingerprint() if fingerprint is not None and not isinstance(file_holds, Exception) else None
    except Exception as error:
        file_holds = error
    if isinstance(file_holds, Exception):
        return Cannot(UNREADABLE, "Its file could not be read: %s" % file_holds)
    if digest is not None and photo_fingerprints.same_picture(
            (stored.mtime, stored.size), stamp, row, (images.FINGERPRINT_KEY, digest)) == photo_fingerprints.CHANGED:
        return Cannot(NO_VECTOR, NOT_NOW)
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
    if fields.reads_same(file_holds, compared) or _current_bake(file_holds, keys, digest):
        return Current()
    return ToBake(made if digest is not None else None)


def _current_bake(file_holds, keys, digest):
    """Does the file hold a bake under this library's keys of the picture whose decode is `digest`? Then it is current
    and never rewritten, whatever faces or photo vector it holds beside this library's rows (owner, DECISIONS
    2026-10-27): two libraries over one folder adopt each other's bake (the index reads it: bake_reading), and a GPU's
    vector and a CPU's do not rewrite each other. With no `digest` (a run planning without a decode) it is not decided
    here: a file differing from the rows is ToBake(None), and the run that decodes it decides."""
    if digest is None:
        return False
    if not all(fields.same_read(bake.WRITTEN[field], file_holds.get(bake.WRITTEN[field]), [key])
               for field, key in ((bake.PHOTO_MODEL, keys.photo), (bake.FACE_MODEL, keys.face))):
        return False
    texts = file_holds.get(bake.WRITTEN[bake.FINGERPRINT])
    if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
        return False
    try:
        return bake.fingerprint_of(texts[0]) == (images.FINGERPRINT_KEY, bytes(digest))
    except ValueError:
        return False   # not a Fingerprint of ours: no bake of ours


class ReadStopped(Exception):
    """A run was asked to stop while a folder's files were read: a photo the read did not reach was not asked of its
    share, and is counted nowhere."""


class FolderRead:
    """The bake fields of a folder's files (Fields.read of bake.WRITTEN's, `et` the run's Fields), read in one read the first
    time a photo of it asks: the one read a folder is planned from, by the index's
    bake (bake_after_index). `stop`, an Event the read stops at (None: the folder is read whole)."""

    def __init__(self, et, photo_paths, stop=None):
        self.et, self.photo_paths, self.stop, self.found = et, photo_paths, stop, None

    def of(self, photo_path):
        """What field_values.read gave for `photo_path`: its fields, or the Exception its read gave (NotAnswered for a
        photo the read did not reach, ReadStopped for one a stop left unread)."""
        if self.found is None:
            self.found = self.et.read(self.photo_paths, list(bake.WRITTEN.values()), stop=self.stop)
        held = self.found.get(paths.key(photo_path))
        if held:
            return held
        if self.stop is not None and self.stop.is_set():
            return ReadStopped("Its file was not read: the run was stopped.")
        return field_values.NotAnswered("Its file was not read.")

    @property
    def stalled(self):
        """Did ExifTool not answer the read in its deadline (field_values.read's NotAnswered)?"""
        return any(isinstance(held, field_values.NotAnswered) for held in (self.found or {}).values())


def walk(stop=None):
    """The shares.Walk a bake run looks at its files through (PhotoAt): patient, so a share slow to wake is not taken as
    away on its first answer; `stop()`, asked while it waits."""
    return shares.Walk(patient=True, stop=stop)


class PhotoAt:
    """One photo of a FolderRead as plan_photo asks it: its fields from the folder's read (`given`, each answer, the last
    its latest plan's; empty when no plan reached its file), its shape from its header, read once however often it is
    planned; its file looked at through the run's `walk` (baking.walk)."""

    def __init__(self, path, read, walk):
        self.path, self.read, self.given, self._shaped = path, read, [], None
        self.walk, self.unanswered = walk, False

    def stamp_of(self, photo_path):
        stamp = self.walk.stamp_of(photo_path)
        self.unanswered = stamp is shares.UNANSWERED
        return stamp

    def held(self):
        self.given.append(self.read.of(self.path))
        return self.given[-1]

    def shape(self):
        if self._shaped is None:
            width, height, _oriented, turn = images.shown_shape(self.path)
            self._shaped = (width, height, turn)
        return self._shaped

    def plan(self, conn, keys, fingerprint=None):
        """plan_photo of the photo, `fingerprint` as plan_photo takes it."""
        return plan_photo(conn, self.path, keys, self.held, self.shape, fingerprint, self.stamp_of)

    @property
    def not_answered(self):
        """Did its share not answer: the walk's look at its file (after its wait), or the folder's read?"""
        return self.unanswered or (bool(self.given) and isinstance(self.given[-1], field_values.NotAnswered))

    @property
    def stopped(self):
        """Did a stop leave its file unread?"""
        return bool(self.given) and isinstance(self.given[-1], ReadStopped)


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

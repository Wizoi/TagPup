"""What a quarter turn or a mirror of a held photo brings with its Orientation: the face boxes, crops and record, and the
vectors.

A rotation is a journaled change of the file's EXIF Orientation (tagpup.services.photos.rotate_photos, through
file_changes.write_fields). The Orientation is all the file holds of it; the rest is rows, and they are made in the
SAME transaction that marks the file `done` -- forward, settled after a crash, or back in an undo -- so a crash can
never leave a turned file with boxes of the old picture, or the reverse (docs/INVARIANTS.md):

- the photo's vectors go, since the embedder applies the Orientation and they describe the photo before the turn, and its
  fingerprint (tagpup.store.photo_fingerprints), the hash of the upright decode;
- its faces' cached crops go, every format: each was cut upright by the old Orientation (images.face_crop_of), and the
  next request cuts it again; and its record of the detectors that looked at it (tagpup.store.faces_detected), since
  faces are detected on the picture as shown: the next index looks at it again, and adds only what no face
  already has (pairing by overlap, clustering.pairs). The faces themselves -- names, exclusions, boxes, vectors -- stay;
- for a TIFF, which Pillow shows already turned, the face boxes turn with it. For every other format the boxes are in
  the stored pixels, which do not move (tagpup.files.images.shown_shape).

None of these is journaled: each is a fact of the picture as it now is, made again by reading it, so an undo of the turn
takes them away once more rather than putting the old ones back.

The direction of the turn is not stored beside the file: it is what the journaled Orientation says (the value before and
the value after), and the stored picture's size is the size the file shows now under the Orientation it holds.
"""
import logging

from tagpup.core import fields
from tagpup.core.orientation import ROTATED_ORIENTATION, reoriented, upright_size
from tagpup.files import images
from tagpup.store import embeddings, faces, faces_detected, photo_fingerprints

logger = logging.getLogger(__name__)

#: The field a rotation writes and the journal compares. Written without its group (fields.WRITTEN_AS), so an XMP
#: tiff:Orientation already in the file is updated to match instead of being left to contradict the EXIF one. Only the
#: Orientation changes, never the pixels: browsers and the previews (ImageOps.exif_transpose) show a photo by it. A turn
#: used to decode the photo, turn it and save it again with only its EXIF block, which dropped every XMP and IPTC field.
FIELD = fields.ORIENTATION

def orientation(texts):
    """The EXIF Orientation (1 to 8) a journaled value says: 1 for a photo that holds none."""
    try:
        value = int(texts[0]) if texts else 1
    except (TypeError, ValueError):
        return 1
    return value if 1 <= value <= 8 else 1


def next_orientation(direction, texts):
    """The Orientation after a quarter turn or a mirror `direction` of a photo holding `texts` (tagpup.core.orientation's
    table)."""
    return ROTATED_ORIENTATION[direction][orientation(texts)]


def direction_between(was, now):
    """'left', 'right' or 'mirror': the quarter turn or the mirror that takes Orientation `was` to `now` (journaled values),
    or None when none does (at most one does: a quarter turn keeps a mirrored value mirrored, a mirror does not)."""
    for direction in ROTATED_ORIENTATION:
        if next_orientation(direction, was) == orientation(now):
            return direction
    return None


def follower(row, state):
    """What the row of a file journaled as a change of its Orientation must do in the transaction that marks it `state`
    ('done', or 'undone' going back): a function of the connection, or None for any other file. Read from the file now, so it is
    called after the file holds its new value. Sets `row.followed` to the face boxes it turned."""
    if FIELD not in row.after:
        return None
    forward = state == "done"
    was, now = (row.before, row.after) if forward else (row.after, row.before)
    turn = None
    if not forward and row.turned:
        # Taken out of its change after it was done (file_changes._read_back): exactly the turn that was made, back.
        width, height, done_from, done_to = row.turned
        turn = (width, height, done_to, done_from)
    elif direction_between(was.get(FIELD), now.get(FIELD)):
        try:
            width, height, oriented, _turn = images.shown_shape(row.path)
        except Exception as problem:
            # A file Pillow cannot open has no boxes worth turning; the Orientation is recorded all the same.
            logger.warning("%s: could not tell whether its face boxes turn: %s", row.named(), problem)
            oriented = False
        if oriented:
            # The file shows its picture under the Orientation it holds now; its stored pixels are that size turned back.
            new = orientation(now.get(FIELD))
            turn = (*upright_size(width, height, new), orientation(was.get(FIELD)), new)

    def follow(conn):
        embeddings.forget(conn, row.path)
        photo_fingerprints.forget(conn, row.path)   # the decode the fingerprint hashed turns too
        faces.forget_crops(conn, row.path)
        faces_detected.forget(conn, row.path)
        row.followed = faces.turn_boxes_in(conn, row.path, lambda box: reoriented(box, *turn)) if turn else 0
        row.turned = turn if forward else None
    return follow

"""What a quarter turn of a held photo brings with its Orientation: the face boxes and the vectors.

A rotation is a journaled change of the file's EXIF Orientation (tagpup.services.photos.rotate_photos, through
file_changes.write_fields). The Orientation is all the file holds of it; the rest is rows, and they are made in the
SAME transaction that marks the file `done` -- forward, settled after a crash, or back in an undo -- so a crash can
never leave a turned file with boxes of the old picture, or the reverse (docs/INVARIANTS.md):

- the photo's vectors go, since the embedder applies the Orientation and they describe the photo before the turn;
- for a TIFF, which Pillow shows already turned, the face boxes turn with it and their cached crops are dropped. For
  every other format the boxes are in the stored pixels, which do not move (tagpup.files.images.shown_size).

The direction of the turn is not stored beside the file: it is what the journaled Orientation says (the value before and
the value after), and the picture's size before it is the size the file shows now, swapped.
"""
import logging

from tagpup.core import fields
from tagpup.files import images
from tagpup.store import embeddings, faces

logger = logging.getLogger(__name__)

#: The field a rotation writes and the journal compares. Written without its group (fields.WRITTEN_AS), so an XMP
#: tiff:Orientation already in the file is updated to match instead of being left to contradict the EXIF one. Only the
#: Orientation changes, never the pixels: browsers and the previews (ImageOps.exif_transpose) show a photo by it. A turn
#: used to decode the photo, turn it and save it again with only its EXIF block, which dropped every XMP and IPTC field.
FIELD = fields.ORIENTATION

#: The EXIF Orientation a photo has after a quarter turn, by the one it had before.
#: Worked out against ImageOps.exif_transpose -- what is shown for the new value is
#: what was shown for the old one, turned -- and checked the same way in
#: tests/test_rotate_keeps_the_photo.py. The mirrored values (2, 4, 5, 7) stay mirrored.
ROTATED_ORIENTATION = {
    "left": {1: 8, 2: 5, 3: 6, 4: 7, 5: 4, 6: 1, 7: 2, 8: 3},
    "right": {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1},
}


def orientation(texts):
    """The EXIF Orientation (1 to 8) a journaled value says: 1 for a photo that holds none."""
    try:
        value = int(texts[0]) if texts else 1
    except (TypeError, ValueError):
        return 1
    return value if 1 <= value <= 8 else 1


def next_orientation(direction, texts):
    """The Orientation after a quarter turn `direction` of a photo holding `texts`."""
    return ROTATED_ORIENTATION[direction][orientation(texts)]


def direction_between(was, now):
    """'left' or 'right': the quarter turn that takes Orientation `was` to `now` (journaled values), or None when no single
    quarter turn does."""
    for direction in ("left", "right"):
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
    direction = direction_between(was.get(FIELD), now.get(FIELD))
    turn = None
    if not forward and row.turned:
        # Taken out of its change after it was done (file_changes._read_back): exactly the turn that was made, back.
        done, width, height = row.turned
        turn = ("right" if done == "left" else "left", height, width)
    elif direction:
        try:
            width, height, oriented = images.shown_size(row.path)
        except Exception as problem:
            # A file Pillow cannot open has no boxes worth turning; the Orientation is recorded all the same.
            logger.warning("%s: could not tell whether its face boxes turn: %s", row.named(), problem)
            oriented = False
        if oriented:
            # The picture before the turn was as wide as the file shows it tall.
            turn = (direction, height, width)

    def follow(conn):
        embeddings.forget(conn, row.path)
        row.followed = faces.turn_boxes_in(conn, row.path, *turn) if turn else 0
        row.turned = turn if forward else None
    return follow

"""What a photo's EXIF Orientation (1 to 8) does to its picture: the one statement of how a point of the pixels as stored
lands on the picture as a person sees it, and of the Orientation a quarter turn or a mirror leaves.

A face box is kept in the pixels Pillow shows a photo in (tagpup.files.images.shown_shape): the stored pixels, which an
Orientation change leaves where they are -- except a TIFF, whose Orientation Pillow applies as it loads it, so its boxes
are already upright and it counts as orientation 1 here (`boxes_turn`). Everything that sees a face sees the picture as
shown, upright, and a box is turned between the two by this module alone (`to_upright`, `box_to_stored`, `to_stored`):
at the file's edge (a region written into a file for other programs, XMP-mwg-rs, phase 6, and read back), at the
detector's edge (the detector looks at the picture as shown, and its boxes are kept stored: tagpup.ml.scrfd_adaface;
DECISIONS 2026-10-27), and in the page's answer (a crop cut as a person sees the face: tagpup.files.images.face_crop_of).

Coordinates are continuous: a box is [x1, y1, x2, y2] with x2 and y2 past its last pixel, as Pillow's getbbox gives it,
so a turn maps a box onto the box of the same pixels turned. For a stored picture W wide and H high, the point (x, y) is
shown at, by Orientation: 1 (x, y); 2 (W-x, y); 3 (W-x, H-y); 4 (x, H-y); 5 (y, x); 6 (H-y, x); 7 (H-y, W-x); 8 (y, W-x)
-- what ImageOps.exif_transpose does (checked per value in tests/test_a_face_box_turned_upright_and_back.py).
"""

#: The EXIF Orientation a photo has after a quarter turn, by the one it had before. Worked out against
#: ImageOps.exif_transpose -- what is shown for the new value is what was shown for the old one, turned -- and checked the
#: same way in tests/test_rotate_keeps_the_photo.py. The mirrored values (2, 4, 5, 7) stay mirrored.
ROTATED_ORIENTATION = {
    "left": {1: 8, 2: 5, 3: 6, 4: 7, 5: 4, 6: 1, 7: 2, 8: 3},
    "right": {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1},
}

#: The EXIF Orientation a photo has after it is mirrored left to right as it is shown, by the one it had before: what is
#: shown for the new value is what was shown for the old one flipped (ImageOps.mirror), checked the same way. Mirrored twice
#: is where it started.
MIRRORED = {1: 2, 2: 1, 3: 4, 4: 3, 5: 6, 6: 5, 7: 8, 8: 7}

#: The Orientations that swap a picture's width and height as it is shown.
TRANSPOSED = frozenset({5, 6, 7, 8})


def boxes_turn(declared, tiff):
    """The Orientation (1 to 8) a photo's face boxes are turned by between the picture as stored and the picture shown
    upright, from what its decode declares (`declared`, Pillow's getexif Orientation, which reads XMP tiff:Orientation
    when the file has no EXIF one: tagpup.files.images): 1 for a TIFF (`tiff`), whose Orientation Pillow applies as it
    loads it, so its boxes are upright already; 1 for a value that is no Orientation; else the value declared. The one
    rule of it: images.shown_shape and images.opened (the index's read of a bake, tagpup.services.bake_reading) ask it,
    and the bake's writer (phase 6, cut 2) turns the boxes it writes by the same."""
    return 1 if tiff or declared not in range(1, 9) else declared


def upright_size(width, height, orientation):
    """(width, height) of a stored picture `width` x `height` as it is shown under `orientation`."""
    return (height, width) if orientation in TRANSPOSED else (width, height)


def _shown(x, y, width, height, orientation):
    """Where the stored point (x, y) of a `width` x `height` picture is shown."""
    return {1: (x, y), 2: (width - x, y), 3: (width - x, height - y), 4: (x, height - y),
            5: (y, x), 6: (height - y, x), 7: (height - y, width - x), 8: (y, width - x)}[orientation]


def _stored(u, v, width, height, orientation):
    """The stored point of a `width` x `height` picture shown at (u, v): `_shown` backwards."""
    return {1: (u, v), 2: (width - u, v), 3: (width - u, height - v), 4: (u, height - v),
            5: (v, u), 6: (v, height - u), 7: (width - v, height - u), 8: (width - v, u)}[orientation]


def _corners(a, b):
    (x1, y1), (x2, y2) = a, b
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def to_upright(box, stored_w, stored_h, orientation):
    """The box [x1, y1, x2, y2] of the stored pixels (a picture `stored_w` x `stored_h`) on the picture as shown under
    `orientation`, in its pixels (upright_size)."""
    x1, y1, x2, y2 = box
    return _corners(_shown(x1, y1, stored_w, stored_h, orientation), _shown(x2, y2, stored_w, stored_h, orientation))


def box_to_stored(box, stored_w, stored_h, orientation):
    """The box [x1, y1, x2, y2] of the picture as shown under `orientation` (upright_size of a stored picture `stored_w` x
    `stored_h`) in the stored pixels: `to_upright` backwards."""
    u1, v1, u2, v2 = box
    return _corners(_stored(u1, v1, stored_w, stored_h, orientation), _stored(u2, v2, stored_w, stored_h, orientation))


def reoriented(box, stored_w, stored_h, was, now):
    """The box [x1, y1, x2, y2] of the picture shown under Orientation `was` on the same stored pixels (a picture `stored_w`
    x `stored_h`) shown under `now`: where a face box of a picture shown as it is stored (a TIFF) goes when the file's
    Orientation changes, a quarter turn and a mirror alike (tagpup.services.rotation)."""
    return to_upright(box_to_stored(box, stored_w, stored_h, was), stored_w, stored_h, now)


def area_of(box, width, height):
    """(X, Y, W, H) of the box [x1, y1, x2, y2] of a `width` x `height` picture, as an MWG region's Area holds it: its
    centre and size, each a fraction of the picture's."""
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0 / width, (y1 + y2) / 2.0 / height, (x2 - x1) / float(width), (y2 - y1) / float(height))


def to_stored(area, applied_w, applied_h, orientation, stored_w, stored_h):
    """The box [x1, y1, x2, y2], whole pixels, of the stored picture (`stored_w` x `stored_h`) that the upright region
    `area` (X, Y, W, H, area_of's) of a picture `applied_w` x `applied_h` (its AppliedToDimensions) covers under
    `orientation`. The applied size is scaled to the stored picture's upright size: a region is a fraction of its picture."""
    x, y, w, h = area
    shown_w, shown_h = upright_size(stored_w, stored_h, orientation)
    across, down = shown_w / float(applied_w), shown_h / float(applied_h)
    upright = [(x - w / 2.0) * applied_w * across, (y - h / 2.0) * applied_h * down,
               (x + w / 2.0) * applied_w * across, (y + h / 2.0) * applied_h * down]
    return [int(round(value)) for value in box_to_stored(upright, stored_w, stored_h, orientation)]

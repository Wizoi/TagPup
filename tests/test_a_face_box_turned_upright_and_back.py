"""A face box turned to the picture as a person sees it, and back (tagpup.core.orientation; docs/PHASE6_DESIGN.md, 7).

A box is kept in the stored pixels; a region written into a file for other programs (MWG) is upright. The two meet at the
file's edge only, and a box that came back one pixel off, or mirrored, would sit beside the face it names in every other
program. So each of the eight Orientations is checked against the picture itself: a real JPEG of 64 x 48 with a bright block
off centre, the block's box in the stored pixels turned by `to_upright` must be the block's box in the picture Pillow shows
upright (images.opened, the decode the fingerprint and the embedder use), and back through an MWG area (`to_stored`) it is
the stored box again, also from a region applied to a smaller copy. A TIFF, whose Orientation Pillow applies as it loads it,
counts as orientation 1: its box is already upright.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from PIL import Image  # noqa: E402

from tagpup.core import orientation  # noqa: E402
from tagpup.files import images  # noqa: E402

WIDTH, HEIGHT = 64, 48
#: Off centre in both directions, so no two Orientations put it in the same place; on the JPEG's 8-pixel grid, so its
#: edges stay sharp.
BLOCK = [8, 8, 24, 16]
ORIENTATION_TAG = 0x0112


def photo(path, value, fmt="JPEG"):
    """A WIDTH x HEIGHT picture, black but for a white BLOCK, holding Orientation `value`."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    img.paste((255, 255, 255), tuple(BLOCK))
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = value
    extra = {"quality": 100} if fmt == "JPEG" else {}
    img.save(path, fmt, exif=exif.tobytes(), **extra)
    return path


def block_in(picture):
    """The bright block's box [x1, y1, x2, y2] in `picture`."""
    return list(picture.convert("L").point(lambda level: 255 if level > 128 else 0).getbbox())


class EachOrientation(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="orientation_")

    def test_the_stored_box_turned_upright_is_the_block_as_shown(self):
        for value in range(1, 9):
            with self.subTest(orientation=value):
                path = photo(os.path.join(self.home.root, "o%d.jpg" % value), value)
                stored = images.opened(path, upright=False)
                shown = images.opened(path, upright=True)
                self.assertEqual(BLOCK, block_in(stored))
                self.assertEqual(shown.size, orientation.upright_size(WIDTH, HEIGHT, value))
                self.assertEqual(block_in(shown), orientation.to_upright(BLOCK, WIDTH, HEIGHT, value))

    def test_eight_orientations_put_the_block_in_eight_places(self):
        self.assertEqual(8, len({tuple(orientation.to_upright(BLOCK, WIDTH, HEIGHT, value)) for value in range(1, 9)}))

    def test_an_upright_region_comes_back_to_the_stored_box_within_a_pixel(self):
        for value in range(1, 9):
            shown_w, shown_h = orientation.upright_size(WIDTH, HEIGHT, value)
            area = orientation.area_of(orientation.to_upright(BLOCK, WIDTH, HEIGHT, value), shown_w, shown_h)
            for applied in ((shown_w, shown_h), (shown_w // 2, shown_h // 2)):   # the picture, and a copy half its size
                with self.subTest(orientation=value, applied=applied):
                    back = orientation.to_stored(area, applied[0], applied[1], value, WIDTH, HEIGHT)
                    self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(back, BLOCK)), back)

    def test_a_box_from_a_detector_in_fractions_comes_back_within_a_pixel(self):
        box = [10.4, 7.6, 31.2, 29.9]   # the detector's boxes are not whole pixels
        for value in range(1, 9):
            with self.subTest(orientation=value):
                shown_w, shown_h = orientation.upright_size(WIDTH, HEIGHT, value)
                area = orientation.area_of(orientation.to_upright(box, WIDTH, HEIGHT, value), shown_w, shown_h)
                back = orientation.to_stored(area, shown_w, shown_h, value, WIDTH, HEIGHT)
                self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(back, box)), back)


class ATiff(unittest.TestCase):
    def test_counts_as_orientation_1_its_box_already_upright(self):
        home = own_home.for_test(self, prefix="orientation_tiff_")
        path = photo(os.path.join(home.root, "turned.tif"), 6, fmt="TIFF")
        width, height, oriented, value = images.shown_shape(path)
        self.assertTrue(oriented)
        self.assertEqual(1, value)
        self.assertEqual(1, images.opened(path, upright=False).info[images.BOXES_TURN_INFO])
        box = block_in(images.opened(path, upright=False))   # where a detector found it: the pixels Pillow shows
        self.assertEqual(block_in(images.opened(path, upright=True)), orientation.to_upright(box, width, height, value))
        self.assertEqual((width, height), (HEIGHT, WIDTH), "Pillow turned it as it loaded it")

    def test_a_jpeg_keeps_its_own(self):
        self.assertEqual(6, orientation.boxes_turn(6, False))
        self.assertEqual(1, orientation.boxes_turn(0, False), "a value that is no Orientation turns nothing")


if __name__ == "__main__":
    unittest.main()

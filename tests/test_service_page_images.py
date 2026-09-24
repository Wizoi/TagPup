"""tagpup.services.photos.page_copy and face_crop: the images the pages show.

Both servers had a copy of each. The photos here are real, if small: what is checked
is the picture that comes back, and what the library keeps of it.
"""
import io
import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core.result import NotFound, Refused  # noqa: E402
from tagpup.services import photos  # noqa: E402

#: EXIF Orientation 6: the stored pixels are shown turned a quarter clockwise.
TURNED_RIGHT = 6


def size_of(jpeg):
    with Image.open(io.BytesIO(jpeg)) as img:
        return img.size


class APhotoForAPage(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        # Stored 60 wide and 30 high, and shown turned: 30 wide and 60 high.
        self.photo = self.lib.photo("lake.jpg", b"")
        exif = Image.Exif()
        exif[0x0112] = TURNED_RIGHT
        Image.new("RGB", (60, 30), (200, 40, 40)).save(self.photo, exif=exif)

    def test_the_file_itself_without_a_size(self):
        content, content_type = photos.page_copy(self.photo)
        with open(self.photo, "rb") as f:
            self.assertEqual(content, f.read())
        self.assertEqual(content_type, "image/jpeg")

    def test_a_copy_turned_upright_as_a_person_sees_it(self):
        content, content_type = photos.page_copy(self.photo, 1000, upright=True)
        self.assertEqual((size_of(content), content_type), ((30, 60), "image/jpeg"))

    def test_a_copy_as_stored_for_boxes_drawn_in_the_stored_pixels(self):
        content, _ = photos.page_copy(self.photo, 1000, upright=False)
        self.assertEqual(size_of(content), (60, 30))

    def test_a_copy_no_larger_than_asked(self):
        content, _ = photos.page_copy(self.photo, 20, upright=True)
        self.assertEqual(size_of(content), (10, 20))

    def test_a_png_goes_out_as_a_png(self):
        png = self.lib.photo("chart.png", b"")
        Image.new("RGB", (8, 8)).save(png)
        self.assertEqual(photos.page_copy(png)[1], "image/png")

    def test_a_copy_that_cannot_be_made_falls_back_to_the_file(self):
        broken = self.lib.photo("broken.jpg", b"not a picture")
        self.assertEqual(photos.page_copy(broken, 100), (b"not a picture", "image/jpeg"))

    def test_a_file_that_is_not_a_photo_is_refused(self):
        secret = self.lib.photo("secret.txt", b"private")
        with self.assertRaises(Refused):
            photos.page_copy(secret)

    def test_a_photo_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            photos.page_copy(os.path.join(self.lib.photos, "ghost.jpg"))


class AFacesCrop(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("party.jpg", b"")
        Image.new("RGB", (400, 300), (10, 120, 10)).save(self.photo)
        self.lib.add_row(self.photo)

    def crop(self, face_id):
        return photos.face_crop(self.lib.library, face_id)

    def kept(self, face_id):
        return self.lib.rows("SELECT crop_image FROM faces WHERE id = ?", (face_id,))[0][0]

    def test_a_kept_crop_is_served_as_it_is(self):
        face = self.lib.add_face(self.photo, [10, 10, 50, 50], crop=b"kept crop")
        self.assertEqual(self.crop(face), b"kept crop")

    def test_one_not_kept_is_cut_from_the_photo_and_kept(self):
        face = self.lib.add_face(self.photo, [10, 20, 60, 100], crop=None)
        crop = self.crop(face)
        self.assertEqual(size_of(crop), (50, 80))
        self.assertEqual(self.kept(face), crop)

    def test_a_large_face_is_cut_no_larger_than_face_detection_cuts_them(self):
        big = self.lib.photo("big.jpg", b"")
        Image.new("RGB", (1200, 1000)).save(big)
        face = self.lib.add_face(big, [0, 0, 1000, 800], crop=None)
        self.assertEqual(size_of(self.crop(face)), (256, 205))

    def test_a_box_in_the_bare_form_of_older_rows_is_read(self):
        face = self.lib.add_face(self.photo, [0, 0, 1, 1], crop=None)
        self.lib.execute("UPDATE faces SET box = '10, 20, 60, 100' WHERE id = ?", (face,))
        self.assertEqual(size_of(self.crop(face)), (50, 80))

    def test_a_box_outside_the_picture_gives_a_grey_square(self):
        face = self.lib.add_face(self.photo, [500, 500, 600, 600], crop=None)
        self.assertEqual(size_of(self.crop(face)), (100, 100))

    def test_a_box_that_cannot_be_read_is_an_error_and_nothing_is_kept(self):
        face = self.lib.add_face(self.photo, [1, 2], crop=None)
        with self.assertRaises(ValueError):
            self.crop(face)
        self.assertIsNone(self.kept(face))

    def test_a_face_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            self.crop(999999)

    def test_a_face_whose_photo_is_gone_is_not_found(self):
        face = self.lib.add_face(self.photo, [10, 10, 50, 50], crop=None)
        os.remove(self.photo)
        with self.assertRaises(NotFound):
            self.crop(face)

    def test_asking_of_a_library_that_is_not_there_does_not_create_it(self):
        from tagpup.core.library import Library
        missing = Library(os.path.join(self.lib.root, "missing.db"))
        with self.assertRaises(NotFound):
            photos.face_crop(missing, 1)
        self.assertFalse(os.path.exists(missing.path))


if __name__ == "__main__":
    unittest.main()

"""tagpup.services.photos.rotate: clicking Rotate Left or Rotate Right.

The file is stood in for: what ExifTool and Pillow do is tested in
test_rotate_keeps_the_photo.py. What is checked here is the service's part -- what it
writes to the library, and that its Result says what changed and what did not.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402
from tagpup.store.faces import turned_box  # noqa: E402


class RotatingAPhoto(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("beach.jpg")
        # The row describes its file: a rotation stamps only such a row (#249).
        stat = os.stat(self.photo)
        self.stamp = (stat.st_mtime, stat.st_size)
        self.lib.add_row(self.photo, mtime=stat.st_mtime, size=stat.st_size)
        self.face = self.lib.add_face(self.photo, [0, 0, 10, 8])

    def rotate(self, oriented=False, fails=None):
        with mock.patch("tagpup.files.images.shown_size", return_value=(40, 30, oriented)), \
                mock.patch("tagpup.files.metadata.rotate_image_file", return_value=6,
                           side_effect=fails):
            return photos.rotate(self.lib.library, self.photo, "right", "exiftool")

    def test_it_says_what_changed(self):
        result = self.rotate()
        self.assertEqual((result.attempted, result.changed, result.errors), (1, 1, []))
        stat = os.stat(self.photo)
        self.assertEqual((result.details["mtime"], result.details["size"]),
                         (stat.st_mtime, stat.st_size))
        self.assertEqual(result.details["orientation"], 6)

    def test_the_index_row_gets_the_files_new_mtime_and_size(self):
        self.rotate()
        stat = os.stat(self.photo)
        self.assertEqual(self.lib.rows("SELECT mtime, size FROM photos"),
                         [(stat.st_mtime, stat.st_size)])

    def test_face_boxes_stay_where_pillow_shows_the_stored_pixels(self):
        result = self.rotate(oriented=False)
        self.assertEqual(self.lib.rows("SELECT box, (SELECT jpeg FROM face_crops c WHERE c.face_id = faces.id) FROM faces"),
                         [(json.dumps([0, 0, 10, 8]), b"crop")])
        self.assertEqual(result.details["faces_turned"], 0)

    def test_face_boxes_turn_where_pillow_turns_the_picture(self):
        result = self.rotate(oriented=True)
        box, crop = self.lib.rows("SELECT box, (SELECT jpeg FROM face_crops c WHERE c.face_id = faces.id) FROM faces")[0]
        self.assertEqual(json.loads(box), turned_box([0, 0, 10, 8], "right", 40, 30))
        self.assertIsNone(crop, "a crop cut from the old box must be cut again")
        self.assertEqual(result.details["faces_turned"], 1)

    def test_a_failed_rotation_changes_nothing_and_says_why(self):
        result = self.rotate(fails=RuntimeError("the file now says 1"))
        self.assertFalse(result.ok)
        self.assertEqual((result.attempted, result.changed), (1, 0))
        self.assertIn("the file now says 1", result.message())
        self.assertEqual(self.lib.rows("SELECT mtime, size FROM photos"), [self.stamp])

    def test_a_row_the_file_has_moved_on_from_is_not_stamped(self):
        self.lib.execute("UPDATE photos SET mtime = 1.0, size = 1")
        self.rotate()
        self.assertEqual(self.lib.rows("SELECT mtime, size FROM photos"), [(1.0, 1)])


if __name__ == "__main__":
    unittest.main()

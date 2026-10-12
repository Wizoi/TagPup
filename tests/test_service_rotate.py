"""tagpup.services.photos.rotate: clicking Rotate Left or Rotate Right.

ExifTool is the table of files of tests/fake_exiftool.py and Pillow's view of the picture is stood in for: what the real tools
do is tested in test_rotate_keeps_the_photo.py and test_rotate_is_journaled.py. What is checked here is the service's part --
what it writes to the library, and that its Result says what changed and what did not.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_exiftool  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402
from tagpup.store.faces import turned_box  # noqa: E402


class RotatingAPhoto(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.exiftool = fake_exiftool.standing_in(self)
        self.photo = self.lib.photo("beach.jpg")
        self.exiftool.hold(self.photo, EXIF__Orientation=6)
        # The row describes its file: a rotation stamps only such a row (#249).
        stat = os.stat(self.photo)
        self.stamp = (stat.st_mtime, stat.st_size)
        self.lib.add_row(self.photo, mtime=stat.st_mtime, size=stat.st_size)
        self.face = self.lib.add_face(self.photo, [0, 0, 10, 8])

    def rotate(self, oriented=False, direction="right"):
        # Pillow shows the file as it is after the turn: 30 wide and 40 tall, so it was 40 wide and 30 tall before.
        with mock.patch("tagpup.files.images.shown_size", return_value=(30, 40, oriented)):
            return photos.rotate(self.lib.library, self.photo, direction, "exiftool")

    def test_it_says_what_changed(self):
        result = self.rotate()
        self.assertEqual((result.attempted, result.changed, result.errors), (1, 1, []))
        stat = os.stat(self.photo)
        self.assertEqual((result.details["mtime"], result.details["size"]),
                         (stat.st_mtime, stat.st_size))
        self.assertEqual(result.details["orientation"], 3)
        self.assertEqual(self.exiftool.fields[next(iter(self.exiftool.fields))]["EXIF:Orientation"], "3")
        self.assertEqual(self.lib.rows("SELECT operation, status FROM changes"), [("rotate", "applied")])
        self.assertEqual(result.details["with_rows"], 1)

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

    def test_a_direction_that_is_not_one_is_refused_before_the_file_is_touched(self):
        # The "rotate direction" kind (docs/findings.md, #224): the route checked it, the
        # service took anything, and the file layer raised on it.
        result = photos.rotate(self.lib.library, self.photo, "up", "exiftool")
        self.assertEqual("Direction must be 'left' or 'right'", result.refused)
        self.assertEqual((result.changed, result.errors), (0, []))
        self.assertEqual(self.exiftool.writes, 0)

    def test_a_failed_rotation_changes_nothing_and_says_why(self):
        from tagpup.core import paths
        self.exiftool.fails.add(paths.key(self.photo))
        result = self.rotate()
        self.assertFalse(result.ok)
        self.assertEqual((result.attempted, result.changed), (1, 0))
        self.assertIn("locked", result.message())
        self.assertEqual(self.lib.rows("SELECT mtime, size FROM photos"), [self.stamp])
        self.assertEqual(self.lib.rows("SELECT box FROM faces"), [(json.dumps([0, 0, 10, 8]),)])

    def test_a_row_the_file_has_moved_on_from_is_not_stamped(self):
        self.lib.execute("UPDATE photos SET mtime = 1.0, size = 1")
        self.rotate()
        self.assertEqual(self.lib.rows("SELECT mtime, size FROM photos"), [(1.0, 1)])

    def test_a_photo_cut_short_is_refused_and_nothing_is_written(self):
        from tagpup.core import paths
        self.exiftool.cut_short.add(paths.key(self.photo))
        result = self.rotate()
        self.assertFalse(result.ok)
        self.assertEqual(self.exiftool.writes, 0)
        self.assertEqual(self.lib.rows("SELECT COUNT(*) FROM changes"), [(0,)])


if __name__ == "__main__":
    unittest.main()

"""A write that records part of a file stamps a row only if the row described the file
just before the write (docs/findings.md, #249).

The same class as #247: a row read from its file, and then the file changed by another
program -- a date set elsewhere -- was still stamped by a later tag, caption or rotate
write, and claimed to match a file whose other fields it never read. The scan trusted it
from then on. Now a partial write stamps a row only when the row's stamp is the file's
from just before the write (within the scan's 0.1 s), and otherwise leaves it for the
scan to read. Real ExifTool, on JPEGs made here; rows as the indexer makes them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, write_outside  # noqa: E402

from tagpup.services import photos as photo_actions, tagging  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

ELSEWHERE = "2011:05:06 07:08:09"


class ARowTheFileHasMovedOnFrom(ReadFilesCase):
    def setUp(self):
        super().setUp()
        self.a = self.make_read("a.jpg")
        self.read_at = self.stamp_of_row()
        write_outside(self.a, {"EXIF:DateTimeOriginal": ELSEWHERE})
        self.assertNotEqual(self.read_at, self.stamp_of_file())

    def stamp_of_row(self):
        return self.rows("SELECT mtime, size FROM photos WHERE path = ?", (self.a,))[0]

    def stamp_of_file(self):
        stat = os.stat(self.a)
        return (stat.st_mtime, stat.st_size)

    def assert_not_stamped(self):
        self.assertEqual(self.read_at, self.stamp_of_row(),
                         "a partial write stamped a row that did not describe its file")

    def test_is_not_stamped_by_a_tag_write(self):
        self.assertEqual(1, self.add([self.a]).changed)
        self.assert_not_stamped()
        self.assertEqual(["Beach", "Harbour"], self.indexed(self.a))

    def test_is_not_stamped_by_a_caption_write(self):
        self.assertEqual(1, tagging.write_suggestions(self.library, [(self.a, [], "Quay at dusk")], EXIFTOOL).changed)
        self.assert_not_stamped()

    def test_is_not_stamped_by_a_rotation(self):
        self.assertTrue(photo_actions.rotate(self.library, self.a, "left", EXIFTOOL).ok)
        self.assert_not_stamped()


class ARowThatDescribesItsFile(ReadFilesCase):
    def test_is_stamped_by_a_rotation(self):
        a = self.make_read("a.jpg")
        self.assertTrue(photo_actions.rotate(self.library, a, "left", EXIFTOOL).ok)
        stat = os.stat(a)
        self.assertEqual([(stat.st_mtime, stat.st_size)],
                         self.rows("SELECT mtime, size FROM photos WHERE path = ?", (a,)))

    def test_describes_within_the_scans_tolerance(self):
        self.assertTrue(store_photos.describes(10.0, 5, (10.05, 5)))
        self.assertFalse(store_photos.describes(10.0, 5, (10.2, 5)))
        self.assertFalse(store_photos.describes(10.0, 5, (10.0, 6)))
        self.assertFalse(store_photos.describes(None, None, (10.0, 5)))
        self.assertFalse(store_photos.describes(10.0, 5, None))


if __name__ == "__main__":
    unittest.main()

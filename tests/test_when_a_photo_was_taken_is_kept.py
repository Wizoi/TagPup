"""When a photo was taken is kept in the library, in photos.taken and photos.year, by
tagpup.store.photos.date_photos at every write of the photo's metadata or path; readers
read the columns rather than parse the metadata each for themselves (#67).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.store import db, photos  # noqa: E402


class Kept(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("a.jpg")
        self.lib.add_row(self.photo)
        stat = os.stat(self.photo)
        self.stamp = (stat.st_mtime, stat.st_size)

    def dates(self, photo=None):
        return self.lib.rows("SELECT taken, year FROM photos WHERE path = ?", (photo or self.photo,))[0]

    def test_a_time_shift_moves_it(self):
        photos.record_reads(self.lib.library.path, [{
            "path": self.photo, "raw_metadata": {"EXIF:DateTimeOriginal": "2021:07:01 09:30:00"},
            "mtime": self.stamp[0], "size": self.stamp[1]}])
        self.assertEqual(("2021:07:01 09:30:00", 2021), self.dates())

    def test_a_rename_to_a_dated_name_gives_it_a_year(self):
        renamed = os.path.join(self.lib.photos, "2018 Heats.jpg")
        photos.move_rows(self.lib.library.path, {self.photo: renamed})
        self.assertEqual((None, 2018), self.dates(renamed))

    def test_indexing_records_it(self):
        conn = db.connect(self.lib.library.path)
        try:
            photos.record_indexed(conn, self.photo, {"mtime": 1.0, "size": 1, "tags": [], "captions": [],
                                                     "raw_metadata": {"XMP:CreateDate": "2015:01:02 03:04:05"}})
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(("2015:01:02 03:04:05", 2015), self.dates())

    def test_a_photo_with_no_date_has_none(self):
        self.assertEqual((None, None), self.dates())


if __name__ == "__main__":
    unittest.main()

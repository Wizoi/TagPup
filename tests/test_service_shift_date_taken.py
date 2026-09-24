"""tagpup.services.photos.shift_date_taken: Time Shift.

ExifTool is stood in for; test_time_shift_tells_the_index.py runs the real thing. The
service's part: it reports what ExifTool changed rather than what it asked for, and
records what the files hold afterwards.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402

SHIFTED = {"EXIF:DateTimeOriginal": "2024:07:04 10:30:00"}


class ShiftingDateTaken(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.shot = [self.lib.photo(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        for path in self.shot:
            self.lib.add_row(path, mtime=1.0, size=1)

    def shift(self, written=2, fails=None):
        def read_back(paths, people=None):
            # The third could not be read after the shift.
            return [{"path": p, "mtime": 5.0, "size": 7, "raw_metadata": dict(SHIFTED)}
                    for p in paths[:2]] + [{"path": paths[2], "raw_metadata": {}}]
        reader = mock.MagicMock()
        reader.return_value.batch_read.side_effect = read_back
        with mock.patch("tagpup.files.times.shift_date_taken", return_value=written, side_effect=fails), \
                mock.patch("tagpup.files.metadata.MetadataExtractor", reader):
            return photos.shift_date_taken(self.lib.library, self.shot, 30, "exiftool")

    def rows(self):
        return {path: (json.loads(raw), mtime, size) for path, raw, mtime, size in
                self.lib.rows("SELECT path, raw_metadata, mtime, size FROM photos")}

    def test_it_counts_what_exiftool_wrote_not_what_it_was_asked(self):
        result = self.shift(written=2)
        self.assertEqual((result.attempted, result.changed, result.errors), (3, 2, []))

    def test_the_index_records_what_the_files_hold_now(self):
        self.shift()
        rows = self.rows()
        for path in self.shot[:2]:
            self.assertEqual(rows[path], (SHIFTED, 5.0, 7))
        # Nothing read back for the third: its row is left as it was.
        self.assertEqual(rows[self.shot[2]], ({}, 1.0, 1))

    def test_a_shift_that_fails_records_nothing(self):
        result = self.shift(fails=OSError("ExifTool is not installed"))
        self.assertFalse(result.ok)
        self.assertEqual(result.message(), "ExifTool is not installed")
        self.assertTrue(all(row == ({}, 1.0, 1) for row in self.rows().values()))


if __name__ == "__main__":
    unittest.main()

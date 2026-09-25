"""tagpup.services.photos.shift_date_taken: Time Shift.

ExifTool is stood in for; test_time_shift_tells_the_index.py runs the real thing, and
tests/test_file_journal.py its crashes and its undo. The service's part: it moves each
date a photo holds by the minutes asked, reports the files it wrote rather than those it
was asked about, and records what the files hold afterwards.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402

TAKEN = "2024:07:04 10:00:00"
SHIFTED = "2024:07:04 10:30:00"


class Dates:
    """ExifTool over a table of each photo's fields: read, and written."""

    def __init__(self, files, refuses=()):
        self.files, self.refuses = files, set(refuses)

    def get_tags(self, photo_paths, tags=None):
        return [dict(self.files[p], SourceFile=p) for p in photo_paths]

    def set_tags(self, photo_paths, tags=None, params=None):
        if photo_paths[0] in self.refuses:
            raise RuntimeError("the file is locked")
        self.files[photo_paths[0]].update(tags)

    def execute(self, *args):
        return ""


class ShiftingDateTaken(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.shot = [self.lib.photo(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        for path in self.shot:
            self.lib.add_row(path, mtime=1.0, size=1)
        # The third holds no date to move.
        self.files = {self.shot[0]: {"EXIF:DateTimeOriginal": TAKEN, "EXIF:CreateDate": TAKEN},
                      self.shot[1]: {"EXIF:DateTimeOriginal": TAKEN},
                      self.shot[2]: {}}

    def shift(self, refuses=()):
        et = Dates(self.files, refuses)
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = et
        session.return_value.__exit__.return_value = False
        reader = mock.MagicMock()
        reader.return_value.batch_read.side_effect = lambda paths, people=None: [{"path": p} for p in paths]
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session), \
                mock.patch("tagpup.files.metadata.MetadataExtractor", reader):
            return photos.shift_date_taken(self.lib.library, self.shot, 30, "exiftool")

    def rows(self):
        return {path: (json.loads(raw), mtime, size) for path, raw, mtime, size in
                self.lib.rows("SELECT path, raw_metadata, mtime, size FROM photos")}

    def test_it_counts_the_files_it_wrote_not_what_it_was_asked(self):
        result = self.shift()
        self.assertEqual((result.attempted, result.changed, result.errors), (3, 2, []))
        self.assertEqual([path for path, _why in result.skipped], [self.shot[2]])

    def test_every_date_a_photo_holds_moves_and_no_other_is_made(self):
        self.shift()
        self.assertEqual(self.files[self.shot[0]], {"EXIF:DateTimeOriginal": SHIFTED, "EXIF:CreateDate": SHIFTED})
        self.assertEqual(self.files[self.shot[1]], {"EXIF:DateTimeOriginal": SHIFTED})

    def test_the_index_records_what_the_files_hold_now(self):
        self.shift()
        rows = self.rows()
        for path in self.shot[:2]:
            stat = os.stat(path)
            raw, mtime, size = rows[path]
            self.assertEqual(raw["EXIF:DateTimeOriginal"], SHIFTED)
            self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        # Nothing written to the third: its row is left as it was.
        self.assertEqual(rows[self.shot[2]], ({}, 1.0, 1))

    def test_a_file_that_cannot_be_written_is_an_error_and_keeps_its_row(self):
        result = self.shift(refuses=[self.shot[1]])
        self.assertEqual((result.changed, result.message()), (1, "the file is locked"))
        self.assertEqual(self.files[self.shot[1]], {"EXIF:DateTimeOriginal": TAKEN})
        self.assertEqual(self.rows()[self.shot[1]], ({}, 1.0, 1))


if __name__ == "__main__":
    unittest.main()

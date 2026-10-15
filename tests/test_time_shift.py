"""Shift Date Taken (F1: a photo write is journaled, read back, and tells the index).

The owner shifts a camera's clock error over a folder. Every date a photo holds moves by the minutes (and none is made),
the row follows the file under the names the scan reads (a photo whose only date is XMP:CreateDate included, #270), the
answer counts the files written and not those asked about (#249), a file that cannot be written is an error and keeps its
row, and Undo puts files and rows back. Real ExifTool on JPEGs made here; the rows are the indexer's (photo_rows).

Lifted: test_time_shift_records_every_date (#270), test_time_shift_tells_the_index and test_service_shift_date_taken (#249),
which ran the same shift three times, once against a table standing in for ExifTool. Mutation proofs: date_shift_plan
shifting only the first field it finds; `changed` reported as the number asked; the photos written as files only (no row,
no journal); the route not calling shift_date_taken.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from handler_harness import Library as RouteLibrary  # noqa: E402
from test_file_journal import EXIFTOOL, SHIFTED, TAKEN, FilesCase, field_of, requires_exiftool, write_outside  # noqa: E402

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db  # noqa: E402


@requires_exiftool
class ShiftingACamerasClock(FilesCase):
    def setUp(self):
        super().setUp()
        self.b = self.make("b.jpg", tags=(), taken=TAKEN)
        # a holds two dates and c only XMP:CreateDate; a row is as the scan stores it: every group's CreateDate and the bare name.
        self.a = self.make("a.jpg", tags=(), taken=TAKEN, row=False)
        self.xmp = self.make("c.jpg", tags=(), row=False)
        write_outside(self.a, {"EXIF:CreateDate": TAKEN})
        write_outside(self.xmp, {"XMP:CreateDate": TAKEN})
        conn = db.connect(self.library.path)
        photo_rows.add_read(conn, self.a, {"EXIF:DateTimeOriginal": TAKEN, "EXIF:CreateDate": TAKEN, "CreateDate": TAKEN})
        photo_rows.add_read(conn, self.xmp, {"XMP:CreateDate": TAKEN, "CreateDate": TAKEN})
        conn.commit()
        conn.close()
        self.undated = self.make("d.jpg", tags=("Beach",))
        self.broken = os.path.join(self.folder, "broken.jpg")
        with open(self.broken, "wb") as handle:
            handle.write(b"not a picture")
        self.shot = [self.a, self.b, self.xmp, self.undated, self.broken]

    def row(self, path):
        raw, mtime, size, taken = self.rows("SELECT raw_metadata, mtime, size, taken FROM photos WHERE path = ?", (path,))[0]
        return json.loads(raw), (mtime, size), taken

    def test_every_date_moves_the_rows_follow_the_answer_counts_the_files_written_and_undo_puts_them_back(self):
        stamp_before = self.row(self.undated)[1]
        result = photo_actions.shift_date_taken(self.library, self.shot, 30, EXIFTOOL)
        self.assertEqual((5, 3, []), (result.attempted, result.changed, result.errors))
        self.assertEqual({self.undated, self.broken}, {path for path, _why in result.skipped},
                         "a photo holding no date, and one ExifTool cannot read, are skipped and not counted")
        for path in (self.a, self.b):
            raw, stamp, _taken = self.row(path)
            self.assertEqual(SHIFTED, raw["EXIF:DateTimeOriginal"], path)
            self.assertEqual((os.stat(path).st_mtime, os.stat(path).st_size), stamp, "the file's new stamp")
        self.assertEqual(SHIFTED, field_of(self.a, "EXIF:CreateDate"), "every date a photo holds moves")
        raw, _stamp, taken = self.row(self.xmp)
        self.assertEqual(SHIFTED, field_of(self.xmp, "XMP:CreateDate"))
        self.assertIn("10:30", taken or "", "when it was taken did not follow the file")
        self.assertEqual((SHIFTED, SHIFTED), (raw.get("XMP:CreateDate"), raw.get("CreateDate")))
        self.assertEqual(stamp_before, self.row(self.undated)[1], "nothing written: its row is left as it was")

        self.assertEqual(3, self.undo(result.details["change"]).changed)
        self.assertEqual(TAKEN, field_of(self.xmp, "XMP:CreateDate"))
        raw, _stamp, taken = self.row(self.xmp)
        self.assertIn("10:00", taken or "")
        self.assertEqual((TAKEN, TAKEN), (raw.get("XMP:CreateDate"), raw.get("CreateDate")))
        self.assertEqual(TAKEN, self.row(self.a)[0]["EXIF:DateTimeOriginal"])

    def test_a_file_that_cannot_be_written_is_an_error_and_keeps_its_row(self):
        real = ExifToolSession.set_tags

        def set_tags(session, photo_paths, tags=None, params=None):
            if self.b in photo_paths:
                raise RuntimeError("the file is locked")
            return real(session, photo_paths, tags=tags, params=params)
        before = self.row(self.b)
        with mock.patch.object(ExifToolSession, "set_tags", set_tags):
            result = photo_actions.shift_date_taken(self.library, [self.a, self.b], 30, EXIFTOOL)
        self.assertEqual(1, result.changed)
        self.assertIn("the file is locked", result.message())
        self.assertEqual(TAKEN, field_of(self.b, "EXIF:DateTimeOriginal"))
        self.assertEqual(before, self.row(self.b))


@requires_exiftool
class TheTimeShiftPage(unittest.TestCase):
    def test_it_reports_the_photos_it_changed_not_the_ones_it_tried_and_the_index_has_the_new_date(self):
        lib = RouteLibrary(self)
        made = []
        for name in ("a.jpg", "b.jpg"):
            from PIL import Image

            path = os.path.join(lib.photos, name)
            Image.new("RGB", (8, 8), (40, 60, 80)).save(path)
            write_outside(path, {"EXIF:DateTimeOriginal": TAKEN, "EXIF:Model": "Harbour Cam"})
            made.append(path)
        with open(os.path.join(lib.photos, "broken.jpg"), "wb") as handle:
            handle.write(b"not a picture")
        conn = db.connect(lib.db_path)
        for path in made:
            photo_rows.add_read(conn, path, {"EXIF:DateTimeOriginal": TAKEN, "EXIF:Model": "Harbour Cam"})
        conn.commit()
        conn.close()
        status, reply = lib.post("/api/folder/time-shift", {
            "folder_path": lib.photos.replace(os.sep, "/"), "camera_model": "All Cameras", "shift_minutes": 30})
        self.assertEqual(200, status, reply)
        self.assertEqual((True, 2, 3), (reply["success"], reply["updated_count"], reply["requested_count"]))
        # The folder is typed as the browser sends it, walked cold, and its photos come back in their own case (the walk
        # once used the lower-cased key).
        self.assertLessEqual(set(made), {photo["path"] for photo in reply["updated_photos"]})
        for path in made:
            raw, mtime, size = lib.rows("SELECT raw_metadata, mtime, size FROM photos WHERE path = ?", (path,))[0]
            self.assertEqual(SHIFTED, json.loads(raw)["EXIF:DateTimeOriginal"], path)
            self.assertEqual((os.stat(path).st_mtime, os.stat(path).st_size), (mtime, size), path)


if __name__ == "__main__":
    unittest.main()

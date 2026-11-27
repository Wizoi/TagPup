"""Shift Date Taken (F1: a photo write is journaled, read back, and tells the index).

The owner shifts a camera's clock error over a folder. Every date a photo holds moves by the minutes (and none is made),
the row follows the file under the names the scan reads (a photo whose only date is XMP:CreateDate included, #270), the
answer counts the files written and not those asked about (#249), a file that cannot be written is an error and keeps its
row, and Undo puts files and rows back. Real ExifTool on JPEGs made here; the rows are the indexer's (photo_rows). A file
that cannot be written is one a viewer holds open: neither the patch in place nor ExifTool's rewrite may write it (a
stand-in raising for its path no longer reached it: the patch hands ExifTool a copy of the header, not the photo).

Lifted: test_time_shift_records_every_date (#270), test_time_shift_tells_the_index and test_service_shift_date_taken (#249),
which ran the same shift three times, once against a table standing in for ExifTool. Mutation proofs: date_shift_plan
shifting only the first field it finds; `changed` reported as the number asked; the photos written as files only (no row,
no journal); the route not calling shift_date_taken. The page's Time Shift is sent as the page sends it, to POST
/api/photos/write op shift (tests/page_writes.py); the order it shows the folder in is the page's own
(tests/frontend/shifting-a-folders-date-taken.test.mjs).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_writes  # noqa: E402
import photo_rows  # noqa: E402
from handler_harness import Library as RouteLibrary  # noqa: E402
from test_a_photo_patched_in_place import open_elsewhere  # noqa: E402
from test_file_journal import EXIFTOOL, SHIFTED, TAKEN, FilesCase, field_of, requires_exiftool, write_outside  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values  # noqa: E402
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
        # Each photo's new Date Taken as the library spells it, told once by the service for the reply and the
        # folder's records alike (#3292): the shifted date for each photo written, none for the one holding no date.
        taken_of = result.details[photo_actions.TAKEN_OF]
        self.assertEqual({path: self.row(path)[2] for path in (self.a, self.b, self.xmp)},
                         {path: taken_of[paths.key(path)] for path in (self.a, self.b, self.xmp)})
        self.assertTrue(all("10:30" in taken_of[paths.key(path)] for path in (self.a, self.b, self.xmp)), taken_of)
        self.assertIsNone(taken_of[paths.key(self.undated)])

        self.assertEqual(3, self.undo(result.details["change"]).changed)
        self.assertEqual(TAKEN, field_of(self.xmp, "XMP:CreateDate"))
        raw, _stamp, taken = self.row(self.xmp)
        self.assertIn("10:00", taken or "")
        self.assertEqual((TAKEN, TAKEN), (raw.get("XMP:CreateDate"), raw.get("CreateDate")))
        self.assertEqual(TAKEN, self.row(self.a)[0]["EXIF:DateTimeOriginal"])

    def test_a_file_that_cannot_be_written_is_an_error_and_keeps_its_row(self):
        # b is open in a viewer (Explorer's preview, the Photos app: reading, letting others read, write and delete).
        before = self.row(self.b)
        with open_elsewhere(self.b, 0x7):
            result = photo_actions.shift_date_taken(self.library, [self.a, self.b], 30, EXIFTOOL)
        self.assertEqual(1, result.changed)
        self.assertEqual([self.b], [path for path, _why in result.errors], result.message())
        self.assertIn("held open by", result.message())
        self.assertEqual(TAKEN, field_of(self.b, "EXIF:DateTimeOriginal"))
        self.assertEqual(before, self.row(self.b))


    def test_a_photo_written_whose_read_back_does_not_answer_is_told_no_new_date_and_no_record(self):
        # The share stops answering once b's write has landed: the read back of it after the write is not answered. Its
        # Date Taken is not known, which is not "none": the service tells none for it, and no record the folder's kept
        # scan would take in place of the one it holds (the page showed "Unknown", #3300).
        real_read = field_values._read

        def read(et, photo_paths, wanted, *args, **kwargs):
            found = real_read(et, photo_paths, wanted, *args, **kwargs)
            if not wanted:   # the whole record of each, as the read back after the write asks it (Fields.whole)
                found[paths.key(self.b)] = field_values.NotAnswered("ExifTool did not answer in 1 s")
            return found
        with mock.patch.object(field_values, "_read", read):
            result = photo_actions.shift_date_taken(self.library, [self.a, self.b], 30, EXIFTOOL)
        self.assertEqual((2, 2, []), (result.attempted, result.changed, result.errors))
        self.assertEqual(SHIFTED, field_of(self.b, "EXIF:DateTimeOriginal"), "its write landed")
        taken_of = result.details[photo_actions.TAKEN_OF]
        self.assertNotIn(paths.key(self.b), taken_of, "a date not read is not told as none")
        self.assertIn("10:30", taken_of[paths.key(self.a)])
        self.assertEqual([paths.key(self.a)], [paths.key(meta["path"]) for meta in result.details["records"]])


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
        shift = page_writes.shift_all(lib.library, lib.photos, 30)
        self.assertEqual(3, len(shift["changes"]), "the page sends the folder's photos, the one it cannot read too")
        status, reply = lib.post(page_writes.ROUTE, shift)
        self.assertEqual((200, True, 2, 3), (status, reply["success"], reply["changed"], reply["attempted"]), reply)
        # The photos written come back in their own case, as the page named them.
        self.assertEqual(set(made), set(reply["written"]))
        for path in made:
            raw, mtime, size = lib.rows("SELECT raw_metadata, mtime, size FROM photos WHERE path = ?", (path,))[0]
            self.assertEqual(SHIFTED, json.loads(raw)["EXIF:DateTimeOriginal"], path)
            self.assertEqual((os.stat(path).st_mtime, os.stat(path).st_size), (mtime, size), path)


if __name__ == "__main__":
    unittest.main()

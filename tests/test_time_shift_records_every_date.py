"""Shift Date Taken records every date it writes in the photo's row, XMP:CreateDate among
them (docs/findings.md, #270).

The shift moved XMP:CreateDate in the file, but the row recorded only the fields of
fields.METADATA_FIELDS, which names CreateDate bare and not under XMP: a photo whose only
date is XMP:CreateDate kept its old `taken`, under a stamp that told the scan the row
was current. The scan stores every group's CreateDate (it asks for the bare name), so
the row records a written field the scan reads, under its group and its bare name. Real
ExifTool, on a JPEG made here; the row seeded as the scan stores it.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, SHIFTED, TAKEN, FilesCase, field_of, write_outside  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402


class APhotoWhoseOnlyDateIsXmpCreateDate(FilesCase):
    def setUp(self):
        super().setUp()
        from PIL import Image

        self.photo = os.path.join(self.folder, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photo, "JPEG")
        write_outside(self.photo, {"XMP:CreateDate": TAKEN})
        stat = os.stat(self.photo)
        # As the scan stores it: every group's CreateDate, and the bare name.
        raw = {"XMP:CreateDate": TAKEN, "CreateDate": TAKEN}
        self.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, taken)"
                     " VALUES (?, ?, ?, '[]', '[]', ?, ?)",
                     (self.photo, stat.st_mtime, stat.st_size, json.dumps(raw), "2024-07-04 10:00:00"))

    def recorded(self):
        taken, raw = self.rows("SELECT taken, raw_metadata FROM photos WHERE path = ?", (self.photo,))[0]
        return taken, json.loads(raw)

    def test_its_row_follows_the_shift_and_the_undo(self):
        result = photo_actions.shift_date_taken(self.library, [self.photo], 30, EXIFTOOL)
        self.assertEqual(1, result.changed)
        self.assertEqual(SHIFTED, field_of(self.photo, "XMP:CreateDate"))
        taken, raw = self.recorded()
        self.assertIn("10:30", taken or "", "when it was taken did not follow the file")
        self.assertEqual((SHIFTED, SHIFTED), (raw.get("XMP:CreateDate"), raw.get("CreateDate")))

        self.assertEqual(1, self.undo(result.details["change"]).changed)
        self.assertEqual(TAKEN, field_of(self.photo, "XMP:CreateDate"))
        taken, raw = self.recorded()
        self.assertIn("10:00", taken or "")
        self.assertEqual((TAKEN, TAKEN), (raw.get("XMP:CreateDate"), raw.get("CreateDate")))


class WhatARowRecords(unittest.TestCase):
    def test_a_field_the_scan_reads_under_its_bare_name(self):
        for field in ("XMP:CreateDate", "EXIF:CreateDate", "XMP:Subject", "IPTC:Keywords", "XMP-xmpMM:DocumentID"):
            self.assertTrue(fields.scan_reads(field), field)

    def test_not_one_it_does_not(self):
        # Keyword and caption writes set these too; the scan never reads them, and a row
        # holding them would not be the row a scan makes.
        for field in ("EXIF:XPKeywords", "EXIF:ImageDescription", "EXIF:XPComment", "EXIF:SubSecTimeOriginal"):
            self.assertFalse(fields.scan_reads(field), field)


if __name__ == "__main__":
    unittest.main()

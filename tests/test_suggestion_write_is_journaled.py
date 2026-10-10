"""The write of suggested tags is one change of photo files, which undo
reverses (docs/findings.md, #266).

It wrote each photo through its own ExifTool session with no record of what the files
held before, so a write could not be undone but from the _original copies ExifTool left
beside each file. It now goes through the journal the page's bulk writes use
(tagpup.services.file_changes). Real ExifTool, on JPEGs made here; the rows seeded as
the indexer stores them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, FilesCase, field_of, keywords_of  # noqa: E402

from tagpup.services import tagging  # noqa: E402


class TheWrite(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make("a.jpg"), self.make("b.jpg", ["Beach", "Relay"])

    def write(self):
        return not tagging.write_suggestions(
            self.library, [(path, ["Activity/Rowing"], "") for path in (self.a, self.b)], EXIFTOOL).errors

    def test_is_a_change_that_undo_reverses(self):
        captions = {path: field_of(path, "XMP:Description") for path in (self.a, self.b)}
        self.assertTrue(self.write())
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(self.a))
        self.assertEqual(["Activity/Rowing", "Beach"], self.indexed(self.a))
        change = self.last_change()
        self.assertEqual([("write suggestions", "applied")],
                         self.rows("SELECT operation, status FROM changes WHERE id = ?", (change,)))
        self.assertEqual(["done", "done"], self.states(change))
        self.assertEqual([], [name for name in os.listdir(self.folder) if name.endswith("_original")],
                         "ExifTool kept a copy beside a file: the journal is the way back")

        undone = self.undo(change)
        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], keywords_of(self.a))
        self.assertEqual(["Beach", "Relay"], keywords_of(self.b))
        self.assertEqual(["Beach", "Relay"], self.indexed(self.b))
        self.assertEqual(captions, {path: field_of(path, "XMP:Description") for path in (self.a, self.b)})

    def test_a_photo_found_damaged_is_skipped_and_said(self):
        # Nothing is written into a photo found damaged (tagpup.services.libraries.
        # leave_out_damaged): skipped, and returned with why.
        from tagpup.services import damaged_photos
        stat = os.stat(self.b)
        damaged_photos.remember(self.library, [(self.b, (stat.st_mtime, stat.st_size), damaged_photos.INCOMPLETE,
                                                "the last 70000 bytes are zeros", 70000)])
        self.assertTrue(self.write())
        self.assertEqual(["Beach", "Relay"], keywords_of(self.b))
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(self.a))
        result = tagging.write_suggestions(self.library, [(self.b, ["Activity/Rowing"], "")], EXIFTOOL)
        self.assertEqual(1, result.details["skipped_damaged"])
        self.assertEqual([self.b], [path for path, _why in result.skipped])


if __name__ == "__main__":
    unittest.main()

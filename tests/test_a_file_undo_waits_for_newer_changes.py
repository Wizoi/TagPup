"""A change of photo files is not undone while a newer change that touched the same
photo stands (review of pass/journal, item 2).

An undo of rows is refused while a newer change touched the same rows, naming it. An
undo of files checked only that each file still held what the change left: undoing
Smart Rename's `original names` change while the rename that came after it stood took
the kept name off the renamed photos, and undoing a write under a newer tag change took
back part of a file the newer change had planned from. Now it is refused, naming the
newer change, until that one is undone. Real ExifTool, on JPEGs made here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, field_of  # noqa: E402

from tagpup.services import photos as photo_actions  # noqa: E402

PRESERVED = "XMP-xmpMM:PreservedFileName"


class ANewerChangeOfTheSamePhoto(ReadFilesCase):
    def change_named(self, operation):
        return self.rows("SELECT MAX(id) FROM changes WHERE operation = ?", (operation,))[0][0]

    def test_a_tag_change_after_it_refuses_the_undo_until_it_is_undone(self):
        a = self.make_read("a.jpg")
        kept = photo_actions.preserve_names(self.library, [a], EXIFTOOL).details["change"]
        tagged = self.add([a]).details["change"]
        refused = self.undo(kept)
        self.assertEqual(0, refused.changed)
        self.assertIn("change %d (add to all selected)" % tagged, refused.message())
        self.assertEqual("a.jpg", field_of(a, PRESERVED))
        self.assertEqual(1, self.undo(tagged).changed)
        self.assertEqual(1, self.undo(kept).changed)
        self.assertIsNone(field_of(a, PRESERVED))

    def test_the_kept_names_wait_for_the_rename(self):
        a, b = self.make_read("IMG_0001.jpg"), self.make_read("IMG_0002.jpg")
        photo_actions.smart_rename(self.library, [a, b], "Regatta", "{grouping} - {index}", EXIFTOOL)
        kept, renamed = self.change_named("smart rename: original names"), self.change_named("smart rename")
        refused = self.undo(kept)
        self.assertEqual(0, refused.changed)
        self.assertIn("change %d (smart rename)" % renamed, refused.message())
        self.assertEqual(2, self.undo(renamed).changed)
        self.assertEqual(2, self.undo(kept).changed)
        self.assertIsNone(field_of(a, PRESERVED))


if __name__ == "__main__":
    unittest.main()

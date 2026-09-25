"""Saving one photo, and the name Smart Rename keeps of each photo before its first
rename, are changes of photo files in the journal, which can be undone
(docs/findings.md, #266).

Neither was recorded: the save wrote the photo's caption, tags and date with ExifTool of
its own, and Smart Rename wrote XMP-xmpMM:PreservedFileName into every photo lacking
one before it renamed them. Both go through the file journal now
(tagpup.services.file_changes.write_fields). Real ExifTool, on JPEGs made here; rows as
the indexer makes them.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, field_of, keywords_of, write_outside  # noqa: E402

from tagpup.services import photos as photo_actions, tagging  # noqa: E402

PRESERVED = "XMP-xmpMM:PreservedFileName"


class TheSave(ReadFilesCase):
    def test_is_a_change_that_can_be_undone(self):
        a = self.make_read("a.jpg")
        result = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"], None, EXIFTOOL, "")
        self.assertEqual((True, 1), (result.ok, result.changed))
        change = self.last_change()
        self.assertEqual(("save photo", "applied", ["done"]),
                         (self.rows("SELECT operation FROM changes WHERE id = ?", (change,))[0][0],
                          self.status(change), self.states(change)))
        self.assertEqual(change, result.details["change"])
        self.assertEqual(["Beach", "Places/Quay"], keywords_of(a))
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertIsNone(field_of(a, "XMP:Description"))
        self.assertEqual(["Beach"], self.indexed(a))

    def test_a_file_holding_it_all_is_not_written(self):
        a = self.make_read("a.jpg")
        tagging.save_photo(self.library, a, "Quay", ["Beach"], None, EXIFTOOL, "")
        written = os.stat(a).st_mtime_ns
        again = tagging.save_photo(self.library, a, "Quay", ["Beach"], None, EXIFTOOL, "")
        self.assertEqual((True, 0, None), (again.ok, again.changed, again.details["change"]))
        self.assertEqual(written, os.stat(a).st_mtime_ns)


class SmartRename(ReadFilesCase):
    def test_the_names_it_keeps_are_a_change_of_their_own(self):
        a, b = self.make_read("IMG_0001.jpg"), self.make_read("IMG_0002.jpg")
        write_outside(b, {PRESERVED: "DSC_0100.JPG"})
        result = photo_actions.smart_rename(self.library, [a, b], "Regatta", "{grouping} - {index}", EXIFTOOL)
        self.assertEqual((True, 2), (result.ok, result.changed))
        kept = self.rows("SELECT id, status FROM changes WHERE operation = 'smart rename: original names'")
        self.assertEqual(1, len(kept))
        files = self.rows("SELECT path, fields_before, fields_after, state FROM change_files WHERE change_id = ?",
                          (kept[0][0],))
        self.assertEqual([(a, {PRESERVED: []}, {PRESERVED: ["IMG_0001.jpg"]}, "done")],
                         [(p, json.loads(before), json.loads(after), s) for p, before, after, s in files])
        renamed = result.details["updated_paths"]
        self.assertEqual("IMG_0001.jpg", field_of(renamed[a], PRESERVED))
        self.assertEqual("DSC_0100.JPG", field_of(renamed[b], PRESERVED), "a name kept already was written over")


if __name__ == "__main__":
    unittest.main()

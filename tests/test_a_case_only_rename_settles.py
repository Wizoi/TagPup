"""A rename that changes only the case of a name, stopped by a crash, is settled by the
name the file has on disk (docs/findings.md, #283).

On NTFS "IMG_0002.JPG" and "img_0002.jpg" are one file, so settling found it under both
names and called it a copy it could not tell from the photo: a conflict, never renamed.
Real JPEGs made here, rows as the indexer makes them; the crash is before the rename,
then after it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, crash_at  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import file_changes  # noqa: E402


class ACaseOnlyRename(ReadFilesCase):
    def setUp(self):
        super().setUp()
        self.old = self.make_read("IMG_0002.JPG")
        self.new = os.path.join(self.folder, "img_0002.jpg")
        self.photo = self.photo_id(self.old)

    def crash(self, step):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
            with self.assertRaises(Crash):
                file_changes.rename(self.library, "smart rename", {self.old: self.new}, {}, EXIFTOOL)
        return self.last_change()

    def assert_renamed(self, change):
        self.assertEqual(1, self.settle())
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assertEqual(["img_0002.jpg"], os.listdir(self.folder))
        self.assertEqual(self.new, self.rows("SELECT path FROM photos WHERE id = ?", (self.photo,))[0][0])

    def test_stopped_before_the_rename_is_renamed(self):
        self.assert_renamed(self.crash("file writing"))

    def test_stopped_after_the_rename_is_recorded(self):
        self.assert_renamed(self.crash("file written"))

    def test_a_name_is_spelled_as_the_disk_has_it(self):
        self.assertTrue(paths.spelled_as(self.old))
        self.assertFalse(paths.spelled_as(self.new))
        self.assertFalse(paths.spelled_as(os.path.join(self.folder, "nothing.jpg")))


if __name__ == "__main__":
    unittest.main()

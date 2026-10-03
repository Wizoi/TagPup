"""A write that fails because a file is held open says which program holds it (tagpup.files.lock_owners;
docs/ARCHITECTURE.md, "File access check"): a rename of a file held exclusively -- by this very test --
names this process; a failed write names the holder the error text has no word for; a write that
succeeds, and a failure of another kind, ask nobody.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_lock_owners import hold_exclusively, release  # noqa: E402

from tagpup.files import field_values, lock_owners, names  # noqa: E402
from tagpup.services import file_changes, file_only  # noqa: E402

MSMPENG = [{"name": "MsMpEng.exe", "pid": 9, "kind": "service", "service": "WinDefend"}]
FIELDS_BEFORE, FIELDS_AFTER = {"XMP-dc:Subject": ["a"]}, {"XMP-dc:Subject": ["b"]}


@unittest.skipUnless(sys.platform == "win32", "a held file is Windows'")
class ARenameOfAHeldFile(unittest.TestCase):
    def test_names_the_process_that_holds_it(self):
        with tempfile.TemporaryDirectory(prefix="held_rename_") as folder:
            old, new = os.path.join(folder, "IMG_0001.jpg"), os.path.join(folder, "Regatta - 1.jpg")
            with open(old, "wb") as handle:
                handle.write(b"x")
            handle = hold_exclusively(old)
            try:
                with self.assertRaises(names.RenameFailed) as raised:
                    names.rename_all({old: new})
            finally:
                release(handle)
            said = raised.exception.message()
            self.assertIn("It is held open by", said)
            self.assertIn("this TagPup process", said)
            self.assertIn("Every photo was put back under its old name.", said)
            self.assertNotIn("..", said)
            self.assertTrue(os.path.exists(old))

    def test_a_rename_that_works_asks_nobody(self):
        with tempfile.TemporaryDirectory(prefix="held_rename_") as folder:
            old, new = os.path.join(folder, "IMG_0001.jpg"), os.path.join(folder, "Regatta - 1.jpg")
            with open(old, "wb") as handle:
                handle.write(b"x")
            with mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
                names.rename_all({old: new})
            self.assertTrue(os.path.exists(new))


class AFailedWrite(unittest.TestCase):
    def only(self, error):
        result = mock.Mock(details={"conflicts": []})
        with mock.patch.object(field_values, "write", side_effect=error), \
                mock.patch.object(field_values, "read_one", return_value=FIELDS_BEFORE):
            return file_only._write_one(object(), "D:\\Photos\\IMG_0001.jpg", FIELDS_BEFORE, FIELDS_AFTER, True, result)

    def test_a_file_only_write_names_the_holder(self):
        with mock.patch.object(lock_owners, "holders", return_value=MSMPENG):
            said = self.only(PermissionError(13, "Permission denied"))
        self.assertIn("It is held open by MsMpEng.exe (Windows Security / Microsoft Defender", str(said))
        self.assertNotIn("Photos", str(said).split("It is held open")[1], "the sentence adds no path")

    def test_a_failure_of_another_kind_asks_nobody(self):
        with mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
            said = self.only(RuntimeError("ExifTool said: File format error"))
        self.assertEqual("ExifTool said: File format error", str(said))

    def test_a_journaled_write_names_the_holder(self):
        library = mock.Mock(path="library.db")
        row = mock.Mock(path="D:\\Photos\\IMG_0001.jpg", id=3, stamp=None)
        error = RuntimeError("Error renaming temporary file to IMG_0001.jpg")
        with mock.patch.object(field_values, "read_one", return_value=FIELDS_BEFORE), \
                mock.patch.object(file_changes.file_journal, "withdraw") as withdraw, \
                mock.patch.object(lock_owners, "holders", return_value=MSMPENG):
            outcome, why = file_changes._after_failure(object(), library, row, FIELDS_BEFORE, FIELDS_AFTER,
                                                       "done", "withdraw", error)
        withdraw.assert_called_once()
        self.assertEqual("failed", outcome)
        self.assertIn("It is held open by MsMpEng.exe", str(why))

    def test_a_write_that_turns_out_to_have_worked_asks_nobody(self):
        library = mock.Mock(path="library.db")
        row = mock.Mock(path="D:\\Photos\\IMG_0001.jpg", id=3, stamp=None)
        with mock.patch.object(field_values, "read_one", return_value=FIELDS_AFTER), \
                mock.patch.object(file_changes, "_record_held"), \
                mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
            outcome, why = file_changes._after_failure(object(), library, row, FIELDS_BEFORE, FIELDS_AFTER,
                                                       "done", "withdraw", PermissionError(13, "Permission denied"))
        self.assertEqual(("done", None), (outcome, why))


if __name__ == "__main__":
    unittest.main()

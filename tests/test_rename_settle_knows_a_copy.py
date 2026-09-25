"""Settling a rename a crash stopped does not take a copy of the photo for the photo
(docs/findings.md, #269).

A renamed file was known by its size and modified time alone, and a copy that keeps them
-- Explorer's, robocopy's, shutil.copy2's -- lying where the photo was to go was taken
for the photo renamed: settling marked it done without renaming it and pointed the
photo's row at the copy, or, with the copy in the plan to be moved aside, moved the copy
and pointed the row at a name that no longer existed. Now a file is known by its file
id as well, which a rename keeps and a copy never shares; and a plan without one that
finds the file under both names is a conflict, the files left as they are. Real JPEGs
made here, the rows seeded as the indexer stores them; the crash is at the step before
the files are renamed.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at  # noqa: E402

from tagpup.files import names as file_names  # noqa: E402
from tagpup.services import file_changes, photos as photo_actions  # noqa: E402

NEW_NAME = "Regatta - 1 - Start.jpg"
ASIDE = "Regatta - 1 - Start_conflict_1.jpg"


class RenameCase(FilesCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("IMG_0001.jpg", caption="Start")
        self.copy = os.path.join(self.folder, NEW_NAME)
        self.photo = self.photo_id(self.a)

    def names(self):
        return sorted(os.listdir(self.folder))

    def path_of_row(self):
        return self.rows("SELECT path FROM photos WHERE id = ?", (self.photo,))[0][0]

    def state_of(self, change, path):
        return dict(self.rows("SELECT path, state FROM change_files WHERE change_id = ?", (change,))).get(path)

    def without_file_ids(self):
        """A plan that knows a file by its size and modified time alone: one written
        before file ids were recorded, or on a volume that gives none."""
        real = file_changes._fingerprint

        def fingerprint(path):
            found = real(path)
            if found:
                found.pop("ino", None)
            return found

        return mock.patch.object(file_changes, "_fingerprint", side_effect=fingerprint)


class ACopyMadeWhileTheRenameWasUnderWay(RenameCase):
    """The copy appears where the photo is to go after the plan: Smart Rename, stopped
    before it renamed anything."""

    def crash(self, plan=None):
        def reached(step):
            if step == "plan committed":
                shutil.copy2(self.a, self.copy)
                self.file_ids = {path: os.stat(path).st_ino for path in (self.a, self.copy)}
            if step == "file writing":
                raise Crash(step)

        with plan or mock.patch.object(file_changes, "_fingerprint", wraps=file_changes._fingerprint):
            with mock.patch.object(file_changes, "_reached", side_effect=reached):
                with self.assertRaises(Crash):
                    photo_actions.smart_rename(self.library, [self.a], "Regatta", "{grouping} - {index} - {caption}",
                                               EXIFTOOL)
        return self.last_change()

    def assert_left_as_they_were(self, change):
        self.assertEqual(["IMG_0001.jpg", NEW_NAME], self.names())
        self.assertEqual(self.file_ids[self.a], os.stat(self.a).st_ino)
        self.assertEqual(self.file_ids[self.copy], os.stat(self.copy).st_ino)
        self.assertEqual(self.a, self.path_of_row())
        self.assertEqual("conflict", self.state_of(change, self.a))
        self.assertEqual("applied", self.status(change))

    def test_is_not_taken_for_the_photo(self):
        change = self.crash()
        self.settle()
        self.assert_left_as_they_were(change)

    def test_nor_by_a_plan_without_file_ids(self):
        change = self.crash(self.without_file_ids())
        self.settle()
        self.assert_left_as_they_were(change)


class ACopyInTheWayMovedAside(RenameCase):
    """The copy is where the photo is to go when the rename is planned, so the plan moves
    it aside first; the rename stopped before either moved."""

    def setUp(self):
        super().setUp()
        shutil.copy2(self.a, self.copy)
        self.file_ids = {path: os.stat(path).st_ino for path in (self.a, self.copy)}

    def crash(self):
        renames = {self.a: self.copy}
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file writing")):
            with self.assertRaises(Crash):
                file_changes.rename(self.library, "smart rename", renames, file_names.aside_for(renames))
        return self.last_change()

    def test_is_moved_aside_and_the_photo_renamed(self):
        change = self.crash()
        self.settle()
        self.assertEqual([NEW_NAME, ASIDE], self.names())
        self.assertEqual(self.file_ids[self.a], os.stat(self.copy).st_ino, "the photo is not under its new name")
        self.assertEqual(self.file_ids[self.copy], os.stat(os.path.join(self.folder, ASIDE)).st_ino)
        self.assertEqual(self.copy, self.path_of_row())
        self.assertEqual("applied", self.status(change))

    def test_by_a_plan_without_file_ids_is_a_conflict_and_nothing_moves(self):
        with self.without_file_ids():
            change = self.crash()
        self.settle()
        self.assertEqual(["IMG_0001.jpg", NEW_NAME], self.names())
        self.assertEqual(self.file_ids[self.a], os.stat(self.a).st_ino)
        self.assertEqual(self.file_ids[self.copy], os.stat(self.copy).st_ino)
        self.assertEqual(self.a, self.path_of_row())
        self.assertEqual("conflict", self.state_of(change, self.a))
        self.assertIsNone(self.state_of(change, self.copy),
                          "the copy's move aside, for a rename not carried out, stayed in the change")
        self.assertEqual("applied", self.status(change))


if __name__ == "__main__":
    unittest.main()

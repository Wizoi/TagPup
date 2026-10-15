"""A Smart Rename stopped part way is finished by the next start, and the index follows the files (scenario F1).

The owner renames a folder's photos; the process is killed between the plan and the rows being moved, the share lets go
of a file, or a copy of a photo lands where one is to go. What is left is settled by what each file is: the next start
finishes the rename, a copy is not mistaken for the photo (a file is known by its file id as well as its size and modified
time: #269), a rename of the case alone settles (#283), and every row -- with its faces named by hand and its vector --
is where its file is, including in a renumbered run, a swap, and a name moved aside. A rename on a volume that gives no
file id is refused with a sentence before anything is written (owner decision 2026-10-15: no weaker recognition by size
and modified time; 791 of 791 rename plans in the three live libraries carry an id, counted that day). Real JPEGs made
here, the rows seeded as the indexer stores them; a crash is a BaseException at a step of the rename.

Lifted: test_rename_settle_knows_a_copy (its two plans-without-file-ids tests are deleted: no live plan lacks an id),
test_a_case_only_rename_settles (#283), test_smart_rename_stays_put and test_rename_keeps_the_index (15 tests of
store.photos.move_rows, the unit under every real rename here; #298 is its route guard, which inspected source and is
deleted: the real renames show the rows move). Each was broken in a scratch copy and this file fails: _holds knowing a
file by size and modified time alone with a file under both names no longer a conflict (the copy tests: the copy is taken
for the photo); _settle_renames without its case-only branch (both case-only tests); move_rows_in treating a name another
rename is leaving as taken (the renumbered run and the name moved aside); smart_rename joining every new name to the first
photo's folder (the subfolder test); rename() not refusing a volume that gives no file id (the refusal).
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import FACES_WITH_PATHS, VECTORS_WITH_PATHS, add_face, add_vector  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import names as file_names  # noqa: E402
from tagpup.services import file_changes, photos as photo_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

FORMAT = "{grouping} - {index} - {caption}"
NEW_NAME = "Regatta - 1 - Start.jpg"
ASIDE = "Regatta - 1 - Start_conflict_1.jpg"
VECTOR = b"an-embedding"


class RenameCase(FilesCase):
    def setUp(self):
        super().setUp()
        # It keeps its name already: the crash is the rename's, not that write's.
        self.a = self.make("IMG_0001.jpg", caption="Start", preserved="IMG_0001.jpg")
        self.new = os.path.join(self.folder, NEW_NAME)
        self.photo = self.photo_id(self.a)
        conn = db.connect(self.library.path)
        try:
            add_vector(conn, self.a, VECTOR)
            add_face(conn, self.a, name="Rowan Thackeray")
            add_face(conn, self.a)
            conn.commit()
        finally:
            conn.close()

    def names(self):
        return sorted(os.listdir(self.folder))

    def path_of_row(self):
        return self.rows("SELECT path FROM photos WHERE id = ?", (self.photo,))[0][0]

    def state_of(self, change, path):
        return dict(self.rows("SELECT path, state FROM change_files WHERE change_id = ?", (change,))).get(path)

    def faces_at(self, path):
        return sorted(name or "" for (name,) in self.rows("SELECT f.name FROM " + FACES_WITH_PATHS + " WHERE p.path = ?",
                                                          (path,)))

    def vector_at(self, path):
        found = self.rows("SELECT e.vector FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?", (path,))
        return found[0][0] if found else None

    def crash_smart_rename(self, step, before=None):
        def reached(name):
            if before:
                before(name)
            crash_at(step)(name)

        with mock.patch.object(file_changes, "_reached", side_effect=reached):
            with self.assertRaises(Crash):
                photo_actions.smart_rename(self.library, [self.a], "Regatta", FORMAT, EXIFTOOL)
        return self.last_change()


class AKilledRename(RenameCase):
    def assert_finished_at_the_next_start(self, step):
        change = self.crash_smart_rename(step)
        self.assertEqual(1, self.settle())
        self.assertEqual([NEW_NAME], self.names())
        self.assertEqual(self.new, self.path_of_row())
        self.assertEqual(["", "Rowan Thackeray"], self.faces_at(self.new), "a name given by hand was lost")
        self.assertEqual(VECTOR, self.vector_at(self.new))
        self.assertEqual([], self.faces_at(self.a))
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assertEqual(0, self.settle(), "a settled change was settled again")

    def test_killed_before_the_files_move_is_finished_at_the_next_start(self):
        self.assert_finished_at_the_next_start("file writing")

    def test_killed_after_they_moved_is_recorded_at_the_next_start(self):
        self.assert_finished_at_the_next_start("file written")

    def assert_case_only_settles(self, step):
        old = os.path.join(self.folder, "IMG_0002.JPG")
        lower = os.path.join(self.folder, "img_0002.jpg")
        os.rename(self.a, old)
        self.execute("UPDATE photos SET path = ? WHERE id = ?", (old, self.photo))
        self.assertTrue(paths.spelled_as(old))
        self.assertFalse(paths.spelled_as(lower))
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
            with self.assertRaises(Crash):
                file_changes.rename(self.library, "smart rename", {old: lower}, {}, EXIFTOOL)
        change = self.last_change()
        self.assertEqual(1, self.settle())
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assertEqual(["img_0002.jpg"], self.names())
        self.assertEqual(lower, self.path_of_row())

    def test_a_case_only_rename_killed_before_it_settles_by_the_name_the_disk_lists(self):
        self.assert_case_only_settles("file writing")

    def test_a_case_only_rename_killed_after_it_is_recorded(self):
        self.assert_case_only_settles("file written")

    def test_a_volume_that_gives_no_file_id_is_refused_before_anything_is_written(self):
        real = os.stat

        def without_file_id(path, *args, **kwargs):
            found = real(path, *args, **kwargs)
            return os.stat_result((found.st_mode, 0) + tuple(found)[2:])

        before = self.rows("SELECT COUNT(*) FROM changes")[0][0]
        with mock.patch.object(file_changes.os, "stat", side_effect=without_file_id):
            result = photo_actions.smart_rename(self.library, [self.a], "Regatta", FORMAT, EXIFTOOL)
        self.assertFalse(result.ok)
        self.assertIn("no file id", result.message())
        self.assertEqual((before, ["IMG_0001.jpg"], self.a), (self.rows("SELECT COUNT(*) FROM changes")[0][0],
                                                              self.names(), self.path_of_row()))


class ACopyOfThePhoto(RenameCase):
    """A copy that keeps the size and modified time -- Explorer's, robocopy's, shutil.copy2's -- is not the photo."""

    def test_made_while_the_rename_was_under_way_is_not_taken_for_it(self):
        def copy_lands(step):
            if step == "plan committed":
                shutil.copy2(self.a, self.new)
                self.ids = {path: os.stat(path).st_ino for path in (self.a, self.new)}

        change = self.crash_smart_rename("file writing", before=copy_lands)
        self.settle()
        self.assertEqual(["IMG_0001.jpg", NEW_NAME], self.names())
        self.assertEqual(self.ids, {path: os.stat(path).st_ino for path in (self.a, self.new)})
        self.assertEqual(self.a, self.path_of_row())
        self.assertEqual(("conflict", "applied"), (self.state_of(change, self.a), self.status(change)))

    def test_in_the_way_when_planned_is_moved_aside_and_the_photo_renamed(self):
        shutil.copy2(self.a, self.new)
        ids = {path: os.stat(path).st_ino for path in (self.a, self.new)}
        renames = {self.a: self.new}
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file writing")):
            with self.assertRaises(Crash):
                file_changes.rename(self.library, "smart rename", renames, file_names.aside_for(renames))
        change = self.last_change()
        self.settle()
        self.assertEqual([NEW_NAME, ASIDE], self.names())
        self.assertEqual(ids[self.a], os.stat(self.new).st_ino, "the photo is not under its new name")
        self.assertEqual(ids[self.new], os.stat(os.path.join(self.folder, ASIDE)).st_ino)
        self.assertEqual((self.new, "applied"), (self.path_of_row(), self.status(change)))


class TheIndexFollowsTheFiles(RenameCase):
    """Rows carry vectors and faces named by hand: each goes with its file, and none is merged into another."""

    def seed(self, name, vector, *face_names):
        path = self.make(name, preserved=name)
        conn = db.connect(self.library.path)
        try:
            add_vector(conn, path, vector)
            for face in face_names:
                add_face(conn, path, name=face)
            conn.commit()
        finally:
            conn.close()
        return path

    def test_a_renumbered_run_and_a_swap_move_every_row_to_its_own_file(self):
        one, two = self.seed("Run - 1.jpg", b"one", "Rowan Thackeray"), self.seed("Run - 2.jpg", b"two", "Ada Marchetti")
        three = os.path.join(self.folder, "Run - 3.jpg")
        done = file_changes.rename(self.library, "smart rename", {one: two, two: three}, {})
        self.assertEqual(2, done.moved)
        self.assertEqual((b"one", b"two", None), (self.vector_at(two), self.vector_at(three), self.vector_at(one)))
        self.assertEqual((["Rowan Thackeray"], ["Ada Marchetti"]), (self.faces_at(two), self.faces_at(three)))
        file_changes.rename(self.library, "smart rename", {two: three, three: two}, {})
        self.assertEqual((b"two", b"one"), (self.vector_at(two), self.vector_at(three)))
        self.assertEqual((["Ada Marchetti"], ["Rowan Thackeray"]), (self.faces_at(two), self.faces_at(three)))

    def test_a_file_moved_aside_takes_its_rows_and_a_name_the_index_holds_is_reported_not_merged_into(self):
        occupant = self.seed("Regatta - 1 - Start.jpg", b"occupant", "Ada Marchetti")
        renames = {self.a: occupant}
        done = file_changes.rename(self.library, "smart rename", renames, file_names.aside_for(renames))
        aside = os.path.join(self.folder, ASIDE)
        self.assertEqual({occupant: aside}, done.moved_aside)
        self.assertEqual((b"occupant", VECTOR), (self.vector_at(aside), self.vector_at(occupant)))
        self.assertEqual((["Ada Marchetti"], ["", "Rowan Thackeray"]), (self.faces_at(aside), self.faces_at(occupant)))
        # A name only the index holds (its file is gone): the file is renamed, both rows stay as they were.
        ghost = self.seed("Ghost.jpg", b"ghost", "Tobias Wren")
        os.remove(ghost)
        taken = file_changes.rename(self.library, "smart rename", {occupant: ghost}, {})
        self.assertEqual((0, [(occupant, ghost)]), (taken.moved, taken.skipped))
        self.assertEqual((["Tobias Wren"], ["", "Rowan Thackeray"]), (self.faces_at(ghost), self.faces_at(occupant)))
        self.assertEqual((b"ghost", VECTOR), (self.vector_at(ghost), self.vector_at(occupant)))


class SmartRenameStaysPut(RenameCase):
    def test_a_photo_in_a_subfolder_stays_in_it(self):
        os.makedirs(os.path.join(self.folder, "Heats"))
        inner = self.make(os.path.join("Heats", "b.jpg"), preserved="b.jpg")
        result = photo_actions.smart_rename(self.library, [self.a, inner], "Regatta", "{grouping} - {index}", EXIFTOOL)
        self.assertTrue(result.ok, result.message())
        moved = result.details["updated_paths"]
        self.assertEqual(os.path.join(self.folder, "Heats"), os.path.dirname(moved[inner]))
        self.assertEqual(self.folder, os.path.dirname(moved[self.a]))

    def test_a_failure_part_way_puts_every_photo_back(self):
        others = [self.make(name, preserved=name) for name in ("b.jpg", "c.jpg")]
        before = self.names()
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append(src)
            # The second pass, a temporary name onto its final one: fail the second.
            if "tmp_rename_" in os.path.basename(src) and sum("tmp_rename_" in os.path.basename(s) for s in calls) == 2:
                raise PermissionError("the file is open in another program")
            return real(src, dst)

        with mock.patch("os.rename", side_effect=rename):
            result = photo_actions.smart_rename(self.library, [self.a] + others, "Regatta", "{grouping} - {index}",
                                                EXIFTOOL)
        self.assertFalse(result.ok)
        self.assertEqual(before, self.names(), "photos were left under temporary names or new ones")
        self.assertEqual(self.a, self.path_of_row())


if __name__ == "__main__":
    unittest.main()

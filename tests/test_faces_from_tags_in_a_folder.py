"""docs/findings.md, #994: `faces-from-tags --folder` and the plan behind the dialog's "Only this folder".

The folder is a filter on which photos a plan reads, never another rule: the folder's plan is the whole-library plan restricted
to the folder's photos (the property below), the write names only those faces, and a folder the library holds no photo under is
refused. Rows are made as the indexer and detection make them (tests/test_faces_from_tags.py's Case).
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_from_tags import Case, ODA, TAMSIN, WREN, at, look, write  # noqa: E402

from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import faces_from_tags, folder_scope  # noqa: E402
from tagpup.store import faces, journal, people, schema, taxonomy  # noqa: E402


class InFolders(Case):
    """Photos of one person in a run folder, its subfolder, two folders whose names only start like it, and elsewhere."""

    def setUp(self):
        super().setUp()
        self.run = os.path.join(self.folder, "Run")
        self.sibling = os.path.join(self.folder, "Run2")
        self.look_alike = os.path.join(self.folder, "Run_x")
        self.elsewhere = os.path.join(self.folder, "Elsewhere")
        # Wren's decided face, so a face is named by the tag only as far as it looks like her.
        reference = self.photo(os.path.join("Elsewhere", "ref.jpg"), ["People/" + WREN])
        self.face(reference, at(0), name=WREN, name_source="manual")
        self.named = {}
        for where in ("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run2/b1.jpg", "Run_x/b2.jpg", "Elsewhere/c1.jpg"):
            photo = self.photo(os.path.join(*where.split("/")), ["People/" + WREN])
            self.named[where] = self.face(photo, at(3))
        crowd = self.photo(os.path.join("Run", "crowd.jpg"), ["People/" + WREN])      # two faces: named by comparison
        self.named["Run/crowd.jpg"] = self.face(crowd, at(-10))
        self.face(crowd, at(180))

    def names(self, *where):
        return {name: self.name(self.named[name]) for name in where}

    def photo_of(self, face_id):
        return look(self.path, "SELECT p.path FROM faces f JOIN photos p ON p.id = f.photo_id WHERE f.id = ?", (face_id,))[0][0]


class TheFoldersPlan(InFolders):
    def test_a_dry_run_counts_only_that_folder_and_its_subfolders(self):
        everywhere = faces_from_tags.faces_from_tags(self.library)
        in_run = faces_from_tags.faces_from_tags(self.library, folder=self.run)
        self.assertEqual(6, everywhere.details["counts"]["faces"])
        self.assertEqual(3, in_run.details["counts"]["faces"], "a1, the subfolder's a2 and the crowd's one face")
        self.assertEqual(0, in_run.changed)
        self.assertEqual({None}, set(self.names(*self.named).values()), "a dry run writes nothing")

    def test_a_subfolder_alone_is_its_own_folder(self):
        sub = faces_from_tags.faces_from_tags(self.library, folder=os.path.join(self.run, "Heat1"))
        self.assertEqual(1, sub.details["counts"]["faces"])

    def test_a_folder_whose_name_another_starts_with_is_not_the_other(self):
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, folder=self.sibling).details["counts"]["faces"])
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, folder=self.look_alike).details["counts"]["faces"])

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "a case-insensitive file system")
    def test_a_folder_spelled_in_another_case_is_the_same_folder(self):
        spelled = faces_from_tags.faces_from_tags(self.library, folder=self.run.upper())
        self.assertEqual(3, spelled.details["counts"]["faces"])

    def test_the_plan_of_a_folder_is_the_library_plan_restricted_to_its_photos(self):
        """The property: the same faces, the same people, whatever the folder."""
        # A person with no decided face and two photos waiting (#839): the library's gate, not the folder's, decides.
        for where in ("Run/t1.jpg", "Elsewhere/t2.jpg"):
            photo = self.photo(os.path.join(*where.split("/")), ["People/Crew/" + TAMSIN])
            self.face(photo, at(40))
        # Someone else's face a look-alike of nobody, and a photo of two people: both left, as the library plan leaves them.
        both = self.photo(os.path.join("Run", "both.jpg"), ["People/" + WREN, "People/" + ODA])
        self.face(both, at(100))
        whole = faces_from_tags.plan(self.library)
        by_face = {choice.face_id: (choice.name, choice.how, choice.source) for choice in whole.work.choices}
        self.assertTrue(by_face, "the fixture names something")
        total = 0
        for folder in (self.run, os.path.join(self.run, "Heat1"), self.sibling, self.look_alike, self.elsewhere, self.folder):
            with self.subTest(folder=os.path.basename(folder)):
                part = faces_from_tags.plan(self.library, folder=folder)
                self.assertIsNone(part.refused)
                expected = {face_id: found for face_id, found in by_face.items()
                            if paths.is_under(self.photo_of(face_id), folder)}
                self.assertEqual(expected, {choice.face_id: (choice.name, choice.how, choice.source)
                                            for choice in part.work.choices})
                self.assertEqual(len(expected), part.counts["faces"])
                if folder != self.folder:
                    total += part.counts["faces"]
                else:
                    self.assertEqual(whole.counts, part.counts, "the whole tree is the whole library's plan")
        self.assertEqual(whole.counts["faces"], total - faces_from_tags.plan(
            self.library, folder=os.path.join(self.run, "Heat1")).counts["faces"], "every face is in one folder of the tree")
        self.assertEqual(0, sum(1 for found in by_face.values() if found[0] == TAMSIN), "the library leaves Tamsin")
        self.assertEqual(2, whole.counts["not_decidable_yet"])
        self.assertEqual(1, faces_from_tags.plan(self.library, folder=self.run).counts["not_decidable_yet"],
                         "one of her photos is in the folder; the other still makes the gate")

    def test_the_names_are_the_librarys_even_when_only_the_folders_photos_are_read(self):
        """The person's decided face is in another folder, and still the reference."""
        photo = self.photo(os.path.join("Run", "far.jpg"), ["People/" + WREN])
        unlike = self.face(photo, at(150))
        faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.run)
        self.assertIsNone(self.name(unlike), "unlike Wren's decided face elsewhere: left")
        self.assertEqual(WREN, self.name(self.named["Run/a1.jpg"]))


class TheFoldersWrite(InFolders):
    def test_apply_names_only_the_folders_faces(self):
        result = faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.run)
        self.assertEqual(3, result.changed)
        self.assertEqual({"Run/a1.jpg": WREN, "Run/Heat1/a2.jpg": WREN, "Run/crowd.jpg": WREN},
                         self.names("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run/crowd.jpg"))
        self.assertEqual({None}, set(self.names("Run2/b1.jpg", "Run_x/b2.jpg", "Elsewhere/c1.jpg").values()))
        self.assertEqual({"faces": 0}, result.details["remaining"], "what is left is counted in the folder")

    def test_what_is_left_after_the_write_is_counted_in_the_folder_only(self):
        result = faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.sibling)
        self.assertEqual(1, result.changed)
        self.assertEqual({"faces": 0}, result.details["remaining"], "the other folders' faces are not counted")
        self.assertEqual(5, faces_from_tags.faces_from_tags(self.library).details["counts"]["faces"])

    def test_undo_takes_back_the_folders_change_and_no_other(self):
        faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.sibling)
        other = faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.look_alike, again=True)
        from tagpup.services import journal as journal_service
        self.assertFalse(journal_service.undo(self.library, other.details["change"], apply=True).refused)
        self.assertEqual({"Run2/b1.jpg": WREN, "Run_x/b2.jpg": None}, self.names("Run2/b1.jpg", "Run_x/b2.jpg"))

    def test_a_second_folder_is_a_second_apply_and_needs_again(self):
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.sibling).changed)
        refused = faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.look_alike)
        self.assertEqual(faces_from_tags.AGAIN, refused.refused)
        self.assertIsNone(self.name(self.named["Run_x/b2.jpg"]))

    def test_the_plan_the_owner_said_yes_to_is_what_is_applied(self):
        planned = faces_from_tags.plan(self.library, folder=self.run)
        # Faces appear in the folder after the question was asked: a plan read again would name them.
        late = self.photo(os.path.join("Run", "late.jpg"), ["People/" + WREN])
        late_face = self.face(late, at(2))
        result = faces_from_tags.faces_from_tags(self.library, apply=True, planned=planned)
        self.assertEqual(3, result.changed)
        self.assertIsNone(self.name(late_face), "not in the plan, so not written")

    def test_a_face_named_meanwhile_refuses_the_whole_change(self):
        """Interrupted by a name given while the question was open: nothing is written, the guards as ever."""
        planned = faces_from_tags.plan(self.library, folder=self.run)
        write(self.path, lambda conn: faces.unname(conn, [self.named["Run/a1.jpg"]]))     # "nobody", by hand
        result = faces_from_tags.faces_from_tags(self.library, apply=True, planned=planned)
        self.assertTrue(result.refused, "the plan no longer holds")
        self.assertEqual(0, result.changed)
        self.assertIsNone(self.name(self.named["Run/Heat1/a2.jpg"]), "all or nothing")

    def test_a_crash_in_the_write_leaves_no_half_change(self):
        with mock.patch.object(journal, "apply", side_effect=RuntimeError("the disk went")):
            result = faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.run)
        self.assertTrue(result.errors)
        self.assertEqual({None}, set(self.names("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run/crowd.jpg").values()))


class AFolderThatHoldsNothing(InFolders):
    def test_a_folder_with_no_photo_is_refused_and_nothing_is_written(self):
        nothing = os.path.join(self.folder, "Nowhere")
        for apply in (False, True):
            result = faces_from_tags.faces_from_tags(self.library, apply=apply, folder=nothing)
            self.assertIn("holds no photo", result.refused)
            self.assertIn(nothing, result.refused)
            self.assertEqual(0, result.changed)
        self.assertEqual({None}, {self.name(face) for face in self.named.values()})

    def test_a_folder_outside_the_library_is_refused(self):
        outside = os.path.join(os.path.dirname(self.folder), "Somewhere else")
        self.assertIn("holds no photo", faces_from_tags.faces_from_tags(self.library, folder=outside).refused)
        with self.assertRaises(folder_scope.NoPhotosThere):
            folder_scope.resolve(self.library, outside)

    def test_the_refusal_is_the_first_thing_said_even_when_it_was_applied_before(self):
        faces_from_tags.faces_from_tags(self.library, apply=True, folder=self.sibling)
        refused = faces_from_tags.faces_from_tags(self.library, apply=True, folder=os.path.join(self.folder, "Nowhere"))
        self.assertIn("holds no photo", refused.refused)

    def test_a_library_that_cannot_be_read_is_an_error_not_an_empty_folder(self):
        missing = Library(os.path.join(os.path.dirname(self.path), "gone.db"))
        with self.assertRaises(sqlite3.Error):
            folder_scope.resolve(missing, self.run)


class TwoLibrariesHoldingOneFolder(Case):
    @staticmethod
    def tree(conn):
        with people.tree_edit(conn):
            taxonomy.add_path(conn, "People", root_has_face=1)
            taxonomy.add_path(conn, "People/" + WREN)

    def seed(self):
        """One decided face of Wren's and one face in Run to be named, in the library `self.path` names."""
        reference = self.photo("ref.jpg", ["People/" + WREN])
        self.face(reference, at(0), name=WREN, name_source="manual")
        return self.face(self.photo(os.path.join("Run", "a1.jpg"), ["People/" + WREN]), at(2))

    def test_a_folder_run_in_one_library_leaves_the_other_alone(self):
        first_face = self.seed()
        first = self.library
        # A second library holding the very same folder (its own rows, the same paths).
        self.path = self.home.library("parkrun.db")
        schema.ensure(self.path)
        self.library = Library(self.path)
        write(self.path, self.tree)
        second_face = self.seed()
        second = self.library
        run = os.path.join(self.folder, "Run")
        self.assertEqual(1, faces_from_tags.faces_from_tags(first, apply=True, folder=run).changed)
        self.assertEqual(WREN, look(first.path, "SELECT name FROM faces WHERE id = ?", (first_face,))[0][0])
        self.assertIsNone(look(second.path, "SELECT name FROM faces WHERE id = ?", (second_face,))[0][0],
                          "the other library holds the same folder and was not touched")
        self.assertEqual(1, faces_from_tags.faces_from_tags(second, folder=run).details["counts"]["faces"])


class TheCommand(InFolders):
    def run_cli(self, *arguments):
        return CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags", *arguments])

    def test_a_dry_run_says_the_folder_and_counts_it(self):
        said = self.run_cli("--folder", self.run)
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Only the photos under the folder given, and its subfolders.", said.output)
        self.assertIn("3 face(s) would be named.", said.output)
        self.assertEqual({None}, set(self.names(*self.named).values()))

    def test_apply_writes_only_there(self):
        said = self.run_cli("--folder", self.run, "--apply")
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Wrote 3 face(s).", said.output)
        self.assertIn("Still to be named by the rule now: 0 face(s).", said.output)
        self.assertIsNone(self.name(self.named["Run2/b1.jpg"]))

    def test_a_folder_with_no_photo_is_refused_with_a_message_and_exit_1(self):
        said = self.run_cli("--folder", os.path.join(self.folder, "Nowhere"), "--apply")
        self.assertEqual(1, said.exit_code)
        self.assertIn("holds no photo of", said.output)
        self.assertIn("Nothing was done", said.output)
        self.assertEqual({None}, set(self.names(*self.named).values()))

    def test_a_second_apply_is_refused_without_again_in_another_folder_too(self):
        self.assertEqual(0, self.run_cli("--folder", self.sibling, "--apply").exit_code)
        said = self.run_cli("--folder", self.look_alike, "--apply")
        self.assertEqual(1, said.exit_code)
        self.assertIn("--again", said.output)
        self.assertEqual(0, self.run_cli("--folder", self.look_alike, "--apply", "--again").exit_code)

    def test_the_output_never_names_a_person(self):
        self.assertNotIn(WREN, self.run_cli("--folder", self.run).output)


if __name__ == "__main__":
    unittest.main()

"""docs/findings.md, #987: `tags-from-faces --folder` writes the keywords of the photos under one folder, and no others.

The rule for each photo is the same; the folder only chooses the photos. So the folder's plan is the whole-library plan restricted
to its photos, a sibling whose name starts like the folder's is not in it, and a folder the library holds no photo under is
refused. ExifTool is a table of files (tests/fake_exiftool.py); the rows are what the indexer records.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tags_from_faces import Mending  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import face_people, folder_scope, tags_from_faces  # noqa: E402
from tagpup.store import db, people  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

from test_organize_faces import ODA, WREN  # noqa: E402


class InFolders(Mending):
    def setUp(self):
        super().setUp()
        self.run = os.path.join(self.folder, "Run")
        self.drift = {where: self.drifted(os.path.join(*where.split("/")), who=WREN if n % 2 else ODA)
                      for n, where in enumerate(("Run/a.jpg", "Run/Heat1/b.jpg", "Run2/c.jpg", "Run_x/d.jpg", "Other/e.jpg"))}

    def conn(self):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        return conn

    def written(self):
        return {where: self.files.tags_of(photo) for where, photo in self.drift.items()}


class ThePlan(InFolders):
    def test_it_counts_only_that_folder_and_its_subfolders(self):
        self.assertEqual(5, tags_from_faces.plan(self.library).details["counts"]["photos_to_write"])
        counts = tags_from_faces.plan(self.library, folder=self.run).details["counts"]
        self.assertEqual((2, 2, 2), (counts["photos_with_a_person_on_a_face_alone"], counts["photos_to_write"],
                                     counts["people_to_write"]))

    def test_a_folder_whose_name_another_starts_with_is_not_the_other(self):
        for sibling in ("Run2", "Run_x"):
            self.assertEqual(1, tags_from_faces.plan(self.library, folder=os.path.join(self.folder, sibling)
                                                     ).details["counts"]["photos_to_write"])

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "a case-insensitive file system")
    def test_a_folder_spelled_in_another_case_is_the_same_folder(self):
        self.assertEqual(2, tags_from_faces.plan(self.library, folder=self.run.upper()).details["counts"]["photos_to_write"])

    def test_the_plan_of_a_folder_is_the_library_plan_restricted_to_its_photos(self):
        # The cases the rule sorts: a guess only, a photo that already names the person under another root.
        guess = self.photo("Run/guess.jpg")
        self.files.keep_tags(guess, [])
        self.face(guess, name=ODA)
        other_root = self.photo("Run/Heat1/rooted.jpg", ["Parkrunner/" + WREN])
        self.files.keep_tags(other_root, ["Parkrunner/" + WREN])
        self.face(other_root, name=WREN, name_source="manual")
        whole = tags_from_faces.plan(self.library)
        for folder in (self.run, os.path.join(self.run, "Heat1"), os.path.join(self.folder, "Run2"), self.folder):
            with self.subTest(folder=os.path.basename(folder)):
                part = tags_from_faces.plan(self.library, folder=folder)
                self.assertEqual([(photo, name) for photo, name in whole.details["work"] if paths.is_under(photo, folder)],
                                 part.details["work"])
        part = tags_from_faces.plan(self.library, folder=self.run).details["counts"]
        self.assertEqual((1, 1), (part["people_from_a_guess_only"], part["people_the_file_names_under_another_root"]))

    def test_a_folder_with_no_photo_is_refused(self):
        nothing = tags_from_faces.plan(self.library, folder=os.path.join(self.folder, "Nowhere"))
        self.assertIn("holds no photo", nothing.refused)
        self.assertEqual([], nothing.details["work"])

    def test_the_rows_of_a_folder_are_read_by_the_path_range(self):
        conn = self.conn()
        where, params = store_roots.sql_under(conn, "p.path", self.run)
        plan = " ".join(row[-1] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT p.path FROM photo_people pp JOIN photos p ON p.id = pp.photo_id"
            " WHERE pp.source = 'face' AND " + where, params))
        self.assertIn("idx_photos_path_nocase", plan)
        self.assertEqual(len(people.on_faces_alone(conn, self.run)), 2)


class TheWrite(InFolders):
    def test_apply_writes_only_the_folders_files(self):
        said = self.run_cli("--folder", self.run, "--apply")
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Wrote 2 photo file(s)", said.output)
        written = self.written()
        self.assertTrue(written["Run/a.jpg"] and written["Run/Heat1/b.jpg"])
        for where in ("Run2/c.jpg", "Run_x/d.jpg", "Other/e.jpg"):
            self.assertEqual([], written[where], where + " is outside the folder")
        self.assertEqual(2, self.files.writes)

    def test_the_rest_is_still_to_do_afterwards(self):
        self.run_cli("--folder", self.run, "--apply")
        self.assertIn("3 photo file(s) would be written", self.run_cli().output)
        self.assertIn("0 photo file(s) would be written", self.run_cli("--folder", self.run).output)

    def test_a_run_stopped_part_way_is_finished_by_running_it_again_in_the_folder(self):
        bad = self.drift["Run/Heat1/b.jpg"]
        self.files.unreadable.add(paths.key(bad))
        said = self.run_cli("--folder", self.run, "--apply")
        self.assertEqual(1, said.exit_code)
        self.assertEqual([], self.written()["Run/Heat1/b.jpg"])
        self.assertTrue(self.written()["Run/a.jpg"], "the other photo of the folder was written")
        self.assertEqual([], self.written()["Run2/c.jpg"])
        self.files.unreadable.discard(paths.key(bad))
        self.assertEqual(0, self.run_cli("--folder", self.run, "--apply").exit_code)
        self.assertTrue(self.written()["Run/Heat1/b.jpg"])
        self.assertEqual([], self.written()["Other/e.jpg"])

    def test_chunks_of_the_folder_are_each_a_change(self):
        with mock.patch.object(face_people, "CHUNK", 1):
            said = self.run_cli("--folder", self.run, "--apply")
        self.assertIn("2 photo file(s) in 2 change(s)", said.output)

    def test_a_folder_with_no_photo_is_refused_and_nothing_is_written(self):
        said = self.run_cli("--folder", os.path.join(self.folder, "Nowhere"), "--apply")
        self.assertEqual(1, said.exit_code)
        self.assertIn("Refused:", said.output)
        self.assertIn("holds no photo of", said.output)
        self.assertEqual(0, self.files.writes)

    def test_the_output_names_no_person_and_no_photo(self):
        said = self.run_cli("--folder", self.run)
        self.assertIn("Only the photos under the folder given, and its subfolders.", said.output)
        for secret in (WREN, ODA, "a.jpg", "b.jpg"):
            self.assertNotIn(secret, said.output)

class TheScopeResolvesTheFolder(InFolders):
    def test_the_folder_is_spelled_as_given_for_a_library_without_roots(self):
        scope = folder_scope.resolve(self.library, self.run)
        self.assertEqual(self.run, scope.folder)
        self.assertEqual(2, len(scope.photo_ids))


if __name__ == "__main__":
    unittest.main()

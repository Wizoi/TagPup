"""docs/findings.md, #994: `faces-from-tags --folder` and the plan behind the dialog's "Only this folder", and the job that asks
it and applies it (tagpup.jobs.naming_faces).

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
from test_faces_from_tags import Case, ODA, TAMSIN, WREN, at, from_tags, look, write  # noqa: E402


from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Conflict, Refused  # noqa: E402
from tagpup.jobs import naming_faces  # noqa: E402
from tagpup.services import faces_from_tags, folder_scope  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import faces, journal, people, schema, taxonomy  # noqa: E402


def wait(job):
    job.thread.join(60)
    assert not job.thread.is_alive(), "the job did not end"
    return job.status()


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
        everywhere = from_tags(self.library)
        in_run = from_tags(self.library, folder=self.run)
        self.assertEqual(6, everywhere.details["counts"]["faces"])
        self.assertEqual(3, in_run.details["counts"]["faces"], "a1, the subfolder's a2 and the crowd's one face")
        self.assertEqual(0, in_run.changed)
        self.assertEqual({None}, set(self.names(*self.named).values()), "a dry run writes nothing")

    def test_a_subfolder_alone_is_its_own_folder(self):
        sub = from_tags(self.library, folder=os.path.join(self.run, "Heat1"))
        self.assertEqual(1, sub.details["counts"]["faces"])

    def test_a_folder_whose_name_another_starts_with_is_not_the_other(self):
        self.assertEqual(1, from_tags(self.library, folder=self.sibling).details["counts"]["faces"])
        self.assertEqual(1, from_tags(self.library, folder=self.look_alike).details["counts"]["faces"])

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "a case-insensitive file system")
    def test_a_folder_spelled_in_another_case_is_the_same_folder(self):
        spelled = from_tags(self.library, folder=self.run.upper())
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
        from_tags(self.library, apply=True, folder=self.run)
        self.assertIsNone(self.name(unlike), "unlike Wren's decided face elsewhere: left")
        self.assertEqual(WREN, self.name(self.named["Run/a1.jpg"]))


class TheFoldersWrite(InFolders):
    def test_apply_names_only_the_folders_faces(self):
        result = from_tags(self.library, apply=True, folder=self.run)
        self.assertEqual(3, result.changed)
        self.assertEqual({"Run/a1.jpg": WREN, "Run/Heat1/a2.jpg": WREN, "Run/crowd.jpg": WREN},
                         self.names("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run/crowd.jpg"))
        self.assertEqual({None}, set(self.names("Run2/b1.jpg", "Run_x/b2.jpg", "Elsewhere/c1.jpg").values()))

    def test_undo_takes_back_the_folders_change_and_no_other(self):
        from_tags(self.library, apply=True, folder=self.sibling)
        other = from_tags(self.library, apply=True, folder=self.look_alike, again=True)
        from tagpup.services import journal as journal_service
        self.assertFalse(journal_service.undo(self.library, other.details["change"], apply=True).refused)
        self.assertEqual({"Run2/b1.jpg": WREN, "Run_x/b2.jpg": None}, self.names("Run2/b1.jpg", "Run_x/b2.jpg"))

    def test_a_second_folder_is_a_second_apply_and_needs_again(self):
        self.assertEqual(1, from_tags(self.library, apply=True, folder=self.sibling).changed)
        refused = from_tags(self.library, apply=True, folder=self.look_alike)
        self.assertTrue(refused.refused.startswith(faces_from_tags.AGAIN))
        self.assertIn("applied to one folder (change", refused.refused)
        self.assertNotIn(self.sibling, refused.refused, "the folder is a path: behind the reveal")
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
            result = from_tags(self.library, apply=True, folder=self.run)
        self.assertTrue(result.errors)
        self.assertEqual({None}, set(self.names("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run/crowd.jpg").values()))


class TheJournalSaysWhatItWasFor(InFolders):
    def summary_of(self, change, reveal=False):
        return journal_service.history(self.library, change_id=change, reveal=reveal)["changes"][0]["summary"]

    def test_a_folders_change_records_the_scope_and_the_folder_only_behind_the_reveal(self):
        change = from_tags(self.library, apply=True, folder=self.sibling).details["change"]
        self.assertEqual("folder", self.summary_of(change)["scope"])
        self.assertNotIn("folder", self.summary_of(change), "History without reveal holds counts only")
        self.assertEqual(self.sibling, self.summary_of(change, reveal=True)["folder"])

    def test_the_whole_librarys_change_says_so(self):
        change = from_tags(self.library, apply=True).details["change"]
        self.assertEqual("whole library", self.summary_of(change)["scope"])
        self.assertNotIn("folder", self.summary_of(change, reveal=True))

    def test_the_history_dialog_never_gets_the_folder(self):
        from tagpup.web import app as web
        from_tags(self.library, apply=True, folder=self.sibling)
        app = web.create_app("tagpup", startup=self.library)
        app.testing = True
        body = app.test_client().get("/harbour/api/history").get_json()
        self.assertEqual("folder", body["changes"][0]["summary"]["scope"])
        self.assertNotIn(self.sibling, str(body))


class AFolderThatHoldsNothing(InFolders):
    def test_a_folder_with_no_photo_is_refused_and_nothing_is_written(self):
        nothing = os.path.join(self.folder, "Nowhere")
        for apply in (False, True):
            result = from_tags(self.library, apply=apply, folder=nothing)
            self.assertIn("holds no photo", result.refused)
            self.assertIn(nothing, result.refused)
            self.assertEqual(0, result.changed)
        self.assertEqual({None}, {self.name(face) for face in self.named.values()})

    def test_a_folder_outside_the_library_is_refused(self):
        outside = os.path.join(os.path.dirname(self.folder), "Somewhere else")
        self.assertIn("holds no photo", from_tags(self.library, folder=outside).refused)
        with self.assertRaises(folder_scope.NoPhotosThere):
            folder_scope.resolve(self.library, outside)

    def test_the_refusal_is_the_first_thing_said_even_when_it_was_applied_before(self):
        from_tags(self.library, apply=True, folder=self.sibling)
        refused = from_tags(self.library, apply=True, folder=os.path.join(self.folder, "Nowhere"))
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
        self.assertEqual(1, from_tags(first, apply=True, folder=run).changed)
        self.assertEqual(WREN, look(first.path, "SELECT name FROM faces WHERE id = ?", (first_face,))[0][0])
        self.assertIsNone(look(second.path, "SELECT name FROM faces WHERE id = ?", (second_face,))[0][0],
                          "the other library holds the same folder and was not touched")
        self.assertEqual(1, from_tags(second, folder=run).details["counts"]["faces"])


class JobInFolders(InFolders):
    """The question and the apply behind the button "Name faces from tags" (tagpup.jobs.naming_faces) with "Only this folder": the
    job's thread joined, no port, no sleep. Moved here from test_naming_faces_in_a_folder: the job is the plan above, asked and answered."""

    def setUp(self):
        super().setUp()
        self.addCleanup(naming_faces.forget, self.library)

    def start(self, folder=None, only_folder=True, **more):
        job = naming_faces.start(self.library, self.run if folder is None else folder, only_folder=only_folder, **more)
        wait(job)
        return job

    def runs(self):
        return runs_service.runs(self.library, naming_faces.JOB, 20)


class ThePlanAndTheApply(JobInFolders):
    def test_the_question_is_about_the_folder_and_its_subfolders_only(self):
        job = self.start()
        found = job.status()
        self.assertEqual(naming_faces.ASKING, found["state"])
        self.assertTrue(found["only_folder"])
        self.assertEqual(3, found["plan"]["faces"], "a1, the subfolder's a2 and the crowd's one face; Run2 and Run_x only start like it")
        self.assertEqual(6, faces_from_tags.plan(self.library).counts["faces"],
                         "the whole library would name every photo of the fixture")

    def test_it_is_the_clis_plan_for_the_folder(self):
        counts = faces_from_tags.plan(self.library, folder=self.run).counts
        plan = self.start().status()["plan"]
        self.assertEqual((counts["faces"], counts["named_by_the_tag_alone"]), (plan["faces"], plan["by_tag"]))

    def test_yes_writes_exactly_the_plan_and_only_in_the_folder(self):
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.DONE, found["state"], found["message"])
        self.assertEqual(3, found["applied"]["changed"])
        self.assertIn("in the open folder", found["message"])
        self.assertEqual({"Run/a1.jpg": WREN, "Run/Heat1/a2.jpg": WREN}, self.names("Run/a1.jpg", "Run/Heat1/a2.jpg"))
        self.assertEqual({None}, set(self.names("Run2/b1.jpg", "Elsewhere/c1.jpg").values()))

    def test_the_plan_is_not_read_again_for_the_write_so_a_face_that_came_later_is_not_named(self):
        job = self.start()
        late = self.face(self.photo(os.path.join("Run", "late.jpg"), ["People/" + WREN]), at(2))
        naming_faces.confirm(self.library, job.handle)
        self.assertEqual(3, wait(job)["applied"]["changed"])
        self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (late,))[0][0])

    def test_a_name_given_meanwhile_refuses_the_whole_change(self):
        job = self.start()
        write(self.path, lambda conn: faces.unname(conn, [self.named["Run/a1.jpg"]]))     # called nobody, by hand
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.FAILED, found["state"])
        self.assertEqual(naming_faces.REFUSED_SAYS, found["message"])
        self.assertEqual({None}, set(self.names("Run/Heat1/a2.jpg").values()), "all or nothing")

    def test_a_folder_job_is_recorded_as_one_and_names_no_one(self):
        job = self.start()
        self.assertTrue(self.runs()[0].changed["in_a_folder"])
        self.assertNotIn(WREN, str(self.runs()[0].changed) + str(job.status()))

    def test_it_survives_a_restart_as_a_folder_job(self):
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        wait(job)
        naming_faces.forget(self.library)
        self.assertTrue(naming_faces.status(self.library, job.handle)["only_folder"])


class WhatIsRefused(JobInFolders):
    def test_a_folder_with_no_photo_is_refused_before_anything_is_claimed(self):
        with self.assertRaises(Refused) as raised:
            naming_faces.start(self.library, os.path.join(self.folder, "Nowhere"), only_folder=True)
        self.assertIn("holds no photo", str(raised.exception))
        self.assertEqual([], self.runs(), "no claim was taken")
        self.assertIsNone(naming_faces.current(self.library))

    def test_only_this_folder_without_a_folder_is_refused(self):
        with self.assertRaises(Refused):
            naming_faces.start(self.library, None, only_folder=True)
        self.assertEqual([], self.runs())

    def test_a_folder_of_photos_with_no_faces_has_nothing_to_ask(self):
        self.photo(os.path.join("Bare", "x.jpg"), ["People/" + WREN])
        job = self.start(os.path.join(self.folder, "Bare"))
        self.assertEqual(naming_faces.DONE, job.status()["state"])
        self.assertEqual(naming_faces.NO_FACES_HERE, job.status()["message"])

    def test_another_process_holding_the_job_refuses_a_folder_job_too(self):
        self.assertTrue(runs_service.claim(self.library, naming_faces.JOB, self.library.name, 1.0))
        with mock.patch.object(naming_faces.batches, "is_this_process", return_value=False):
            with self.assertRaises(Conflict):
                naming_faces.start(self.library, self.run, only_folder=True)
        self.assertEqual({None}, set(self.names("Run/a1.jpg").values()))


class OneQuestionAtATime(JobInFolders):
    def test_a_click_again_for_the_same_folder_shows_the_question(self):
        job = self.start()
        self.assertIs(job, naming_faces.start(self.library, self.run, only_folder=True))

    def test_the_same_folder_spelled_in_another_case_is_the_same_scope(self):
        if not os.path.normcase("A") == "a":
            self.skipTest("a case-sensitive file system")
        job = self.start()
        self.assertIs(job, naming_faces.start(self.library, self.run.upper(), only_folder=True))

    def test_a_question_for_another_scope_is_not_handed_back_as_this_one(self):
        whole = naming_faces.start(self.library)
        wait(whole)
        with self.assertRaises(naming_faces.AlreadyWorking) as raised:
            naming_faces.start(self.library, self.run, only_folder=True)
        self.assertEqual(whole.handle, raised.exception.job["job"])
        self.assertFalse(raised.exception.job["only_folder"])
        self.assertIn("whole library", str(raised.exception))

    def test_nor_the_other_way_round(self):
        job = self.start()
        with self.assertRaises(naming_faces.AlreadyWorking) as raised:
            naming_faces.start(self.library)
        self.assertEqual(job.handle, raised.exception.job["job"])
        self.assertTrue(raised.exception.job["only_folder"])

    def test_another_folder_is_another_scope(self):
        self.start()
        with self.assertRaises(naming_faces.AlreadyWorking) as raised:
            naming_faces.start(self.library, self.elsewhere, only_folder=True)
        self.assertIn("ANOTHER folder", str(raised.exception))
        self.assertNotIn(self.run, str(raised.exception) + str(raised.exception.job), "the status is counts only")


class TheChoicesCounts(JobInFolders):
    def test_the_counts_of_the_folder_and_of_the_library(self):
        found = naming_faces.scope(self.library, self.run)
        self.assertEqual({"named": 0, "unnamed": 4, "photos": 3}, found["folder"])
        self.assertEqual({"named": 1, "unnamed": 7}, found["library"])
        self.assertIsNone(found["job"])

    def test_a_folder_with_no_photo_says_why_and_still_gives_the_library(self):
        found = naming_faces.scope(self.library, os.path.join(self.folder, "Nowhere"))
        self.assertIsNone(found["folder"])
        self.assertIn("holds no photo", found["why"])
        self.assertEqual(8, found["library"]["named"] + found["library"]["unnamed"])

    def test_no_folder_open_gives_the_library_alone(self):
        found = naming_faces.scope(self.library, None)
        self.assertIsNone(found["folder"])
        self.assertEqual(8, found["library"]["named"] + found["library"]["unnamed"])

    def test_the_job_to_pick_up_is_given(self):
        job = self.start()
        self.assertEqual(job.handle, naming_faces.scope(self.library, self.run)["job"]["job"])


if __name__ == "__main__":
    unittest.main()

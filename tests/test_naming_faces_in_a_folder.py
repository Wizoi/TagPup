"""docs/findings.md, #994: "Only this folder" in the job behind the button "Name faces from tags".

The plan is limited to the folder the page has open and its subfolders, and so is the apply, which writes exactly the plan the owner
said Yes to under the same guards. Grouping re-derives the whole library's automatic names, so a folder's job refuses it. Run on
small libraries made as the indexer makes them (tests/test_faces_from_tags.py's Case), the job's thread joined: no port, no sleep.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_from_tags import WREN, Case, at, look, write  # noqa: E402

from tagpup.core.result import Conflict, Refused  # noqa: E402
from tagpup.jobs import naming_faces  # noqa: E402
from tagpup.services import faces_from_tags  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.store import faces  # noqa: E402


def wait(job):
    job.thread.join(60)
    assert not job.thread.is_alive(), "the job did not end"
    return job.status()


class InFolders(Case):
    def setUp(self):
        super().setUp()
        self.addCleanup(naming_faces.forget, self.library)
        self.run = os.path.join(self.folder, "Run")
        self.other = os.path.join(self.folder, "Other")
        reference = self.photo(os.path.join("Other", "ref.jpg"), ["People/" + WREN])
        self.face(reference, at(0), name=WREN, name_source="manual")
        self.named = {}
        for where in ("Run/a1.jpg", "Run/Heat1/a2.jpg", "Run2/b1.jpg", "Other/c1.jpg"):
            photo = self.photo(os.path.join(*where.split("/")), ["People/" + WREN])
            self.named[where] = self.face(photo, at(3))

    def start(self, folder=None, only_folder=True, **more):
        job = naming_faces.start(self.library, self.run if folder is None else folder, only_folder=only_folder, **more)
        wait(job)
        return job

    def names(self, *where):
        return {name: look(self.path, "SELECT name FROM faces WHERE id = ?", (self.named[name],))[0][0] for name in where}

    def runs(self):
        return runs_service.runs(self.library, naming_faces.JOB, 20)


class ThePlanAndTheApply(InFolders):
    def test_the_question_is_about_the_folder_and_its_subfolders_only(self):
        job = self.start()
        found = job.status()
        self.assertEqual(naming_faces.ASKING, found["state"])
        self.assertTrue(found["only_folder"])
        self.assertEqual(2, found["plan"]["faces"], "Run/a1 and Run/Heat1/a2; Run2 only starts like it")
        self.assertEqual(4, faces_from_tags.faces_from_tags(self.library).details["counts"]["faces"],
                         "the whole library would name every photo of the fixture")

    def test_it_is_the_clis_plan_for_the_folder(self):
        counts = faces_from_tags.faces_from_tags(self.library, folder=self.run).details["counts"]
        plan = self.start().status()["plan"]
        self.assertEqual((counts["faces"], counts["named_by_the_tag_alone"]), (plan["faces"], plan["by_tag"]))

    def test_yes_writes_exactly_the_plan_and_only_in_the_folder(self):
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.DONE, found["state"], found["message"])
        self.assertEqual(2, found["applied"]["changed"])
        self.assertIn("in the open folder", found["message"])
        self.assertEqual({"Run/a1.jpg": WREN, "Run/Heat1/a2.jpg": WREN}, self.names("Run/a1.jpg", "Run/Heat1/a2.jpg"))
        self.assertEqual({None}, set(self.names("Run2/b1.jpg", "Other/c1.jpg").values()))

    def test_the_plan_is_not_read_again_for_the_write_so_a_face_that_came_later_is_not_named(self):
        job = self.start()
        late = self.face(self.photo(os.path.join("Run", "late.jpg"), ["People/" + WREN]), at(2))
        naming_faces.confirm(self.library, job.handle)
        self.assertEqual(2, wait(job)["applied"]["changed"])
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


class Grouping(InFolders):
    def test_a_folder_job_does_not_group_and_says_why(self):
        job = self.start()
        with mock.patch.object(naming_faces.identities, "resolve", side_effect=AssertionError("grouped")):
            with self.assertRaises(Refused) as raised:
                naming_faces.confirm(self.library, job.handle, group=True)
        self.assertEqual(naming_faces.GROUP_SAYS, str(raised.exception))
        self.assertEqual(naming_faces.ASKING, job.status()["state"], "the question is still open")
        self.assertEqual({None}, set(self.names("Run/a1.jpg").values()))

    def test_the_whole_library_job_still_groups(self):
        job = naming_faces.start(self.library)
        wait(job)
        with mock.patch.object(naming_faces.identities, "resolve", return_value={}) as resolve:
            naming_faces.confirm(self.library, job.handle, group=True)
            wait(job)
        self.assertEqual(1, resolve.call_count)


class WhatIsRefused(InFolders):
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
        with mock.patch.object(naming_faces.bulk_edit, "is_this_process", return_value=False):
            with self.assertRaises(Conflict):
                naming_faces.start(self.library, self.run, only_folder=True)
        self.assertEqual({None}, set(self.names("Run/a1.jpg").values()))


class OneQuestionAtATime(InFolders):
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
        with self.assertRaises(naming_faces.AlreadyWorking):
            naming_faces.start(self.library, self.other, only_folder=True)


class TheChoicesCounts(InFolders):
    def test_the_counts_of_the_folder_and_of_the_library(self):
        found = naming_faces.scope(self.library, self.run)
        self.assertEqual({"named": 0, "unnamed": 2, "photos": 2}, found["folder"])
        self.assertEqual({"named": 1, "unnamed": 4}, found["library"])
        self.assertIsNone(found["job"])

    def test_a_folder_with_no_photo_says_why_and_still_gives_the_library(self):
        found = naming_faces.scope(self.library, os.path.join(self.folder, "Nowhere"))
        self.assertIsNone(found["folder"])
        self.assertIn("holds no photo", found["why"])
        self.assertEqual(5, found["library"]["named"] + found["library"]["unnamed"])

    def test_no_folder_open_gives_the_library_alone(self):
        found = naming_faces.scope(self.library, None)
        self.assertIsNone(found["folder"])
        self.assertEqual(5, found["library"]["named"] + found["library"]["unnamed"])

    def test_the_job_to_pick_up_is_given(self):
        job = self.start()
        self.assertEqual(job.handle, naming_faces.scope(self.library, self.run)["job"]["job"])


if __name__ == "__main__":
    unittest.main()

"""docs/findings.md, #907 (owner, 2026-10-08): Identify Faces' Assign, Unmatch and Ignore cluster, and Re-examine this folder, keep faces
and the photos' person tags in step, as a single face does (tests/test_faces_and_tags_in_step.py), for many photos at once: one job
(tagpup.jobs.face_assignments), resumable, whose chunks are the journaled changes of photo files the single writes make.

ExifTool is a table of files (tests/fake_exiftool.py); photos are rows as the indexer records them, faces as the code that makes them.
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import PEOPLE, InStep  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.result import Conflict  # noqa: E402
from tagpup.jobs import face_assignments  # noqa: E402
from tagpup.services import face_assignment  # noqa: E402
from tagpup.store import db, people, taxonomy  # noqa: E402


class Bulk(InStep):
    def many(self, count, keywords=(), **face):
        """`count` photos holding `keywords`, each with one face (named as `face` says)."""
        made = []
        for n in range(count):
            photo = self.held("bulk_%02d.jpg" % n, keywords)
            made.append((photo, self.face(photo, **face)))
        return made

    def operations(self, name):
        return [row[0] for row in self.look("SELECT id FROM changes WHERE operation = ?", (name,))]


class AssigningMany(Bulk):
    def test_every_photo_gets_the_tag_and_then_the_face_is_named(self):
        made = self.many(30)
        order = []

        def watching(path, n):
            row = self.look("SELECT name FROM faces WHERE id = ?", (dict((photo, face) for photo, face in made)[path],))[0]
            order.append(row[0])

        self.files.on_write = watching
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        body = reply.get_json()
        self.assertEqual(30, body["matched"])
        self.assertEqual(30, body["tags_written"])
        self.assertEqual([], body["not_named"])
        self.assertEqual([None] * 30, order, "a face was named before its photo held the person")
        for photo, face in made:
            self.assertEqual([PEOPLE + WREN], self.tags(photo))
            self.assertEqual((WREN, "manual"), self.row(face)[:2])
        self.assertEqual(2, len(self.operations("person added for a named face")), "30 photos are two chunks of 25 in History")

    def test_a_photo_that_cannot_be_written_keeps_its_face_unnamed_and_the_rest_are_named(self):
        made = self.many(4)
        self.files.fails.add(paths.key(made[1][0]))
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        body = reply.get_json()
        self.assertEqual(3, body["matched"], "what was named, not what was asked")
        self.assertEqual([made[1][1]], body["not_named"])
        self.assertIn("could not be written", body["warning"])
        self.assertEqual((None, None), self.row(made[1][1])[:2])
        self.assertEqual([], self.tags(made[1][0]))
        self.assertEqual(3, sum(1 for _p, face in made if self.row(face)[0] == WREN))

    def test_a_refusal_is_made_before_any_file_is_written(self):
        photo = self.held("two.jpg")
        first, second = self.face(photo), self.face(photo, box=(70, 10, 110, 60))
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [first, second], "person_name": WREN})
        self.assertEqual(400, reply.status_code)
        self.assertEqual(0, self.files.writes)
        self.assertEqual([], self.look("SELECT 1 FROM job_runs WHERE job = 'assign faces'"), "no run for a refusal")

    def test_a_branch_of_the_tree_is_never_a_person(self):
        def branch(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People/Pairs/Cleo")
        db.write_with_connection(self.path, branch)
        made = self.many(1)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [made[0][1]], "person_name": "Pairs"})
        self.assertEqual(400, reply.status_code)
        self.assertIn("group of people, not a person", reply.get_json()["error"])
        self.assertEqual(0, self.files.writes)

    def test_a_person_the_tree_files_in_two_places_is_refused_before_a_name_is_given(self):
        def two_places(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People/Pairs/" + ODA)
                taxonomy.add_path(conn, "People/Others/" + ODA)
        db.write_with_connection(self.path, two_places)
        made = self.many(2)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": ODA})
        self.assertEqual(400, reply.status_code, reply.get_json())
        self.assertEqual([None, None], [self.row(face)[0] for _p, face in made])

    def test_faces_ruled_out_are_left_alone_and_a_face_already_under_the_name_writes_no_file(self):
        made = self.many(3)
        excluded = self.face(self.held("ruled.jpg"), excluded=1)
        done_photo = self.held("done.jpg", [PEOPLE + WREN])
        done = self.face(done_photo, name=WREN, name_source="manual")
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [f for _p, f in made] + [excluded, done], "person_name": WREN})
        body = reply.get_json()
        self.assertEqual(3, body["matched"])
        self.assertEqual([excluded], body["skipped_excluded"])
        self.assertEqual(3, self.files.writes)


class TakingNamesOffMany(Bulk):
    def test_unmatch_takes_the_tags_off_the_photos_whose_person_no_face_carries(self):
        made = self.many(3, [PEOPLE + WREN, "Regatta"], name=WREN, name_source="manual")
        shared = self.held("shared.jpg", [PEOPLE + WREN])
        gone, kept = self.face(shared, name=WREN, name_source="manual"), self.face(shared, box=(70, 10, 110, 60), name=WREN)
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": [f for _p, f in made] + [gone]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        for photo, face in made:
            self.assertEqual(["Regatta"], self.tags(photo))
            self.assertEqual((None, "manual"), self.row(face)[:2])
        self.assertEqual([PEOPLE + WREN], self.tags(shared), "another face of the photo is still the person")
        self.assertEqual(WREN, self.row(kept)[0])
        self.assertEqual(3, reply.get_json()["tags_removed"])

    def test_an_assignment_taken_back_leaves_the_faces_unreviewed_and_the_tags_off(self):
        made = self.many(2)
        ids = [face for _p, face in made]
        self.tuner_post("faces/match-bulk", {"face_ids": ids, "person_name": WREN})
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": ids, "undo": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        for photo, face in made:
            self.assertEqual([], self.tags(photo))
            self.assertEqual((None, None), self.row(face)[:2], "unreviewed, not nobody")

    def test_a_tag_that_cannot_be_taken_off_is_said_and_the_faces_are_unnamed_all_the_same(self):
        made = self.many(2, [PEOPLE + WREN, "Regatta"], name=WREN, name_source="manual")
        self.files.fails.add(paths.key(made[0][0]))
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": [f for _p, f in made]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertIn("warning", reply.get_json())
        self.assertEqual([None, None], [self.row(f)[0] for _p, f in made])

    def test_ignoring_a_cluster_takes_the_named_ones_people_off_and_reads_no_photo_for_the_rest(self):
        named = self.many(2, [PEOPLE + WREN], name=WREN, name_source="manual")
        nameless = [self.face(self.held("n_%d.jpg" % n)) for n in range(40)]
        reads = self.files.read_calls
        reply = self.tuner_post("faces/exclude", {"face_ids": [f for _p, f in named] + nameless, "reason": "ignored cluster",
                                                   "bulk": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(42, reply.get_json()["excluded"])
        self.assertEqual(2, reply.get_json()["tags_removed"])
        for photo, _f in named:
            self.assertEqual([], self.tags(photo))
        self.assertLessEqual(self.files.read_calls - reads, 4, "the photos of nameless faces were read")
        self.assertEqual([1] * 42, [self.row(f)[2] for f in [f for _p, f in named] + nameless])

    def test_a_bad_reason_is_refused_before_anything_is_changed(self):
        made = self.many(1, [PEOPLE + WREN], name=WREN, name_source="manual")
        reply = self.tuner_post("faces/exclude", {"face_ids": [made[0][1]], "reason": "because", "bulk": True})
        self.assertEqual(400, reply.status_code)
        self.assertEqual(WREN, self.row(made[0][1])[0])


class ReExamine(Bulk):
    def folder_with_a_reference(self, count):
        reference = self.held("ref.jpg", [PEOPLE + WREN])
        self.face(reference, degrees=0, name=WREN, name_source="manual")
        made = []
        for n in range(count):
            photo = self.held("re_%02d.jpg" % n)
            made.append((photo, self.face(photo, degrees=2)))
        return made

    def test_the_dry_run_writes_nothing_and_applying_names_the_faces_and_tags_the_photos(self):
        made = self.folder_with_a_reference(3)
        dry = self.tuner_post("folder/automatch", {"folder_path": self.folder, "dry_run": True})
        self.assertEqual(3, dry.get_json()["faces"])
        self.assertEqual(0, self.files.writes)
        applied = self.tuner_post("folder/automatch", {"folder_path": self.folder, "dry_run": False})
        body = applied.get_json()
        self.assertEqual(200, applied.status_code, body)
        self.assertEqual(3, body["matched_count"])
        self.assertEqual(3, body["tags_written"])
        self.assertEqual({WREN: 3}, body["people"])
        for photo, face in made:
            self.assertEqual((WREN, None), self.row(face)[:2], "a guess, as automatch always made")
            self.assertEqual([PEOPLE + WREN], self.tags(photo))

    def test_a_photo_that_cannot_be_tagged_has_its_guess_taken_back(self):
        made = self.folder_with_a_reference(2)
        self.files.fails.add(paths.key(made[0][0]))
        body = self.tuner_post("folder/automatch", {"folder_path": self.folder, "dry_run": False}).get_json()
        self.assertEqual(1, body["matched_count"], "the faces still named, not the guesses made")
        self.assertEqual((None, None), self.row(made[0][1])[:2], "no face is left named that its photo does not carry")
        self.assertEqual(WREN, self.row(made[1][1])[0])
        self.assertIn("warning", body)


class InterruptedAndAtOnce(Bulk):
    def plan(self, count):
        made = self.many(count)
        return made, face_assignment.plan_name(self.library, [face for _p, face in made], WREN)

    def test_cancelled_after_a_step_resumes_from_the_next_without_writing_a_file_twice(self):
        made, plan = self.plan(60)
        stopped = []

        def stop_after_first(done):
            if not stopped:
                stopped.append(True)
                job.cancel.set()

        job = face_assignments.start(self.library, plan, "exiftool", after_step=stop_after_first)
        job.finished.wait(30)
        self.assertEqual(face_assignments.CANCELLED, job.state)
        self.assertEqual(25, job.changed)
        self.assertEqual(25, self.files.writes)
        found = face_assignments.status(self.library, job.handle)
        self.assertTrue(found["resumable"])
        resumed = face_assignments.resume(self.library, job.handle, "exiftool")
        resumed.finished.wait(30)
        self.assertEqual((face_assignments.DONE, 60), (resumed.state, resumed.changed))
        self.assertEqual(60, self.files.writes, "no file was written twice")
        self.assertTrue(all(self.row(face)[0] == WREN for _p, face in made))

    def test_a_process_that_died_inside_a_step_leaves_a_job_a_resume_completes(self):
        made, plan = self.plan(30)

        class Died(BaseException):
            pass

        def die(path, n):
            if n == 30:                  # inside the second step, after the first photo of it was tagged
                raise Died()

        self.files.on_write = die
        job = face_assignments.start(self.library, plan, "exiftool")
        job.finished.wait(30)
        self.assertEqual(face_assignments.FAILED, job.state)
        self.files.on_write = None
        resumed = face_assignments.resume(self.library, job.handle, "exiftool")
        resumed.finished.wait(30)
        self.assertEqual(face_assignments.DONE, resumed.state)
        self.assertTrue(all(self.row(face)[0] == WREN for _p, face in made))
        self.assertTrue(all(self.tags(photo) == [PEOPLE + WREN] for photo, _f in made))
        self.assertEqual(30, self.files.writes - 1, "one file the dead step wrote is not written again")

    def test_a_stored_job_of_a_process_that_is_gone_says_abandoned(self):
        made, plan = self.plan(2)
        gate, entered = threading.Event(), threading.Event()
        self.files.on_write = lambda path, n: (entered.set(), gate.wait(10))
        job = face_assignments.start(self.library, plan, "exiftool")
        entered.wait(10)
        # The process "restarts": nothing in memory knows the job; its record and its run are what is left.
        face_assignments.forget(self.library)
        found = face_assignments.status(self.library, job.handle)
        self.assertEqual("abandoned", found["state"])
        self.assertTrue(found["resumable"])
        self.assertIn("0 of 2", found["message"])
        gate.set()
        job.finished.wait(30)
        self.assertEqual("done", face_assignment.read_state(self.library, job.handle)["state"],
                         "a finished job keeps its record, for its Undo")

    def test_a_second_assignment_while_one_runs_is_a_conflict_and_changes_nothing(self):
        made, plan = self.plan(3)
        gate, entered = threading.Event(), threading.Event()

        def hold(path, n):
            entered.set()
            gate.wait(10)

        self.files.on_write = hold
        job = face_assignments.start(self.library, plan, "exiftool")
        entered.wait(10)
        other = self.many(1)
        second = face_assignment.plan_unname(self.library, [other[0][1]])
        with self.assertRaises(Conflict):
            face_assignments.start(self.library, second, "exiftool")
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": [other[0][1]]})
        self.assertEqual(409, reply.status_code)
        gate.set()
        job.finished.wait(30)
        self.assertEqual(face_assignments.DONE, job.state)

    def test_the_status_route_and_the_current_job(self):
        made, plan = self.plan(2)
        job = face_assignments.start(self.library, plan, "exiftool")
        job.finished.wait(30)
        self.assertIsNone(self.tuner_client.get("/library/api/faces/job/current").get_json()["job"])
        self.assertEqual(200, self.tuner_client.get("/library/api/faces/job/status?job=%d" % job.handle).status_code)
        self.assertEqual(404, self.tuner_client.get("/library/api/faces/job/status?job=999").status_code)


if __name__ == "__main__":
    unittest.main()

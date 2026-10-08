"""Review of a1821cf (docs/findings.md, #922-#928): what a change of photo files really did to the people on its photos decides what
happens to their faces (one place: tagpup.services.face_people.follow_change); a tag put back names the face the removal unnamed;
the Undo of an assignment, an Ignore cluster or an Exclude selected is one undo of the job; the job is seen, held to by an update and
kept as long as it can be resumed.
"""
import os
import sys
import threading
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import PEOPLE, InStep  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import job_files  # noqa: E402
from tagpup.jobs import face_assignments  # noqa: E402
from tagpup.services import bulk_edit, face_assignment  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, faces  # noqa: E402
from tagpup.web import lifecycle  # noqa: E402

UNNAMED = "face unnamed for a person taken off the photo"
RENAMED = "face named again for a person put back on the photo"


class Case(InStep):
    def save(self, photo, tags):
        reply = self.client.post("/library/api/photo/save-metadata", json={"path": photo, "title": "", "tags": list(tags)})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def bulk_tags(self, paths_, add=(), remove=()):
        reply = self.client.post("/library/api/photos/bulk-tags",
                                 json={"paths": list(paths_), "add_tags": list(add), "remove_tags": list(remove)})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def change_ids(self, operation):
        return [row[0] for row in self.look("SELECT id FROM changes WHERE operation = ? ORDER BY id", (operation,))]


class OnlyWhatReallyChangedIsFollowed(Case):
    def photos(self):
        has = self.held("lost_has.jpg", [PEOPLE + WREN, "Regatta"])
        lacks = self.held("lost_lacks.jpg", ["Regatta"])
        return has, self.face(has, name=WREN, name_source="manual"), lacks, self.face(lacks, name=WREN, name_source="manual")

    def test_a_selection_taking_a_person_off_leaves_the_hand_named_face_of_a_photo_that_never_held_the_tag(self):
        has, on_has, lacks, on_lacks = self.photos()
        reply = self.bulk_tags([has, lacks], remove=[PEOPLE + WREN])
        self.assertEqual((None, "manual"), self.row(on_has)[:2], "the photo that held the tag lost the person")
        self.assertEqual((WREN, "manual"), self.row(on_lacks)[:2], "a hand-named face of a photo that never held the tag was unnamed")
        self.assertEqual([{"id": on_has, "name": WREN}], reply["unnamed_faces"])

    def test_the_bulk_edit_job_is_held_to_the_same(self):
        has, on_has, lacks, on_lacks = self.photos()
        ids = [row[0] for row in self.look("SELECT id FROM photos ORDER BY id")]
        edit = bulk_edit.prepare(self.library, bulk_edit.TAGS, {"remove": [PEOPLE + WREN]})
        out = bulk_edit.run_chunk(self.library, edit, ids, "exiftool", "bulk tags 1")
        self.assertEqual(1, out.changed)
        self.assertEqual((None, "manual"), self.row(on_has)[:2])
        self.assertEqual((WREN, "manual"), self.row(on_lacks)[:2])


class APersonPutBackNamesNoFaceByItself(Case):
    """Round two: a decision made since is not in the journal, so a tag put back by hand never names a face from an old record;
    History's Undo of the removal is the one exact inverse, and the rules that name faces only block on the record."""

    def two_faces(self):
        photo = self.held("back_001.jpg", [PEOPLE + WREN, "Regatta"])
        return photo, self.face(photo, name=WREN, name_source="manual"), self.face(photo, box=(70, 10, 110, 60))

    def test_the_pill_off_and_on_names_no_face_and_the_rule_does_not_name_the_other(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.assertEqual((None, "manual"), self.row(wren)[:2])
        reply = self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertEqual((None, "manual"), self.row(wren)[:2], "a face was named from an old record")
        self.assertEqual((None, None), self.row(other)[:2], "the tag alone named the face that was never anybody's")
        self.assertNotIn("renamed_faces", reply)

    def test_control_z_in_tagpup_names_no_face_either(self):
        photo, wren, other = self.two_faces()
        self.bulk_tags([photo], remove=[PEOPLE + WREN])
        self.bulk_tags([photo], add=[PEOPLE + WREN])          # web/tagpup/undo.js: the undo re-adds through bulk-tags
        self.assertEqual([(None, "manual"), (None, None)], [self.row(wren)[:2], self.row(other)[:2]])

    def test_tagpups_order_save_then_match_ends_with_the_other_face_the_person(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.save(photo, ["Regatta", PEOPLE + WREN])           # the page writes the tag first ...
        reply = self.post("face/match", {"face_id": other, "person_name": WREN, "page_writes_tags": True})   # ... then names
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([(None, "manual"), (WREN, "manual")], [self.row(wren)[:2], self.row(other)[:2]])

    def test_tagtuners_order_the_server_writes_the_tag_then_names_the_face(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        reply = self.tuner_post("face/match", {"face_id": other, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([(None, "manual"), (WREN, "manual")], [self.row(wren)[:2], self.row(other)[:2]])

    def test_naming_jobs_only_block_on_the_record(self):
        from tagpup.services import faces as faces_service
        from tagpup.services import faces_from_tags, identify
        reference = self.held("ref.jpg", [PEOPLE + WREN])
        self.face(reference, degrees=0, name=WREN, name_source="manual")
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertNotIn(other, faces_from_tags.plan(self.library).ids["faces"])
        proposed = faces_service.automatch_folder(self.library, self.folder, lambda: identify.decided_faces(self.library)[1])
        self.assertEqual(0, proposed.details["faces"], "Re-examine named a face as the person the owner took off this photo")
        self.assertEqual((None, None), self.row(other)[:2])

    def test_historys_undo_of_the_removal_is_the_exact_inverse_and_names_the_face(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        removal = self.change_ids("save photo")[-1]
        unnamed = self.change_ids(UNNAMED)[-1]
        undone = journal_service.undo(self.library, removal, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors, undone.errors)
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)))
        self.assertEqual([(WREN, "manual"), (None, None)], [self.row(wren)[:2], self.row(other)[:2]])
        self.assertEqual([{"id": wren, "name": WREN}], undone.details["renamed_faces"])
        self.assertTrue(journal_service.undo(self.library, unnamed, apply=True).refused, "undone twice")

    def test_a_decision_made_since_stops_the_inverse(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        removal = self.change_ids("save photo")[-1]
        db.write_with_connection(self.path, lambda conn: faces.name(conn, [wren], ODA))       # by hand, not journaled
        undone = journal_service.undo(self.library, removal, apply=True, exiftool_path="exiftool")
        self.assertEqual(ODA, self.row(wren)[0], "the owner's newer decision was overwritten")
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)), "the tag itself went back")
        self.assertIn("changed since", undone.details["faces_problem"])


class UndoOfTheWholeJob(Case):
    def named(self, count, keywords):
        made = []
        for n in range(count):
            photo = self.held("job_%02d.jpg" % n, keywords)
            made.append((photo, self.face(photo, name=WREN, name_source="manual" if n % 2 == 0 else None)))
        return made

    def test_undo_after_ignore_cluster_puts_back_the_names_the_deciders_and_the_tags(self):
        made = self.named(30, [PEOPLE + WREN, "Regatta"])
        ids = [face for _p, face in made]
        reply = self.tuner_post("faces/exclude", {"face_ids": ids, "reason": "ignored cluster", "bulk": True}).get_json()
        self.assertEqual(30, reply["excluded"])
        self.assertEqual(["Regatta"], self.tags(made[0][0]))
        undone = self.tuner_post("faces/job/undo", {"job": reply["job"]})
        self.assertEqual(200, undone.status_code, undone.get_json())
        self.assertEqual(30, undone.get_json()["faces"])
        for n, (photo, face) in enumerate(made):
            self.assertEqual((WREN, "manual" if n % 2 == 0 else None, 0), self.row(face)[:3])
            self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)))
        self.assertEqual(400, self.tuner_post("faces/job/undo", {"job": reply["job"]}).status_code, "undone twice")

    def test_undo_after_an_assign_takes_off_only_the_tags_that_assign_wrote(self):
        had = self.held("had.jpg", [PEOPLE + WREN])
        lacked = self.held("lacked.jpg")
        on_had, on_lacked = self.face(had), self.face(lacked)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [on_had, on_lacked], "person_name": WREN}).get_json()
        self.assertEqual(1, reply["tags_written"])
        undone = self.tuner_post("faces/job/undo", {"job": reply["job"]})
        self.assertEqual(200, undone.status_code, undone.get_json())
        self.assertEqual([PEOPLE + WREN], self.tags(had), "a tag the assign never wrote was taken off")
        self.assertEqual([], self.tags(lacked))
        self.assertEqual([(None, None), (None, None)], [self.row(on_had)[:2], self.row(on_lacked)[:2]])

    def test_unmatch_bulk_of_an_assign_with_undo_leaves_the_rule_alone_and_job_undo_is_the_way_back(self):
        made = self.named(2, [PEOPLE + WREN])
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": [f for _p, f in made]}).get_json()
        self.assertEqual([[]] * 2, [self.tags(p) for p, _f in made])
        self.assertEqual(200, self.tuner_post("faces/job/undo", {"job": reply["job"]}).status_code)
        self.assertEqual([[PEOPLE + WREN]] * 2, [self.tags(p) for p, _f in made])
        self.assertEqual([WREN, WREN], [self.row(f)[0] for _p, f in made])

    def test_a_file_changed_since_is_refused_and_said_and_the_rest_is_put_back(self):
        made = self.named(2, [PEOPLE + WREN])
        reply = self.tuner_post("faces/exclude", {"face_ids": [f for _p, f in made], "bulk": True}).get_json()
        self.files.keep_tags(made[0][0], ["Added elsewhere since"])
        undone = self.tuner_post("faces/job/undo", {"job": reply["job"]}).get_json()
        self.assertEqual([PEOPLE + WREN], self.tags(made[1][0]))
        # The journal refuses to overwrite a file changed since; the person is put back by adding, which keeps what is there.
        self.assertEqual({"Added elsewhere since", PEOPLE + WREN}, set(self.tags(made[0][0])))
        self.assertTrue(undone["undone"])


class UndoOfAJobRoundTwo(Case):
    def removal(self, count=3):
        made = []
        for n in range(count):
            photo = self.held("r2_%02d.jpg" % n, [PEOPLE + WREN, "Regatta"])
            made.append((photo, self.face(photo, name=WREN, name_source="manual")))
        reply = self.tuner_post("faces/exclude", {"face_ids": [f for _p, f in made], "bulk": True}).get_json()
        return made, reply["job"]

    def test_an_undone_job_is_not_resumable_and_is_not_carried_on(self):
        photos_ = [self.held("u_%d.jpg" % n) for n in range(30)]
        ids = [self.face(photo) for photo in photos_]
        job = face_assignments.start(self.library, face_assignment.plan_name(self.library, ids, WREN), "exiftool",
                                     after_step=lambda done: job.cancel.set())
        job.finished.wait(30)
        self.assertEqual(200, self.tuner_post("faces/job/undo", {"job": job.handle}).status_code)
        offered = self.tuner_client.get("/library/api/faces/job/current").get_json()["job"]
        self.assertEqual((True, False), (offered["undone"], offered["resumable"]))
        writes = self.files.writes
        self.assertEqual(400, self.tuner_post("faces/job/resume", {"job": job.handle}).status_code)
        self.assertEqual(writes, self.files.writes, "the second half of an undone job was written")

    def test_a_file_that_cannot_be_put_back_keeps_its_face_and_the_job_undoable_until_it_can(self):
        made, job = self.removal()
        self.files.fails.add(paths.key(made[0][0]))
        first = self.tuner_post("faces/job/undo", {"job": job}).get_json()
        self.assertEqual((False, 1), (first["undone"], first["remaining"]))
        self.assertIn("warning", first)
        self.assertEqual([], self.tags(made[0][0]) and [x for x in self.tags(made[0][0]) if x == PEOPLE + WREN])
        self.assertEqual((None, "manual", 1), self.row(made[0][1])[:3], "a face was named again with no person on its photo")
        self.assertEqual((WREN, "manual", 0), self.row(made[1][1])[:3], "the photos that went back have their faces back")
        self.files.fails.clear()                                      # the share is back
        second = self.tuner_post("faces/job/undo", {"job": job}).get_json()
        self.assertEqual((True, 0), (second["undone"], second["remaining"]))
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(made[0][0])))
        self.assertEqual((WREN, "manual", 0), self.row(made[0][1])[:3])
        self.assertEqual(400, self.tuner_post("faces/job/undo", {"job": job}).status_code, "undone twice")

    def test_the_name_direction_takes_the_same_care(self):
        made = [(self.held("n_%d.jpg" % n, ["Regatta"]), None) for n in range(3)]
        ids = [self.face(photo) for photo, _x in made]
        reply = self.tuner_post("faces/match-bulk", {"face_ids": ids, "person_name": WREN}).get_json()
        self.files.fails.add(paths.key(made[0][0]))
        first = self.tuner_post("faces/job/undo", {"job": reply["job"]}).get_json()
        self.assertEqual((False, 1), (first["undone"], first["remaining"]))
        self.files.fails.clear()
        second = self.tuner_post("faces/job/undo", {"job": reply["job"]}).get_json()
        self.assertTrue(second["undone"])
        self.assertEqual([["Regatta"]] * 3, [self.tags(photo) for photo, _x in made])

    def test_an_undo_is_held_like_a_running_job(self):
        made, job = self.removal(2)
        gate, entered = threading.Event(), threading.Event()
        self.files.on_write = lambda path, n: (entered.set(), gate.wait(10))
        worker = threading.Thread(target=lambda: face_assignments.undo(self.library, job, "exiftool"))
        worker.start()
        try:
            entered.wait(10)
            self.assertTrue(any("assignment" in each for each in lifecycle.long_work()), lifecycle.long_work())
            with self.assertRaises(face_assignments.Conflict):
                face_assignments.refuse_if_running(self.library)
            other = self.held("late.jpg")
            late = self.face(other)
            self.assertEqual(409, self.tuner_post("faces/match-bulk", {"face_ids": [late], "person_name": ODA}).status_code)
        finally:
            gate.set()
            worker.join(30)
        self.assertFalse(any("assignment" in each for each in lifecycle.long_work()))

    def test_a_job_running_in_another_process_is_not_let_go(self):
        photo = self.held("lg.jpg")
        face = self.face(photo)
        gate, entered = threading.Event(), threading.Event()
        self.files.on_write = lambda path, n: (entered.set(), gate.wait(10))
        job = face_assignments.start(self.library, face_assignment.plan_name(self.library, [face], WREN), "exiftool")
        entered.wait(10)
        try:
            face_assignments.forget(self.library)             # this process knows nothing of it ...
            with unittest.mock.patch.object(bulk_edit, "process_alive", return_value=True):      # ... its owner is alive
                with self.assertRaises(face_assignments.Conflict):
                    face_assignments.let_go(self.library, job.handle)
            self.assertIsNotNone(face_assignment.read_plan(self.library, job.handle), "a running job's record was removed")
        finally:
            gate.set()
            job.finished.wait(30)


class TheQueryIsFast(Case):
    def test_a_photos_named_faces_are_not_found_through_the_index_of_every_named_face(self):
        photo = self.held("plan.jpg")
        self.face(photo, name=WREN)
        statements = []
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        conn.set_trace_callback(statements.append)
        faces.named_in_photo(conn, photo)
        faces.nobody_in_photo(conn, photo)
        conn.set_trace_callback(None)
        self.assertTrue(statements)
        for sql in statements:
            if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                plan = " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
                self.assertNotIn("idx_faces_identify", plan, sql)


class TheJobIsSeenAndHeldToAndKept(Case):
    def test_an_update_waits_for_a_running_assignment(self):
        photo = self.held("wait.jpg")
        face = self.face(photo)
        gate, entered = threading.Event(), threading.Event()
        self.files.on_write = lambda path, n: (entered.set(), gate.wait(10))
        job = face_assignments.start(self.library, face_assignment.plan_name(self.library, [face], WREN), "exiftool")
        entered.wait(10)
        self.assertTrue(any("assignment" in each for each in lifecycle.long_work()), lifecycle.long_work())
        gate.set()
        job.finished.wait(30)
        self.assertFalse(any("assignment" in each for each in lifecycle.long_work()))

    def test_the_resume_route_forgets_the_grids_as_a_start_does(self):
        photo = self.held("resume.jpg")
        face = self.face(photo)
        job = face_assignments.start(self.library, face_assignment.plan_name(self.library, [face], WREN), "exiftool")
        job.finished.wait(30)
        with unittest.mock.patch.object(face_assignments, "resume", side_effect=face_assignments.Refused("no")) as resumed:
            self.tuner_post("faces/job/resume", {"job": job.handle})
        self.assertIsNotNone(resumed.call_args.kwargs.get("after_step"), "the resume left the cached grids as they were")

    def test_a_stopped_job_is_offered_and_can_be_let_go(self):
        photos_ = [self.held("stop_%d.jpg" % n) for n in range(30)]
        ids = [self.face(photo) for photo in photos_]
        job = face_assignments.start(self.library, face_assignment.plan_name(self.library, ids, WREN), "exiftool",
                                     after_step=lambda done: job.cancel.set())
        job.finished.wait(30)
        offered = self.tuner_client.get("/library/api/faces/job/current").get_json()["job"]
        self.assertEqual((job.handle, True), (offered["job"], offered["resumable"]))
        self.assertEqual(200, self.tuner_post("faces/job/cancel", {"job": job.handle, "let_go": True}).status_code)
        self.assertIsNone(self.tuner_client.get("/library/api/faces/job/current").get_json()["job"])


class TheRecordIsKeptAsLongAsItCanBeResumed(unittest.TestCase):
    def test_a_plan_and_state_live_thirty_days_not_seven(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            job_files.write_plan(folder, 5, {"op": "name"})
            job_files.write_state(folder, 5, {"state": "cancelled"})
            now = time.time()
            self.assertEqual([], job_files.sweep(folder, now + 20 * 86400))
            self.assertIsNotNone(job_files.read_plan(folder, 5))
            job_files.sweep(folder, now + 31 * 86400)
            self.assertIsNone(job_files.read_plan(folder, 5))
            self.assertIsNone(job_files.read_state(folder, 5))


if __name__ == "__main__":
    unittest.main()

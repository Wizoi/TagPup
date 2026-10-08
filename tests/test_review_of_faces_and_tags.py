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


class AFaceIsNamedAgainWhereItWasUnnamed(Case):
    def two_faces(self):
        photo = self.held("back_001.jpg", [PEOPLE + WREN, "Regatta"])
        return photo, self.face(photo, name=WREN, name_source="manual"), self.face(photo, box=(70, 10, 110, 60))

    def test_the_pill_off_and_on_names_the_same_face_not_the_other(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.assertEqual((None, "manual"), self.row(wren)[:2])
        reply = self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertEqual((WREN, "manual"), self.row(wren)[:2], "the face the removal unnamed")
        self.assertEqual((None, None), self.row(other)[:2], "the tag alone named the face that was never anybody's")
        self.assertEqual([{"id": wren, "name": WREN}], reply["renamed_faces"])

    def test_control_z_in_tagpup_puts_the_face_back_too(self):
        photo, wren, other = self.two_faces()
        self.bulk_tags([photo], remove=[PEOPLE + WREN])
        self.bulk_tags([photo], add=[PEOPLE + WREN])          # web/tagpup/undo.js: the undo re-adds through bulk-tags
        self.assertEqual((WREN, "manual"), self.row(wren)[:2])
        self.assertEqual((None, None), self.row(other)[:2])

    def test_historys_undo_of_the_removal_names_the_face_and_the_order_of_undos_holds(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        removal = self.change_ids("save photo")[-1]
        unnamed = self.change_ids(UNNAMED)[-1]
        undone = journal_service.undo(self.library, removal, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors, undone.errors)
        self.assertEqual([PEOPLE + WREN, "Regatta"], sorted(self.tags(photo), key=lambda t: t != PEOPLE + WREN))
        self.assertEqual((WREN, "manual"), self.row(wren)[:2])
        self.assertEqual((None, None), self.row(other)[:2])
        renamed = self.change_ids(RENAMED)[-1]
        # The unnaming cannot be undone under the naming again that came after it; the newer first, then the older.
        refused = journal_service.undo(self.library, unnamed, apply=True)
        self.assertTrue(refused.refused, "an older change was undone from under a newer one")
        self.assertEqual([], journal_service.undo(self.library, renamed, apply=True).errors)
        self.assertEqual((None, "manual"), self.row(wren)[:2])
        self.assertFalse(journal_service.undo(self.library, unnamed, apply=True).refused)
        self.assertEqual((WREN, "manual"), self.row(wren)[:2])

    def test_a_face_renamed_since_the_removal_is_not_taken_back(self):
        photo, wren, _other = self.two_faces()
        self.save(photo, ["Regatta"])
        db.write_with_connection(self.path, lambda conn: faces.name(conn, [wren], ODA))
        self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertEqual(ODA, self.row(wren)[0])


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
        self.assertIn("warning", undone)
        self.assertEqual([PEOPLE + WREN], self.tags(made[1][0]))
        self.assertEqual(["Added elsewhere since"], self.tags(made[0][0]), "never overwritten")


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

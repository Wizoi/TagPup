"""docs/findings.md, #907, #892, #893 (owner, 2026-10-08; DECISIONS 2026-10-11): Identify Faces' Assign, Unmatch and Ignore cluster, and
Re-examine this folder, keep faces and the photos' person tags in step, as a single face does (tests/test_faces_and_tags_in_step.py), for
many photos at once: ONE batch of photo files (tagpup.services.face_assignment), whose photos' faces are written in the transaction that
marks each file done (tagpup.services.face_batch, tagpup.store.face_batches).

ExifTool is a table of files (tests/fake_exiftool.py); photos are rows as the indexer records them, faces as the code that makes them.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import PEOPLE, InStep  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.result import Conflict  # noqa: E402
from tagpup.services import activity, batches, face_assignment, file_changes, identify, job_runs  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, people, taxonomy  # noqa: E402
from tagpup.web import face_routes  # noqa: E402

ASSIGN = "assign faces to a person"
IGNORE = "ignore faces"


class Bulk(InStep):
    def many(self, count, keywords=(), **face):
        """`count` photos holding `keywords`, each with one face (named as `face` says)."""
        made = []
        for n in range(count):
            photo = self.held("bulk_%02d.jpg" % n, keywords)
            made.append((photo, self.face(photo, **face)))
        return made

    def operations(self, name):
        return [row[0] for row in self.look("SELECT id FROM changes WHERE operation = ? ORDER BY id", (name,))]

    def undo(self, change):
        reply = self.client.post("/library/api/history/%d/undo" % change, json={"apply": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def faces_recorded(self, change):
        return self.look("SELECT COUNT(DISTINCT row_key) FROM change_rows WHERE change_id = ? AND table_name = 'faces'", (change,))[0][0]


class AssigningMany(Bulk):
    def test_every_photo_gets_the_tag_and_its_face_is_named_in_one_change(self):
        made = self.many(30)
        order = []

        def watching(path, n):
            row = self.look("SELECT name FROM faces WHERE id = ?", (dict(made)[path],))[0]
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
        change = self.operations(ASSIGN)
        self.assertEqual([body["change"]], change, "30 photos are ONE change in History")
        self.assertEqual(30, self.faces_recorded(change[0]), "the journal holds the faces beside the files")
        self.assertEqual([(30,)], self.look("SELECT COUNT(*) FROM change_files WHERE change_id = ? AND state = 'done'", (change[0],)))

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
        self.assertEqual(3, self.faces_recorded(body["change"]), "the journal holds what was written, not what was planned")

    def test_a_photo_that_holds_the_person_already_has_its_face_named_without_a_write(self):
        has = self.held("has.jpg", [PEOPLE + WREN, "Regatta"])
        lacks = self.held("lacks.jpg")
        on_has, on_lacks = self.face(has, box=(10, 10, 60, 60)), self.face(lacks)
        self.face(has, box=(70, 10, 110, 60), name=ODA, name_source="manual")     # the tag alone names neither
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [on_has, on_lacks], "person_name": WREN})
        body = reply.get_json()
        self.assertEqual((2, 1), (body["matched"], body["tags_written"]), body)
        self.assertEqual(1, self.files.writes, "the photo that held the person was not written")
        self.assertEqual([WREN, WREN], [self.row(on_has)[0], self.row(on_lacks)[0]])
        self.assertEqual(2, self.faces_recorded(body["change"]))

    def test_a_refusal_is_made_before_any_file_is_written(self):
        photo = self.held("two.jpg")
        first, second = self.face(photo), self.face(photo, box=(70, 10, 110, 60))
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [first, second], "person_name": WREN})
        self.assertEqual(400, reply.status_code)
        self.assertEqual(0, self.files.writes)
        self.assertEqual([], self.operations(ASSIGN), "no change for a refusal")

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

    def test_the_tag_just_written_does_not_leave_the_other_face_guessed(self):
        photo = self.held("rename.jpg", [PEOPLE + ODA])
        chosen = self.face(photo, name=ODA, name_source="manual")
        other = self.face(photo, box=(70, 10, 110, 60))
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [chosen], "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((WREN, "manual"), self.row(chosen)[:2])
        self.assertEqual((None, None), self.row(other)[:2], "the one-face rule named the face that was not chosen")

    def test_a_face_the_tag_itself_guessed_is_the_choice_made(self):
        photo = self.held("only.jpg")
        only = self.face(photo)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [only], "person_name": WREN})
        self.assertEqual(1, reply.get_json()["matched"], reply.get_json())
        self.assertEqual((WREN, "manual"), self.row(only)[:2], "the guess the rule made was left a guess")

    def test_a_person_the_tree_does_not_have_yet_is_made_and_linked(self):
        made = self.many(2)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": "Zelda Pike"})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(2, reply.get_json()["matched"])
        node = self.look("SELECT id FROM tag_taxonomy WHERE tag = ?", (PEOPLE + "Zelda Pike",))   # the tag first, where the filer files them
        self.assertEqual(1, len(node), "the person was not made")
        for photo, face in made:
            self.assertEqual([PEOPLE + "Zelda Pike"], self.tags(photo))
            self.assertEqual(("Zelda Pike", "manual", 0, node[0][0]), self.row(face), "every face is the one person, by id")
        self.undo(reply.get_json()["change"])
        self.assertEqual([(None, None, 0, None)] * 2, [self.row(face) for _p, face in made])

    def test_more_photos_than_the_cap_are_refused_whole_saying_so(self):
        made = self.many(4)
        with mock.patch.object(batches, "MAX_BATCH", 3):
            reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": WREN})
        self.assertEqual(400, reply.status_code)
        self.assertIn("3 is the most", reply.get_json()["error"])
        self.assertEqual((0, [None] * 4, []), (self.files.writes, [self.row(f)[0] for _p, f in made], self.operations(ASSIGN)))

    def test_a_queue_that_is_full_refuses_and_changes_nothing(self):
        made = self.many(30)
        with mock.patch.object(batches, "MOST_WAITING", -1):
            reply = self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": WREN})
        self.assertEqual(409, reply.status_code, reply.get_json())
        self.assertEqual((0, [None] * 30), (self.files.writes, [self.row(f)[0] for _p, f in made]))


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
        self.assertEqual((None, "manual"), self.row(gone)[:2])
        self.assertEqual(WREN, self.row(kept)[0])
        self.assertEqual(3, reply.get_json()["tags_removed"])
        self.assertEqual(4, reply.get_json()["changed"])

    def test_an_assignment_is_taken_back_by_historys_undo_of_its_change(self):
        made = self.many(2)
        ids = [face for _p, face in made]
        change = self.tuner_post("faces/match-bulk", {"face_ids": ids, "person_name": WREN}).get_json()["change"]
        self.undo(change)
        for photo, face in made:
            self.assertEqual([], self.tags(photo))
            self.assertEqual((None, None), self.row(face)[:2], "unreviewed, not nobody")

    def test_a_photo_that_cannot_be_written_keeps_its_tag_and_its_face_named(self):
        made = self.many(2, [PEOPLE + WREN, "Regatta"], name=WREN, name_source="manual")
        self.files.fails.add(paths.key(made[0][0]))
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": [f for _p, f in made]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertIn("warning", reply.get_json())
        self.assertEqual([WREN, None], [self.row(f)[0] for _p, f in made], "the face and its photo disagree")
        self.assertEqual([made[0][1]], reply.get_json()["left"], "the page would take the card of a face that stayed named")
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(made[0][0])))

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
        change = reply.get_json()["change"]
        self.assertEqual(2, self.faces_recorded(change), "the faces without a photo are not in the change")

    def test_the_undo_of_an_ignore_puts_back_the_names_the_deciders_and_the_tags(self):
        made = []
        for n in range(30):
            photo = self.held("job_%02d.jpg" % n, [PEOPLE + WREN, "Regatta"])
            made.append((photo, self.face(photo, name=WREN, name_source="manual" if n % 2 == 0 else None)))
        reply = self.tuner_post("faces/exclude", {"face_ids": [f for _p, f in made], "reason": "ignored cluster", "bulk": True})
        self.assertEqual(30, reply.get_json()["excluded"])
        self.assertEqual(["Regatta"], self.tags(made[0][0]))
        self.undo(reply.get_json()["change"])
        for n, (photo, face) in enumerate(made):
            self.assertEqual((WREN, "manual" if n % 2 == 0 else None, 0), self.row(face)[:3])
            self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)))

    def test_a_face_named_since_is_left_by_the_undo_and_the_rest_is_put_back(self):
        made = self.many(3, [PEOPLE + WREN], name=WREN, name_source="manual")
        change = self.tuner_post("faces/exclude", {"face_ids": [f for _p, f in made], "bulk": True}).get_json()["change"]
        db.write_with_connection(self.path, lambda conn: conn.execute(
            "UPDATE faces SET excluded = 0, excluded_reason = NULL, name = ?, tag_id = NULL, name_source = 'manual' WHERE id = ?",
            (ODA, made[1][1])))
        self.assertEqual(1, self.undo(change)["faces_left"], "the undo did not count the face it left")
        self.assertEqual([WREN, ODA, WREN], [self.row(f)[0] for _p, f in made], "somebody's newer decision was overwritten")
        self.assertEqual([[PEOPLE + WREN]] * 3, [self.tags(p) for p, _f in made], "the tags go back with the files")

    def test_a_bad_reason_is_refused_before_anything_is_changed(self):
        made = self.many(1, [PEOPLE + WREN], name=WREN, name_source="manual")
        reply = self.tuner_post("faces/exclude", {"face_ids": [made[0][1]], "reason": "because", "bulk": True})
        self.assertEqual(400, reply.status_code)
        self.assertEqual(WREN, self.row(made[0][1])[0])

    def test_faces_that_carry_no_name_are_marked_nobody_without_reading_a_photo(self):
        nameless = [self.face(self.held("p_%d.jpg" % n)) for n in range(5)]
        reads = self.files.read_calls
        reply = self.tuner_post("faces/unmatch-bulk", {"face_ids": nameless})
        self.assertEqual((200, 5, None), (reply.status_code, reply.get_json()["changed"], reply.get_json()["change"]))
        self.assertEqual(reads, self.files.read_calls)
        self.assertEqual([(None, "manual")] * 5, [self.row(f)[:2] for f in nameless])


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
        self.undo(body["change"])
        for photo, face in made:
            self.assertEqual(((None, None), []), (self.row(face)[:2], self.tags(photo)))

    def test_a_photo_that_cannot_be_tagged_has_no_guess_made(self):
        made = self.folder_with_a_reference(2)
        self.files.fails.add(paths.key(made[0][0]))
        body = self.tuner_post("folder/automatch", {"folder_path": self.folder, "dry_run": False}).get_json()
        self.assertEqual(1, body["matched_count"], "the faces still named, not the guesses made")
        self.assertEqual((None, None), self.row(made[0][1])[:2], "no face is left named that its photo does not carry")
        self.assertEqual(WREN, self.row(made[1][1])[0])
        self.assertIn("warning", body)


class InterruptedAndAtOnce(Bulk):
    def assign(self, made):
        return self.tuner_post("faces/match-bulk", {"face_ids": [face for _p, face in made], "person_name": WREN})

    def settle(self):
        return file_changes.settle(self.library, "exiftool")

    def test_a_crash_between_the_file_and_its_faces_is_finished_by_the_settle_with_both(self):
        made = self.many(3)
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", nth=2)):
            with self.assertRaises(Crash):
                face_assignment.run(self.library, face_assignment.plan_name(self.library, [f for _p, f in made], WREN), "exiftool")
        self.assertEqual([[PEOPLE + WREN], [PEOPLE + WREN], []], [self.tags(p) for p, _f in made][:2] + [self.tags(made[2][0])])
        self.assertEqual([WREN, None, None], [self.row(f)[0] for _p, f in made], "the second file was written, not recorded")
        self.assertEqual(1, self.settle())
        self.assertEqual([WREN, WREN, WREN], [self.row(f)[0] for _p, f in made], "the settle finished the files AND the faces")
        self.assertEqual([[PEOPLE + WREN]] * 3, [self.tags(p) for p, _f in made])
        change = self.operations(ASSIGN)[0]
        self.assertEqual(3, self.faces_recorded(change))
        self.assertEqual(0, self.settle(), "nothing is left to settle")

    def test_a_crash_before_a_photo_is_touched_leaves_it_and_its_face_as_they_were_until_the_settle(self):
        made = self.many(3)
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", nth=1)):
            with self.assertRaises(Crash):
                face_assignment.run(self.library, face_assignment.plan_name(self.library, [f for _p, f in made], WREN), "exiftool")
        self.assertEqual([WREN, None, None], [self.row(f)[0] for _p, f in made])
        self.assertEqual([[PEOPLE + WREN], [], []], [self.tags(p) for p, _f in made])
        self.settle()
        self.assertEqual([WREN] * 3, [self.row(f)[0] for _p, f in made])

    def test_cancelled_after_a_chunk_the_first_photos_are_whole_and_the_rest_untouched_and_it_is_undoable(self):
        made = self.many(60)

        def cancel_after_the_first_chunk(path, n):
            if n == 25:
                batches.current().stop.set()

        self.files.on_write = cancel_after_the_first_chunk
        reply = self.assign(made)
        body = reply.get_json()
        self.assertEqual(200, reply.status_code, body)
        self.assertEqual(25, body["matched"])
        self.assertIn("Stopped as asked after 25 of 60", body["warning"])
        self.assertEqual(25, sum(1 for _p, f in made if self.row(f)[0] == WREN))
        self.assertEqual(25, sum(1 for p, _f in made if self.tags(p) == [PEOPLE + WREN]))
        self.assertEqual(25, self.faces_recorded(body["change"]), "the journal holds the faces it wrote")
        self.undo(body["change"])
        self.assertEqual([None] * 60, [self.row(f)[0] for _p, f in made])
        self.assertEqual([[]] * 60, [self.tags(p) for p, _f in made])

    def test_a_face_named_by_hand_since_the_click_is_left_and_said(self):
        made = self.many(3)

        def somebody_names_the_third_face(path, n):
            if n == 1:
                db.write_with_connection(self.path, lambda conn: conn.execute(
                    "UPDATE faces SET name = ?, name_source = 'manual' WHERE id = ?", (ODA, made[2][1])))

        self.files.on_write = somebody_names_the_third_face
        body = self.assign(made).get_json()
        self.assertEqual(2, body["matched"])
        self.assertEqual([made[2][1]], body["not_named"])
        self.assertIn("changed since", body["warning"])
        self.assertEqual((ODA, "manual"), self.row(made[2][1])[:2], "a decision made since was overwritten")
        self.assertEqual(2, self.faces_recorded(body["change"]))

    def merge_away(self):
        def merged_away(conn):
            with people.tree_edit(conn):
                taxonomy.remove_node(conn, PEOPLE + WREN, force=True)

        db.write_with_connection(self.path, merged_away)

    def test_a_person_merged_away_before_the_write_refuses_the_whole_and_writes_nothing(self):
        made = self.many(2)
        plan = face_assignment.plan_name(self.library, [f for _p, f in made], WREN)
        self.merge_away()
        with self.assertRaises(Conflict) as why:
            face_assignment.run(self.library, plan, "exiftool")
        self.assertIn("Nothing was changed", str(why.exception))
        self.assertEqual((0, [None, None], []), (self.files.writes, [self.row(f)[0] for _p, f in made], self.operations(ASSIGN)))

    def test_a_person_merged_away_during_the_write_names_no_later_face_and_the_batch_goes_on(self):
        made = self.many(2)
        plan = face_assignment.plan_name(self.library, [f for _p, f in made], WREN)
        self.files.on_write = lambda path, n: self.merge_away() if n == 1 else None
        outcome = face_assignment.run(self.library, plan, "exiftool")
        self.assertEqual(([], 2, 2), (outcome.applied, len(outcome.left), outcome.files), "the tags are written all the same")
        self.assertEqual([None, None], [self.row(f)[0] for _p, f in made])

    def test_a_file_that_holds_what_it_held_after_the_write_takes_its_faces_back(self):
        made = self.many(1)
        photo, face = made[0]

        def the_write_did_not_take(step):
            if step == "file written":
                self.files.keep_tags(photo, [])

        with mock.patch.object(file_changes, "_reached", side_effect=the_write_did_not_take):
            body = self.assign(made).get_json()
        self.assertEqual(([], (None, None)), (self.tags(photo), self.row(face)[:2]), "a face is named for a tag the file lacks")
        self.assertEqual(0, self.faces_recorded(body["change"]), "History holds a face change for a file that is not in it")
        self.assertEqual([face], body["not_named"])

    def test_the_cached_grid_keeps_its_cards_when_the_tag_itself_guesses_the_face(self):
        made = self.many(3)                               # each photo's only face is the one the rule would guess
        cache = face_routes.identify_cache.of(self.library)
        cache.put("matches:Unknown Faces", identify.fingerprint(self.library),
                  {"faces": [{"id": face, "cluster_id": -1} for _p, face in made], "unclustered_total": 3})
        body = self.tuner_post("faces/match-bulk", {"face_ids": [made[0][1]], "person_name": WREN}).get_json()
        self.assertEqual(1, body["matched"])
        entry = cache.entry("matches:Unknown Faces")
        self.assertEqual([made[1][1], made[2][1]], [each["id"] for each in entry["value"]["faces"]],
                         "the grid was thrown away: the next click builds it again")
        self.assertEqual(identify.fingerprint(self.library), entry["fingerprint"])

    def test_a_batch_cancelled_before_its_turn_is_answered_as_stopped_and_changes_nothing(self):
        made = self.many(30)
        with mock.patch.object(batches, "_closing", True):
            reply = self.assign(made)
        self.assertEqual(200, reply.status_code, reply.get_json())
        body = reply.get_json()
        self.assertEqual((0, 30), (body["matched"], len(body["not_named"])))
        self.assertIn("cancelled before it began", body["warning"])
        self.assertEqual((0, [None] * 30), (self.files.writes, [self.row(f)[0] for _p, f in made]))

    def test_a_crash_while_undoing_keeps_the_faces_the_change_recorded(self):
        made = self.many(3)
        change = self.assign(made).get_json()["change"]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", nth=1)):
            with self.assertRaises(Crash):
                journal_service.undo(self.library, change, apply=True, exiftool_path="exiftool")
        self.settle()
        self.assertEqual(([None] * 3, 3), ([self.row(f)[0] for _p, f in made], self.faces_recorded(change)),
                         "settling the undo took away what History shows of the change")


class TheOldAssignmentRecordsAreLetGo(Bulk):
    def test_a_run_an_older_version_recorded_is_still_listed_in_activity(self):
        claim = job_runs.claim(self.library, "assign faces", self.library.name, time.time())
        job_runs.end(self.library, claim.run_id, time.time(), {"what": "assign faces to a person: 60 of 60 faces", "state": "done"})
        listed = [entry["what"] for entry in activity.timeline(self.library) if entry["kind"] == "job"]
        self.assertEqual(["assign faces to a person: 60 of 60 faces"], listed)


if __name__ == "__main__":
    unittest.main()

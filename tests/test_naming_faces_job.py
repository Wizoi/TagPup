"""docs/findings.md, #789: the button "Name faces from tags" is one job: a plan, a question, the names, and (if asked) grouping.

tagpup.jobs.naming_faces runs faces-from-tags (its plan, then the journaled change the CLI's --apply makes) and, when ticked,
identities.resolve, on a thread of its own. Nothing is written before the answer Yes. These tests run it on small libraries made
as the indexer makes them (tests/test_faces_from_tags.py's Case), in a home of their own, and join the job's thread: no port, no
sleep. They cover how it fails: two clicks, Cancel at each step, a restart, a plan gone stale, a library with no faces, two
libraries, and the other work that must finish first.
"""
import contextlib
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from test_faces_from_tags import ODA, WREN, Case, at, look, write  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Conflict, NotFound, Refused  # noqa: E402
from tagpup.jobs import naming_faces  # noqa: E402
from tagpup.services import faces_from_tags, identities  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.store import faces, people, schema, taxonomy  # noqa: E402


def wait(job):
    job.thread.join(60)
    assert not job.thread.is_alive(), "the job did not end"
    return job.status()


class Case789(Case):
    def setUp(self):
        super().setUp()
        self.addCleanup(naming_faces.forget, self.library)

    def seed(self):
        """Faces there before the rule: Wren's alone (one face, one person), a crowd one of whose faces is like Wren's decided
        face, and a photo two people are alike on."""
        self.single = self.photo("old_001.jpg", ["People/" + WREN])
        self.single_face = self.face(self.single)
        self.crowd = self.photo("old_002.jpg", ["People/" + WREN])
        self.crowd_faces = [self.face(self.crowd, at(-10)), self.face(self.crowd, at(180))]
        self.left = self.photo("old_003.jpg", ["People/" + WREN, "People/" + ODA])
        self.left_face = self.face(self.left, at(30))
        elsewhere = self.photo("old_004.jpg", ["People/" + WREN])
        self.known_face = self.face(elsewhere, at(0), name=WREN, name_source="manual")
        also = self.photo("old_005.jpg", ["People/" + ODA])
        self.face(also, at(60), name=ODA, name_source="manual")

    def start(self, **more):
        job = naming_faces.start(self.library, **more)
        wait(job)
        return job

    def names(self):
        return look(self.path, "SELECT id, name, name_source FROM faces ORDER BY id")

    def runs(self):
        return runs_service.runs(self.library, naming_faces.JOB, 20)


class ThePlanIsAJobAndWritesNothing(Case789):
    def test_the_plan_is_read_and_the_question_waits_with_nothing_written(self):
        self.seed()
        before = self.names()
        job = self.start()
        found = job.status()
        self.assertEqual(naming_faces.ASKING, found["state"])
        self.assertEqual({"faces": 2, "by_tag": 1, "by_comparison": 1, "photos": 2, "left": 1, "earlier_apply": False,
                          "again": None}, found["plan"])
        self.assertEqual(before, self.names(), "a plan writes nothing")
        self.assertEqual(0, naming_faces.running(self.library), "a question waiting is no work an update must wait for")

    def test_the_plan_is_the_clis_plan(self):
        self.seed()
        counts = faces_from_tags.faces_from_tags(self.library).details["counts"]
        plan = self.start().status()["plan"]
        self.assertEqual(counts["faces"], plan["faces"])
        self.assertEqual(counts["named_by_the_tag_alone"], plan["by_tag"])
        self.assertEqual(counts["photos_left_for_identify_faces"], plan["left"])

    def test_the_run_is_recorded_for_the_activity_page_with_counts_and_no_name(self):
        self.seed()
        job = self.start()
        recorded = self.runs()
        self.assertEqual(1, len(recorded))
        self.assertEqual("done", recorded[0].outcome, "the plan's run is over: no claim is held while the question waits")
        self.assertEqual(2, recorded[0].changed["faces"])
        self.assertNotIn(WREN, json.dumps(recorded[0].changed) + json.dumps(job.status()))

    def test_the_progress_is_reported_in_steps(self):
        self.seed()
        heard = []
        real = faces_from_tags.plan

        def watching(library, on_step=None):
            return real(library, on_step=lambda *step: (heard.append(step[0]), on_step(*step)))
        with mock.patch.object(faces_from_tags, "plan", watching):
            self.start()
        self.assertEqual("reading", heard[0])
        self.assertIn("checking", heard)
        self.assertIn("comparing", heard)

    def test_a_library_with_no_faces_has_nothing_to_ask(self):
        job = self.start()
        found = job.status()
        self.assertEqual(naming_faces.DONE, found["state"])
        self.assertEqual(naming_faces.NO_FACES, found["message"])
        with self.assertRaises(Conflict):
            naming_faces.confirm(self.library, job.handle)

    def test_a_plan_with_nothing_to_name_still_offers_the_grouping(self):
        # Faces, and no keyword names anyone: the question is only about grouping.
        self.face(self.photo("plain_001.jpg"), at(0))
        job = self.start()
        self.assertEqual(naming_faces.ASKING, job.status()["state"])
        self.assertEqual(0, job.status()["plan"]["faces"])
        with self.assertRaises(Refused):
            naming_faces.confirm(self.library, job.handle)
        again = naming_faces.confirm(self.library, job.handle, group=True)
        self.assertEqual(naming_faces.DONE, wait(again)["state"])


class YesWritesTheChange(Case789):
    def test_yes_applies_the_plan_the_question_showed_as_one_journaled_change(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.DONE, found["state"], found["message"])
        self.assertEqual(2, found["applied"]["changed"])
        self.assertIsNotNone(found["applied"]["change"])
        self.assertEqual(WREN, look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0])
        self.assertTrue(faces_from_tags.earlier_apply(self.library), "History has it, with Undo")
        self.assertEqual(2, len(self.runs()), "a run for the plan and one for the write")
        self.assertIsNone(job.planned, "the plan is let go")

    def test_the_plan_is_not_read_again_for_the_write(self):
        self.seed()
        job = self.start()
        with mock.patch.object(faces_from_tags, "_plan", side_effect=AssertionError("planned again")):
            naming_faces.confirm(self.library, job.handle)
            self.assertEqual(naming_faces.DONE, wait(job)["state"], job.message)

    def test_a_second_apply_is_the_clis_again_and_the_question_says_so(self):
        self.seed()
        first = self.start()
        naming_faces.confirm(self.library, first.handle)
        wait(first)
        # Something more to name: the faces just named are references now.
        extra = self.photo("old_006.jpg", ["People/" + WREN])
        self.face(extra, at(35))
        second = self.start()
        plan = second.status()["plan"]
        self.assertTrue(plan["earlier_apply"])
        self.assertEqual(faces_from_tags.AGAIN, plan["again"])
        naming_faces.confirm(self.library, second.handle)
        found = wait(second)
        self.assertEqual(naming_faces.DONE, found["state"], found["message"])
        self.assertEqual(1, found["applied"]["changed"])

    def test_two_yes_clicks_write_once(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        with self.assertRaises(Conflict):
            naming_faces.confirm(self.library, job.handle)
        self.assertEqual(2, wait(job)["applied"]["changed"])

    def test_faces_are_held_against_other_writes_from_the_yes_to_the_end(self):
        self.seed()
        held = []

        @contextlib.contextmanager
        def hold():
            held.append("in")
            try:
                yield
            finally:
                held.append("out")
        job = self.start(hold=hold)
        self.assertEqual([], held, "a plan holds nothing: the owner keeps working while the question waits")
        naming_faces.confirm(self.library, job.handle)
        wait(job)
        self.assertEqual(["in", "out"], held)

    def test_the_open_folders_faces_are_counted_before_and_after(self):
        self.seed()
        job = self.start(folder=self.folder)
        self.assertEqual({"named": 2, "unnamed": 4}, job.status()["in_folder"]["before"])
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual({"named": 4, "unnamed": 2}, found["in_folder"]["after"])


class WhenTheFacesChangeBeforeTheAnswer(Case789):
    def test_a_face_named_by_hand_meanwhile_refuses_the_whole_change(self):
        self.seed()
        job = self.start()
        write(self.path, lambda conn: faces.unname(conn, [self.single_face]))   # called nobody, by hand
        before = self.names()
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.FAILED, found["state"])
        self.assertEqual(naming_faces.REFUSED_SAYS, found["message"])
        self.assertEqual(before, self.names(), "all or nothing: the other face was not named either")

    def test_a_plan_not_answered_in_time_is_let_go(self):
        self.seed()
        job = self.start()
        job.asked -= naming_faces.ASK_SECONDS + 1
        self.assertEqual(naming_faces.EXPIRED, job.status()["state"])
        with self.assertRaises(Refused):
            naming_faces.confirm(self.library, job.handle)
        self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0])


class TwoClicks(Case789):
    def blocked_plan(self):
        """A plan that waits to be let go, as a slow one on a big library does."""
        gate, reached = threading.Event(), threading.Event()
        real = faces_from_tags.plan

        def slow(library, on_step=None):
            on_step("reading", 0, 1)
            reached.set()
            gate.wait(30)
            return real(library, on_step=on_step)
        return gate, reached, mock.patch.object(faces_from_tags, "plan", slow)

    def test_the_second_click_while_it_plans_is_told_one_runs(self):
        self.seed()
        gate, reached, patched = self.blocked_plan()
        with patched:
            job = naming_faces.start(self.library)
            self.assertTrue(reached.wait(30))
            with self.assertRaises(naming_faces.AlreadyWorking) as raised:
                naming_faces.start(self.library)
            self.assertEqual(job.handle, raised.exception.job["job"], "the page that asked again can show the one that runs")
            self.assertEqual(1, naming_faces.running(self.library))
            gate.set()
            wait(job)
        self.assertEqual(1, len(self.runs()), "one job, one run: the refused click claimed nothing")

    def test_a_click_while_the_question_waits_shows_the_question(self):
        self.seed()
        job = self.start()
        self.assertIs(job, naming_faces.start(self.library))

    def test_the_second_click_while_it_writes_is_told_one_runs(self):
        self.seed()
        job = self.start()
        gate, reached = threading.Event(), threading.Event()
        real = faces_from_tags.faces_from_tags

        def slow(*args, **more):
            reached.set()
            gate.wait(30)
            return real(*args, **more)
        with mock.patch.object(faces_from_tags, "faces_from_tags", slow):
            naming_faces.confirm(self.library, job.handle)
            self.assertTrue(reached.wait(30))
            with self.assertRaises(naming_faces.AlreadyWorking):
                naming_faces.start(self.library)
            gate.set()
            wait(job)


class Cancel(Case789):
    def test_no_to_the_question_changes_nothing_and_holds_nothing(self):
        self.seed()
        before = self.names()
        job = self.start()
        found = naming_faces.cancel(self.library, job.handle)
        self.assertEqual(naming_faces.CANCELLED, found["state"])
        self.assertEqual(before, self.names())
        self.assertIsNone(job.planned)
        again = naming_faces.start(self.library)      # not refused by the one that was answered No
        self.assertEqual(naming_faces.ASKING, wait(again)["state"])

    def test_cancel_in_the_plan_stops_it_with_nothing_changed(self):
        self.seed()
        before = self.names()
        real = faces_from_tags.plan

        def cancelled_midway(library, on_step=None):
            naming_faces.cancel(library, next(iter(naming_faces._held(library))))
            return real(library, on_step=on_step)
        with mock.patch.object(faces_from_tags, "plan", cancelled_midway):
            job = naming_faces.start(self.library)
            found = wait(job)
        self.assertEqual(naming_faces.CANCELLED, found["state"])
        self.assertEqual(before, self.names())
        self.assertIsNone(job.planned)

    def test_cancel_before_the_write_writes_nothing(self):
        self.seed()
        before = self.names()
        job = self.start()

        @contextlib.contextmanager
        def hold_then_cancel():
            naming_faces.cancel(self.library, job.handle)
            yield
        job.hold = hold_then_cancel
        naming_faces.confirm(self.library, job.handle)
        found = wait(job)
        self.assertEqual(naming_faces.CANCELLED, found["state"])
        self.assertIn("nothing was changed", found["message"])
        self.assertEqual(before, self.names())

    def test_cancel_during_the_write_lets_the_write_finish_whole_and_skips_the_grouping(self):
        self.seed()
        job = self.start()
        real = faces_from_tags.faces_from_tags

        def cancelled_while_writing(*args, **more):
            naming_faces.cancel(self.library, job.handle)
            return real(*args, **more)
        with mock.patch.object(faces_from_tags, "faces_from_tags", cancelled_while_writing), \
                mock.patch.object(identities, "resolve", side_effect=AssertionError("grouped")):
            naming_faces.confirm(self.library, job.handle, group=True)
            found = wait(job)
        self.assertEqual(naming_faces.CANCELLED, found["state"])
        self.assertEqual(2, found["applied"]["changed"], "a write begun is finished, never half")
        self.assertIn("written as one change", found["message"])
        self.assertIn("Grouping was not run", found["message"])

    def test_cancel_in_the_grouping_writes_no_name(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        wait(job)
        self.assertEqual(naming_faces.DONE, job.state)
        before = self.names()
        second = self.start()
        real = identities.resolve

        def cancelled_in_the_middle(index, max_iterations=5, on_step=None):
            naming_faces.cancel(self.library, second.handle)
            return real(index, max_iterations, on_step)
        with mock.patch.object(identities, "resolve", cancelled_in_the_middle):
            naming_faces.confirm(self.library, second.handle, group=True)
            found = wait(second)
        self.assertEqual(naming_faces.CANCELLED, found["state"])
        self.assertEqual(before, self.names(), "grouping stopped before its commit: no name changed")

    def test_cancel_of_a_job_that_ended_is_no_error(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        wait(job)
        self.assertEqual(naming_faces.DONE, naming_faces.cancel(self.library, job.handle)["state"])
        with self.assertRaises(NotFound):
            naming_faces.cancel(self.library, 999999)


class Grouping(Case789):
    def test_grouping_runs_identities_resolve_and_keeps_names_given_by_hand(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle, group=True)
        found = wait(job)
        self.assertEqual(naming_faces.DONE, found["state"], found["message"])
        self.assertIsNotNone(found["grouped"])
        self.assertEqual((WREN, "manual"), look(self.path, "SELECT name, name_source FROM faces WHERE id = ?",
                                                (self.known_face,))[0], "a name given by hand is kept")
        self.assertIn("Grouping named", found["message"])

    def test_grouping_is_not_run_unless_ticked(self):
        self.seed()
        job = self.start()
        with mock.patch.object(identities, "resolve", side_effect=AssertionError("grouped")):
            naming_faces.confirm(self.library, job.handle)
            self.assertEqual(naming_faces.DONE, wait(job)["state"])

    def test_grouping_uses_no_model(self):
        # It loads the library's photos without their vectors: no model, no graphics card.
        self.seed()
        job = self.start()
        from tagpup.services import search
        made = []
        real = search.PhotoIndex

        def watching(db_path, model=None, read_only=False):
            made.append(model)
            return real(db_path, model=model, read_only=read_only)
        with mock.patch.object(search, "PhotoIndex", watching):
            naming_faces.confirm(self.library, job.handle, group=True)
            wait(job)
        self.assertEqual([None], made)

    def test_grouping_that_fails_says_so_and_what_was_written_before(self):
        self.seed()
        job = self.start()
        with mock.patch.object(identities, "resolve", side_effect=RuntimeError("secret " + WREN)):
            naming_faces.confirm(self.library, job.handle, group=True)
            found = wait(job)
        self.assertEqual(naming_faces.FAILED, found["state"])
        self.assertIn("2 face names were written as one change", found["message"])
        self.assertNotIn(WREN, found["message"], "an exception's text can carry a name or a path")


class WhatMustFinishFirst(Case789):
    def test_indexing_or_suggest_in_the_library_refuses_it_and_claims_nothing(self):
        self.seed()
        with self.assertRaises(Conflict) as raised:
            naming_faces.start(self.library, busy=lambda: ["an index run is running or queued"])
        self.assertIn("index run", str(raised.exception))
        self.assertIn("no graphics card", str(raised.exception))
        self.assertEqual([], self.runs())

    def test_a_bulk_edit_in_this_process_refuses_it(self):
        self.seed()
        with mock.patch.object(naming_faces.bulk_edits, "running", return_value=1):
            with self.assertRaises(Conflict) as raised:
                naming_faces.start(self.library)
        self.assertIn("bulk edit", str(raised.exception))

    def test_a_bulk_edit_in_another_process_refuses_it(self):
        self.seed()
        claim = runs_service.claim(self.library, naming_faces.bulk_edits.JOB, self.library.name, 1.0)
        self.assertTrue(claim)
        with mock.patch.object(naming_faces.bulk_edit, "process_alive", return_value=True):
            with self.assertRaises(Conflict) as raised:
                naming_faces.start(self.library)
        self.assertIn("another TagPup process", str(raised.exception))

    def test_another_process_holding_the_job_refuses_it(self):
        self.seed()
        self.assertTrue(runs_service.claim(self.library, naming_faces.JOB, self.library.name, 1.0))
        with mock.patch.object(naming_faces.bulk_edit, "is_this_process", return_value=False):
            with self.assertRaises(Conflict) as raised:
                naming_faces.start(self.library)
        self.assertIn("another TagPup process", str(raised.exception))

    def test_a_library_behind_this_version_is_not_migrated(self):
        self.seed()
        with mock.patch.object(schema, "pending", return_value=["a migration"]):
            with self.assertRaises(Conflict) as raised:
                naming_faces.start(self.library)
        self.assertIn("brought up to date", str(raised.exception))


class AfterARestart(Case789):
    def test_a_job_the_process_no_longer_holds_is_abandoned_and_says_what_that_means(self):
        self.seed()
        gate, reached = threading.Event(), threading.Event()
        real = faces_from_tags.plan

        def slow(library, on_step=None):
            reached.set()
            gate.wait(30)
            return real(library, on_step=on_step)
        with mock.patch.object(faces_from_tags, "plan", slow):
            job = naming_faces.start(self.library)
            self.assertTrue(reached.wait(30))
            naming_faces.forget(self.library)          # the process that held it is gone
            with mock.patch.object(naming_faces.bulk_edit, "process_alive", return_value=False):
                found = naming_faces.status(self.library, job.handle)
            gate.set()
            job.thread.join(60)
        self.assertEqual(naming_faces.ABANDONED, found["state"])
        self.assertEqual(naming_faces.RESTARTED, found["message"])

    def test_confirming_a_plan_a_restart_took_changes_nothing(self):
        self.seed()
        job = self.start()
        naming_faces.forget(self.library)
        before = self.names()
        with self.assertRaises(NotFound):
            naming_faces.confirm(self.library, job.handle)
        self.assertEqual(before, self.names())

    def test_a_finished_job_is_read_from_the_library_after_a_restart(self):
        self.seed()
        job = self.start()
        naming_faces.confirm(self.library, job.handle)
        wait(job)
        naming_faces.forget(self.library)
        found = naming_faces.status(self.library, job.handle)
        self.assertEqual(naming_faces.DONE, found["state"])


class TwoLibraries(Case789):
    def test_each_library_names_its_own_faces_and_runs_beside_the_other(self):
        self.seed()
        other_path = self.home.library("quay.db")
        schema.ensure(other_path)
        other = Library(other_path)
        self.addCleanup(naming_faces.forget, other)
        photo = os.path.join(os.path.dirname(other_path), "Pictures", "q_001.jpg")
        def tree(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People", root_has_face=1)
                taxonomy.add_path(conn, "People/" + WREN)

        def rows(conn):
            photo_rows.add_read(conn, photo, {"XMP:Subject": ["People/" + WREN]})
            return faces.insert(conn, photo, [0, 0, 100, 100], at(0))
        write(other_path, tree)
        face = write(other_path, rows)
        mine = self.start()
        theirs = naming_faces.start(other)
        wait(theirs)
        self.assertEqual(naming_faces.ASKING, theirs.status()["state"])
        self.assertEqual(1, theirs.status()["plan"]["faces"])
        naming_faces.confirm(other, theirs.handle)
        wait(theirs)
        self.assertEqual(WREN, look(other_path, "SELECT name FROM faces WHERE id = ?", (face,))[0][0])
        self.assertEqual(naming_faces.ASKING, mine.status()["state"], "the other library's job did not answer this one's question")
        self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0])


if __name__ == "__main__":
    unittest.main()

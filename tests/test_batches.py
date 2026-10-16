"""One batch model: a journaled change written a chunk at a time, one at a time in a process (tagpup.services.batches,
file_changes.write_fields; docs/ARCHITECTURE.md "One batch model"; DECISIONS 2026-10-11). The library view's edit is the batch the
tests run (60 photos: three chunks); ExifTool is the fake table of tests/fake_exiftool.py. Fictional names only.
"""
import os
import socket
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

from tagpup.core.result import Conflict, Refused  # noqa: E402
from tagpup.services import batches, bulk_edit, file_changes, tagging  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402
from tagpup.web import lifecycle  # noqa: E402

ANOTHER = "otherhost:1:1"                                 # a live process on another machine: this one cannot tell
GONE = "%s:4000000:1" % socket.gethostname()             # a process of this machine that ended


def wait_until(check, what="the condition"):
    deadline = time.monotonic() + 30
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("never happened: %s" % what)
        threading.Event().wait(0.01)


def in_thread(call):
    """Run `call` on a thread of its own; (thread, [what it returned])."""
    answered = []
    thread = threading.Thread(target=lambda: answered.append(call()))
    thread.start()
    return thread, answered


class Chunks(T.Bulk):
    def test_a_writer_that_waits_is_let_in_between_two_chunks(self):
        saved = {}

        def hook(_path, n):
            if n == 10:
                def save():
                    with file_changes.exclusively():
                        saved["after"] = self.files.writes
                saved["thread"] = threading.Thread(target=save)
                saved["thread"].start()
                wait_until(lambda: file_changes._waiting == 1, "the save waits for the lock")
        self.files.on_write = hook
        self.tags_edit(add=["Trips/Coast"])
        saved["thread"].join(30)
        self.assertEqual(25, saved["after"], "the save ran after the first chunk of 25, not after all 60")
        self.assertEqual(self.PHOTOS, self.files.writes)

    def test_cancel_stops_at_the_chunk_applies_what_was_written_and_undoes_and_a_second_run_writes_the_rest_once(self):
        seen = {}

        def hook(_path, n):
            if n == 10:
                client = self.app.test_client()
                seen["listed"] = client.get("/api/activity/batches").get_json()["batches"]
                seen["cancelled"] = client.post("/api/activity/batches/cancel", json={
                    "library": self.library.name, "ticket": seen["listed"][0]["ticket"]}).status_code
        self.files.on_write = hook
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(200, seen["cancelled"])
        row = seen["listed"][0]
        self.assertEqual(("running", self.PHOTOS, True), (row["state"], row["photos"], row["here"]))
        self.assertEqual(self.PHOTOS, row["done"] + row["planned"] + row["conflict"], "the files' states are the progress")
        self.assertEqual((25, self.PHOTOS - 25), (done["changed"], done["error_count"]))
        self.assertIn("Stopped as asked after 25 of 60 photos", done["errors"][0]["why"])
        self.assertEqual([("applied",)], [(each[2],) for each in self.changes("bulk tags%")])
        self.assertEqual([("done", 25)], self.vl.rows("SELECT state, COUNT(*) FROM change_files GROUP BY state"),
                         "the files it did not reach were withdrawn")
        self.assertEqual([], batches.listing(self.library))
        self.files.on_write = None
        again = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS - 25, 25), (again["changed"], again["unchanged"]))
        self.assertEqual(self.PHOTOS, self.files.writes, "no file was written twice")
        first = self.changes("bulk tags%")[0][0]
        undone = file_changes.undo(self.library, first, "exiftool", apply=True)
        self.assertEqual(25, undone.changed, "what the stopped batch wrote can be undone")

    def test_a_stop_asked_while_the_files_are_read_writes_and_plans_nothing(self):
        self.addCleanup(batches.reopen)
        self.files.on_read = lambda *_: batches.stop_all()     # the edit's own claim is in line while it reads
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((0, self.PHOTOS, 0), (done["changed"], done["error_count"], self.files.writes))
        self.assertEqual([], self.changes("bulk tags%"))
        self.assertIn("before anything was written", done["errors"][0]["why"])


class Line(T.Bulk):
    def test_a_second_batch_waits_its_turn_then_runs_and_the_drain_names_them(self):
        gate = T.Gate(1)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        first, first_reply = in_thread(lambda: self.post(
            {"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}}, client=self.app.test_client()).status_code)
        self.assertTrue(gate.reached.wait(30))
        second, second_reply = in_thread(lambda: self.post(
            {"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Lakes"]}}, client=self.app.test_client()).status_code)
        wait_until(lambda: batches.running_here() == 2, "the second batch queues")
        listed = batches.listing(self.library)
        self.assertEqual([("running", 0), ("queued", 1)], [(each["state"], each["position"]) for each in listed])
        self.assertEqual([self.PHOTOS, self.PHOTOS], [each["photos"] for each in listed])
        self.assertEqual(["2 photo batches (120 photos in all, 1 waiting)"], batches.sentences())
        self.assertIn(lifecycle.Work(lifecycle.BATCH, None, "2 photo batches (120 photos in all, 1 waiting)"),
                      lifecycle.long_work())
        gate.release.set()
        first.join(60)
        second.join(60)
        self.assertEqual(([200], [200]), (first_reply, second_reply))
        for photo_id in self.ids:
            self.assertLessEqual({"Trips/Coast", "Trips/Lakes"}, set(self.tags(photo_id)))
        self.assertEqual(2, len(self.changes("bulk tags%")))
        self.assertEqual(0, batches.running_here())

    def test_nine_waiting_is_one_too_many_and_the_refusal_says_how_many_are_ahead(self):
        self.assertEqual(8, batches.MOST_WAITING)
        release, threads, refused = threading.Event(), [], []

        def hold():
            try:
                with batches.claim(self.library, "bulk tags", self.PHOTOS):
                    release.wait(30)
            except Conflict as why:
                refused.append(str(why))
        self.addCleanup(release.set)
        for n in range(1, batches.MOST_WAITING + 2):
            thread = threading.Thread(target=hold)
            thread.start()
            threads.append(thread)
            wait_until(lambda: batches.running_here() == n, "batch %d is in line" % n)
        threading.Thread(target=hold).start()
        wait_until(lambda: refused, "the tenth is refused")
        self.assertEqual("9 photo batches are ahead of this one (one running, 8 waiting). Try again when one has finished.", refused[0])
        release.set()
        for thread in threads:
            thread.join(30)
        self.assertEqual(0, batches.running_here())

    def test_a_waiting_batch_cancelled_never_starts(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = threading.Event()

        def first():
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                started.set()
                release.wait(30)
        holder = threading.Thread(target=first)
        holder.start()
        self.assertTrue(started.wait(30))
        waiter, outcome = in_thread(lambda: self.edit_refused())
        wait_until(lambda: batches.running_here() == 2, "the edit waits")
        queued = [each for each in batches.listing(self.library) if each["state"] == "queued"][0]
        self.assertTrue(batches.cancel(self.library, queued["ticket"]))
        waiter.join(30)
        self.assertEqual(0, self.files.writes)
        self.assertEqual(["The batch was cancelled before it began; nothing was written."], outcome)
        release.set()
        holder.join(30)
        self.assertEqual([], self.changes("bulk tags%"))

    def edit_refused(self):
        try:
            bulk_edit.run(self.library, bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]}), self.ids, "exiftool")
        except Refused as why:
            return str(why)

    def test_a_caller_that_holds_the_lock_does_not_wait_for_a_batch_that_needs_it(self):
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def first():
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                started.set()
                release.wait(30)
        holder = threading.Thread(target=first)
        holder.start()
        self.assertTrue(started.wait(30))
        with file_changes.exclusively():
            done = file_changes.write_fields(self.library, "x", "exiftool", [self.path(each) for each in self.ids], [], None)
        self.assertIn("A batch of photo files is running", done.refused)
        release.set()
        holder.join(30)


class Cap(T.Bulk):
    def test_the_cap_is_five_thousand_and_5001_is_refused_before_ExifTool_is_started(self):
        self.assertEqual(5000, batches.MAX_BATCH)
        done = file_changes.write_fields(self.library, "x", "exiftool", ["a%d.jpg" % n for n in range(5001)], [], None)
        self.assertEqual("Select up to 5,000 photos at a time (5,001 were named).", done.refused)
        self.assertEqual((0, 0), (self.files.starts, self.files.read_calls))

    def test_organizes_bulk_tags_and_smart_rename_over_the_cap_are_refused_before_a_turn_is_taken_or_a_file_read(self):
        """The cap is file_changes' (too_many), not a route's: over it, a write that tells the page's records in order is
        refused before its batch claims a turn (nothing waits behind it), and Smart Rename before a caption is read."""
        from tagpup.files import names
        from tagpup.services import photos as photo_actions
        with mock.patch.object(batches, "MAX_BATCH", 30), mock.patch.object(batches, "claim") as claim, \
                mock.patch.object(names, "read_for_renaming") as read_captions:
            with self.assertRaises(Refused) as refused:
                with file_changes.batch_exclusively(self.library, "bulk tags", 31):
                    self.fail("the write began")
            renamed = photo_actions.smart_rename(self.library, [self.path(each) for each in self.ids[:31]], "Regatta",
                                                 "{grouping} - {index} - {caption}", "exiftool")
        self.assertEqual("Select up to 30 photos at a time (31 were named).", str(refused.exception))
        self.assertEqual("Select up to 30 photos at a time (31 were named).", renamed.refused)
        self.assertEqual((0, 0, 0), (claim.call_count, read_captions.call_count, self.files.read_calls))

    def test_at_the_cap_it_is_written_and_over_it_it_is_refused_with_nothing_read(self):
        with mock.patch.object(batches, "MAX_BATCH", 30):
            ok = self.tags_edit(add=["Trips/Coast"], selection_body={"ids": self.ids[:30]})
            self.assertEqual(30, ok["changed"])
            reads = self.files.read_calls
            edit = bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Lakes"]})
            refused = bulk_edit.run(self.library, edit, self.ids[:31], "exiftool")      # past the selection's own check
        self.assertEqual((0, 31), (refused.changed, len(refused.errors)))
        self.assertIn("Select up to 30 photos at a time (31 were named)", refused.errors[0][2])
        self.assertEqual(reads, self.files.read_calls)

    def test_the_tag_trees_rewrites_have_no_cap(self):
        wanted = [self.path(each) for each in self.ids]
        with mock.patch.object(batches, "MAX_BATCH", 30):
            capped = tagging.change_tags(self.library, wanted, ["Trips/Coast"], [], "exiftool")
            self.assertIn("Select up to 30", capped.refused)
            renamed = tagging.replace_tag(self.library, wanted, "Old/Stuff", "Old/Things", "exiftool")
        self.assertEqual((None, 20), (renamed.refused, renamed.changed), "a third of the 60 carry the tag")


class SeenFromTheJournal(T.Bulk):
    def plan_by(self, owner, files=30):
        untagged = [each for n, each in enumerate(self.ids) if n % 3]       # the photos that hold no tag
        change_id, _rows = file_journal.plan(self.library.path, "bulk tags", [
            {"photo_id": each, "path": self.path(each), "before": {"XMP:Subject": []}, "after": {"XMP:Subject": ["Trips/Coast"]}}
            for each in untagged[:files]])
        db.write_with_connection(self.library.path, lambda conn: conn.execute(
            "UPDATE changes SET owner = ? WHERE id = ?", (owner, change_id)), label="test: whose change")
        return change_id

    def test_a_change_of_another_process_is_listed_not_queued_and_cannot_be_cancelled_here(self):
        change_id = self.plan_by(ANOTHER)
        listed = batches.listing(self.library)
        self.assertEqual([(change_id, 30, 30, 0, "running", False, None)], [
            (each["id"], each["photos"], each["planned"], each["done"], each["state"], each["here"], each["ticket"]) for each in listed])
        self.assertEqual(0, batches.running_here())
        reply = self.app.test_client().post("/api/activity/batches/cancel", json={"library": self.library.name, "ticket": 1})
        self.assertEqual(404, reply.status_code)

    def test_a_change_whose_process_is_gone_is_not_running_and_the_next_open_settles_it(self):
        change_id = self.plan_by(GONE)
        self.assertEqual([], batches.listing(self.library), "a crash left it; nothing is writing it")
        self.assertEqual(1, file_changes.settle(self.library, "exiftool"))
        self.assertEqual("applied", file_journal.change(self.library.path, change_id).status)
        self.assertEqual(30, len([each for each in self.ids if self.tags(each) == ["Trips/Coast"]]))

    def test_a_small_change_is_a_save_and_not_a_batch(self):
        self.plan_by(ANOTHER, files=batches.CHUNK)
        self.assertEqual([], batches.listing(self.library))

    def test_a_library_that_cannot_be_read_lists_this_processs_own_and_says_so_in_the_log(self):
        gone = types.SimpleNamespace(path=os.path.join(self.home.root, "missing.db"), key="missing", name="missing")
        with self.assertLogs("tagpup.services.batches", "WARNING"):
            self.assertEqual([], batches.listing(gone))


class HandOver(T.Bulk):
    def test_a_drain_stops_a_running_batch_within_a_chunk_and_a_stopped_one_is_applied(self):
        gate = T.Gate(1)

        def slow(path, n):
            gate(path, n)
            threading.Event().wait(0.02)           # a slow ExifTool: a chunk of 25 takes half a second
        self.files.on_write = slow
        self.addCleanup(gate.release.set)
        self.addCleanup(batches.reopen)
        edit = bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]})
        writing, outcome = in_thread(lambda: bulk_edit.run(self.library, edit, self.ids, "exiftool"))
        self.assertTrue(gate.reached.wait(30))
        moving = lifecycle.Lifecycle()
        draining, drained = in_thread(lambda: moving.drain(seconds=30, lease=60))
        wait_until(lambda: [each["state"] for each in batches.listing(self.library)] == ["stopping"], "the batch is asked to stop")
        asked = time.monotonic()
        gate.release.set()
        writing.join(30)
        draining.join(30)
        self.assertLess(time.monotonic() - asked, 5, "it ended within a chunk, not after 60 photos")
        self.assertEqual([{"drained": True}], drained)
        self.assertEqual((25, self.PHOTOS - 25), (outcome[0].changed, len(outcome[0].errors)))
        self.assertEqual(25, self.files.writes)
        self.assertEqual([("applied",)], [(each[2],) for each in self.changes("bulk tags%")], "applied, in History, undoable")

    def test_a_drain_refuses_at_once_for_other_work_and_does_not_stop_the_batch(self):
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        stopped = []

        def hold():
            with batches.claim(self.library, "bulk tags", self.PHOTOS) as batch:
                started.set()
                release.wait(30)
                stopped.append(batch.stop.is_set())
        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(started.wait(30))
        with mock.patch("tagpup.jobs.suggestions.under_way", return_value=[{"library": self.library.name}]):
            answer = lifecycle.Lifecycle().drain(seconds=1, lease=60)
        self.assertEqual({"drained": False, "waiting_for": ["1 Suggest run(s)"]}, answer)
        release.set()
        holder.join(30)
        self.assertEqual([False], stopped)


class AfterTheServerClosed(T.Bulk):
    def test_a_batch_that_claims_after_the_drain_asked_is_stopped_before_it_begins_and_a_resume_lets_them_in(self):
        self.addCleanup(batches.reopen)
        moving = lifecycle.Lifecycle()
        self.assertEqual({"drained": True}, moving.drain(seconds=5, lease=60))
        with self.assertRaises(Refused):
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                self.fail("it began")
        moving.resume()
        with batches.claim(self.library, "bulk tags", self.PHOTOS):
            pass


class TheClaimIsNeeded(T.Bulk):
    def test_more_than_a_chunk_written_without_the_claim_raises(self):
        wanted = [self.path(each) for each in self.ids[:batches.CHUNK + 1]]
        with self.assertRaises(RuntimeError):
            file_changes._write_session(self.library, "x", "exiftool", wanted, [], None, None, "fail", False, None, None, (), None)
        self.assertEqual(0, self.files.starts)


if __name__ == "__main__":
    unittest.main()

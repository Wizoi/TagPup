"""The 9d-1 follow-up review's findings #583 to #589. A time shift is the one operation that cannot be done twice, and each is a way
its progress record can be missing, stale or false; the rule they test: NOTHING IS WRITTEN TO A PHOTO FILE UNTIL THE RECORD THAT
LETS A RESUME KNOW ABOUT IT IS DURABLY WRITTEN, and a record is never older than the files (tests/test_bulk_edits_review.py has
the earlier round). Scenarios are the reviewer's. Fictional names only.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402
import test_bulk_edits_review as R  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import exiftool_session, job_files  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402
from tagpup.store import file_journal  # noqa: E402


class Followup(R.Crashing):
    def setUp(self):
        super().setUp()
        for name in ("PERSIST_SECONDS",):
            patcher = mock.patch.object(bulk_edits, name, 0, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def failing_writes_of_state(self, times, after_chunk=True, before_chunk=True):
        """bulk_edit.write_state raising PermissionError (WinError 5: another handle holds the file) for the first `times` writes
        that are made at the end of chunk 1 (`done` 25), before chunk 2 and after it."""
        real, seen = bulk_edit.write_state, []

        def flaky(library, job, state):
            around_chunk_two = state.get("done") == 25 and (
                (before_chunk and state.get("inflight", 0) > 25) or (after_chunk and state.get("inflight", 0) == 25))
            if around_chunk_two and len(seen) < times:
                seen.append(1)
                raise PermissionError(5, "Access is denied")
            return real(library, job, state)
        self.seen_failures = seen
        return mock.patch.object(bulk_edit, "write_state", flaky)

    def resume_and_finish(self, handle):
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        return self.finish(handle)


class TheRecordBeforeTheChunk(Followup):
    def test_the_reviewers_scenario_one_failed_write_before_chunk_two_then_a_crash_shifts_nothing_twice(self):
        self.die_at_write(30)
        with self.failing_writes_of_state(1, after_chunk=False):
            handle = self.run_until_crash()
        self.assertEqual(1, len(self.seen_failures))
        state = bulk_edit.read_state(self.library, handle)
        self.assertEqual((25, 50), (state["done"], state["inflight"]), "the chunk in flight is in the record")
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_the_writes_after_one_chunk_and_before_the_next_failing_twice_and_more_is_retried(self):
        for times in (2, 3, 4):
            with self.subTest(times=times):
                self.tearDown_library()
                self.die_at_write(30)
                with self.failing_writes_of_state(times):
                    handle = self.run_until_crash()
                self.resume_and_finish(handle)
                self.assert_every_photo_shifted_once()

    def tearDown_library(self):
        """A fresh library for the next case of a loop: the files and rows of setUp again."""
        self.files.writes = 0
        self.files.writes_of.clear()
        self.files.on_write = self.files.on_read = None
        for photo_id in self.ids:
            self.files.hold(self.path(photo_id), EXIF__DateTimeOriginal=self.taken[photo_id])
        db_path = self.library.path
        from tagpup.store import db
        db.write_with_connection(db_path, lambda conn: [conn.execute("DELETE FROM job_runs"), conn.execute("DELETE FROM change_files"),
                                                        conn.execute("DELETE FROM changes")])
        bulk_edits._jobs.clear()
        for name in os.listdir(self.library.bulk_jobs) if os.path.isdir(self.library.bulk_jobs) else ():
            os.remove(os.path.join(self.library.bulk_jobs, name))

    def test_a_record_that_cannot_be_written_stops_the_job_before_the_chunk_and_it_resumes(self):
        with self.failing_writes_of_state(10 ** 6, after_chunk=False):
            handle = self.shift_job(90)["job"]
            done = self.finish(handle)
        self.assertEqual(("failed", 25, True), (done["state"], done["done"], done["resumable"]))
        self.assertIn("could not record where it had got to", done["message"])
        self.assertEqual(25, self.files.writes, "nothing of chunk 2 was written")
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_state_replaced_over_a_file_another_handle_holds_open_is_tried_again(self):
        # Windows: os.replace over a file open elsewhere (the other TagPup process's status, Defender, the search indexer) fails
        # until it lets go. The reader here lets go after a moment.
        import tempfile
        folder = tempfile.mkdtemp()
        job_files.write_state(folder, 1, {"seq": 1})
        held, let_go = threading.Event(), threading.Event()

        def reader():
            with open(job_files.state_path(folder, 1), "rb") as handle:
                handle.read()
                held.set()
                let_go.wait(10)
        thread = threading.Thread(target=reader)
        thread.start()
        self.assertTrue(held.wait(10))
        timer = threading.Timer(0.15, let_go.set)
        timer.start()
        try:
            job_files.write_state(folder, 1, {"seq": 2})
        finally:
            let_go.set()
            thread.join(10)
            timer.cancel()
        self.assertEqual(2, job_files.read_state(folder, 1)["seq"])

    def test_a_replace_refused_at_the_file_level_is_tried_five_times(self):
        import tempfile
        folder = tempfile.mkdtemp()
        calls = []
        real = job_files._replace

        def refuses_twice(source, target):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(5, "Access is denied")
            return real(source, target)
        with mock.patch.object(job_files, "_replace", refuses_twice), mock.patch.object(job_files, "_pause", lambda seconds: None):
            job_files.write_state(folder, 3, {"seq": 1})
        self.assertEqual(3, len(calls))
        with mock.patch.object(job_files, "_replace", mock.Mock(side_effect=PermissionError(5, "x"))), \
                mock.patch.object(job_files, "_pause", lambda seconds: None):
            with self.assertRaises(PermissionError):
                job_files.write_state(folder, 4, {"seq": 1})
        self.assertEqual([], [name for name in os.listdir(folder) if ".tmp-" in name], "no temporary file is left")

    def test_the_status_reader_holds_the_file_open_only_for_the_read(self):
        import tempfile
        folder = tempfile.mkdtemp()
        job_files.write_state(folder, 1, {"seq": 1})
        job_files.read_state(folder, 1)
        job_files.write_state(folder, 1, {"seq": 2})     # would fail on Windows if the reader still held it
        self.assertEqual(2, job_files.read_state(folder, 1)["seq"])


class AfterAStalledCommand(Followup):
    def write_then_time_out(self, number, write_first=True):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")
        real = self.files.set_tags
        self.files.writes_seen = []

        def set_tags(photo_paths, tags=None, params=None):
            self.files.writes_seen.append(photo_paths[0])
            if len(self.files.writes_seen) == number:
                if write_first:
                    real(photo_paths, tags, params)
                raise stalled
            return real(photo_paths, tags, params)
        self.files.set_tags = set_tags

    def test_a_write_that_landed_and_timed_out_is_found_by_reading_it_and_counted_changed(self):
        self.write_then_time_out(3)
        done = self.finish(self.shift_job(90)["job"])
        third = self.files.writes_seen[2]
        self.assertEqual("done", done["state"])
        self.assertEqual(3, self.files.writes_of[paths.key(third)] + 2, "the file was written once")
        self.assertEqual(self.shifted(self.taken[self.id_of(third)]), self.files.taken_of(third))
        self.assertEqual((2 + 1 + 35, 22), (done["changed"], done["error_count"]))
        self.assertNotIn(self.id_of(third), [each["id"] for each in done["errors"]], "it is not listed among the never written")
        self.assertIn("was not shifted", done["errors"][0]["why"])
        rows = self.vl.rows("SELECT state FROM change_files WHERE path = ?", paths.stored(third))
        self.assertEqual([("done",)], rows)

    def test_a_write_that_timed_out_before_it_wrote_is_an_error_saying_it_was_not_shifted(self):
        self.write_then_time_out(3, write_first=False)
        done = self.finish(self.shift_job(90)["job"])
        third = self.files.writes_seen[2]
        self.assertEqual((2 + 35, 23), (done["changed"], done["error_count"]))
        self.assertEqual(self.taken[self.id_of(third)], self.files.taken_of(third))
        self.assertIn(self.id_of(third), [each["id"] for each in done["errors"]])
        self.assertEqual([], self.vl.rows("SELECT state FROM change_files WHERE path = ?", paths.stored(third)),
                         "taken out of the change: it was not written")

    def test_a_read_back_that_times_out_after_every_write_landed_changes_nothing_and_blames_nobody(self):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")
        fired = []

        def hook(_path, _n):
            if self.files.writes >= 25 and not fired:
                fired.append(1)
                raise stalled
        self.files.on_read = hook
        done = self.finish(self.shift_job(90)["job"])
        self.assertEqual(("done", self.PHOTOS, 0), (done["state"], done["changed"], done["error_count"]))
        self.assert_every_photo_shifted_once()

    def test_when_the_files_cannot_be_read_afterwards_either_the_job_stops_with_the_chunk_in_flight_and_a_resume_decides(self):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")
        self.write_then_time_out(3)
        deaf = []
        real_write = self.files.set_tags

        def set_tags(photo_paths, tags=None, params=None):
            try:
                return real_write(photo_paths, tags, params)
            finally:
                if len(self.files.writes_seen) == 3:
                    deaf.append(1)
        self.files.set_tags = set_tags

        def hook(_path, _n):
            if deaf:
                raise stalled
        self.files.on_read = hook
        handle = self.shift_job(90)["job"]
        failed = self.finish(handle)
        self.assertEqual(("failed", 0, True), (failed["state"], failed["done"], failed["resumable"]))
        self.assertIn("not known which of them were shifted", failed["message"])
        state = bulk_edit.read_state(self.library, handle)
        self.assertEqual(25, state["inflight"], "the chunk stays in flight in the record")
        self.assertEqual(0, failed["changed"], "nothing is counted of a chunk that could not be decided")
        # The files can be read again: the resume reads the conflicts and decides, and shifts nothing twice.
        deaf.clear()
        self.files.on_read = None
        self.files.set_tags = type(self.files).set_tags.__get__(self.files)
        self.files.writes_seen = []
        done = self.resume_and_finish(handle)
        self.assertEqual("done", done["state"])
        self.assert_every_photo_shifted_once()

    def test_a_resume_that_cannot_read_the_conflicts_changes_nothing_and_says_try_again(self):
        self.write_then_time_out(3)
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")
        self.files.on_read = lambda _path, _n: (_ for _ in ()).throw(stalled) if len(self.files.writes_seen) >= 3 else None
        handle = self.shift_job(90)["job"]
        self.finish(handle)
        before = bulk_edit.read_state(self.library, handle)
        reply = self.post("resume", {"job": handle})
        self.assertEqual(409, reply.status_code)
        self.assertEqual(before, bulk_edit.read_state(self.library, handle))
        self.files.on_read = None
        self.files.set_tags = type(self.files).set_tags.__get__(self.files)
        self.files.writes_seen = []
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def id_of(self, path):
        return next(photo_id for photo_id in self.ids if paths.key(self.path(photo_id)) == paths.key(path))


class AllOrNothing(Followup):
    def test_a_start_whose_list_cannot_be_written_leaves_nothing_registered_claimed_or_on_disk(self):
        from tagpup.web import lifecycle
        with mock.patch.object(bulk_edit, "write_ids", mock.Mock(side_effect=OSError(28, "No space left on C:\\private"))):
            reply = self.post("start", {"op": "time_shift", "selection": T.ALL, "params": {"minutes": 5}})
        self.assertEqual(409, reply.status_code)
        self.assertNotIn("private", reply.get_json()["error"])
        self.assertEqual((0, []), (bulk_edits.running(self.library), lifecycle.long_work()))
        self.assertEqual([], [name for name in os.listdir(self.library.bulk_jobs)] if os.path.isdir(self.library.bulk_jobs) else [])
        self.assertEqual(0, self.vl.rows("SELECT COUNT(*) FROM job_runs")[0][0], "the claim's row was given back")
        self.assertEqual(200, self.post("start", {"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}}).status_code)
        bulk_edits._held(self.library)[next(iter(bulk_edits._held(self.library)))].thread.join(60)

    def test_a_start_whose_thread_cannot_be_started_leaves_nothing_behind(self):
        with mock.patch.object(bulk_edits, "_launch", mock.Mock(side_effect=RuntimeError("can't start new thread"))):
            reply = self.post("start", {"op": "time_shift", "selection": T.ALL, "params": {"minutes": 5}})
        self.assertEqual(409, reply.status_code)
        self.assertEqual(0, bulk_edits.running(self.library))
        self.assertEqual([], os.listdir(self.library.bulk_jobs))
        self.assertEqual(200, self.post("start", {"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}}).status_code)

    def test_a_resume_that_cannot_start_its_thread_restores_the_job_and_the_record(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        before = bulk_edit.read_state(self.library, handle)
        with mock.patch.object(bulk_edits, "_launch", mock.Mock(side_effect=RuntimeError("boom at C:\\secret"))):
            reply = self.post("resume", {"job": handle})
        self.assertEqual(409, reply.status_code)
        self.assertNotIn("secret", reply.get_json()["error"])
        self.assertEqual(0, bulk_edits.running(self.library))
        self.assertEqual(before["done"], bulk_edit.read_state(self.library, handle)["done"])
        self.assertEqual(before["inflight"], bulk_edit.read_state(self.library, handle)["inflight"])
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_start_that_is_stopped_by_the_process_dying_is_not_swallowed(self):
        with mock.patch.object(bulk_edits, "_launch", mock.Mock(side_effect=T.Crash())):
            with self.assertRaises(T.Crash):
                bulk_edits.start(self.library, bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]}), self.ids, "exiftool")
        self.assertEqual(0, bulk_edits.running(self.library))


class _CrashInResume:
    """The reviewer's D, E, F, G and I: a process dying at each point of a resume, of a second run, of a chunk."""

    def crash_in_resume(self, where):
        self.die_at_write(30)
        handle = self.run_until_crash()
        patches = []
        if where == "after_claim":
            patches.append(mock.patch.object(bulk_edits, "_prepare_resume", mock.Mock(side_effect=T.Crash())))
        elif where == "after_state_read":
            patches.append(mock.patch.object(bulk_edit, "read_ids", mock.Mock(side_effect=T.Crash())))
        elif where == "in_settle":
            patches.append(mock.patch.object(file_changes, "settle", mock.Mock(side_effect=T.Crash())))
        elif where == "after_settle":
            real = bulk_edits._settle_flight

            def after(library, job, state):
                real(library, job, state)
                raise T.Crash()
            patches.append(mock.patch.object(bulk_edits, "_settle_flight", after))
        elif where == "after_state_write":
            patches.append(mock.patch.object(bulk_edits, "_launch", mock.Mock(side_effect=T.Crash())))
        for each in patches:
            each.start()
        try:
            with self.assertRaises(T.Crash):
                bulk_edits.resume(self.library, handle, "exiftool")
        finally:
            for each in patches:
                each.stop()
        self.crash()
        done = self.resume_and_finish(handle)
        self.assertEqual(("done", self.PHOTOS, 0), (done["state"], done["done"], done["error_count"]))
        self.assert_every_photo_shifted_once()

    def second_crash(self, number):
        self.die_at_write(30)
        handle = self.run_until_crash()
        self.die_at_write(number)
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            try:
                self.post("resume", {"job": handle})       # the second death can be in the settle, inside the request
            except T.Crash:
                pass
            job = bulk_edits._held(self.library).get(handle)
            if job is not None and job.thread is not None:
                job.thread.join(60)
        self.crash()
        self.files.on_write = None
        done = self.resume_and_finish(handle)
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS, 0), (done["state"], done["done"], done["changed"], done["error_count"]))
        self.assert_every_photo_shifted_once()


class EveryPlaceAResumeCanDie(_CrashInResume, Followup):
    def test_after_the_claim(self):
        self.crash_in_resume("after_claim")

    def test_after_the_state_is_read(self):
        self.crash_in_resume("after_state_read")

    def test_in_the_settle(self):
        self.crash_in_resume("in_settle")

    def test_after_the_settle(self):
        self.crash_in_resume("after_settle")

    def test_after_the_state_is_written(self):
        self.crash_in_resume("after_state_write")

    def test_a_second_crash_in_the_credited_chunk(self):
        self.second_crash(40)

    def test_a_second_crash_in_the_next_chunk(self):
        self.second_crash(60)

    def test_a_second_crash_at_the_first_write_of_the_resume(self):
        self.second_crash(31)

    def test_a_crash_with_the_credit_pending(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None), \
                mock.patch.object(bulk_edit, "run_chunk", mock.Mock(side_effect=T.Crash())):
            self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        done = self.resume_and_finish(handle)
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS, 0), (done["state"], done["done"], done["changed"], done["error_count"]))
        self.assert_every_photo_shifted_once()

    def step_crash(self, step, number):
        seen, real = [], file_changes._reached

        def die(s):
            real(s)
            if s == step:
                seen.append(1)
                if len(seen) == number:
                    raise T.Crash()
        with mock.patch.object(file_changes, "_reached", die):
            handle = self.run_until_crash()
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_crash_after_a_file_was_recorded(self):
        self.step_crash("file recorded", 30)

    def test_a_crash_after_the_last_file_of_a_chunk_was_recorded(self):
        self.step_crash("file recorded", 50)

    def test_a_crash_in_the_read_back(self):
        real, calls = file_changes._read_back, []

        def dies(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise T.Crash()
            return real(*a, **k)
        with mock.patch.object(file_changes, "_read_back", dies):
            handle = self.run_until_crash()
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_crash_after_a_chunk_and_before_its_record(self):
        real, calls = bulk_edits.Job._take, []

        def dies(self_, out, size):
            real(self_, out, size)
            calls.append(1)
            if len(calls) == 2:
                raise T.Crash()
        with mock.patch.object(bulk_edits.Job, "_take", dies):
            handle = self.run_until_crash()
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_two_resumes_at_once_begin_one(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        barrier = threading.Barrier(2)
        out = []
        real = bulk_edits.runs_service.claim

        def claim(*a, **k):
            barrier.wait(10)
            return real(*a, **k)

        def go():
            try:
                out.append(bulk_edits.resume(self.library, handle, "exiftool"))
            except Exception as problem:
                out.append(problem)
        with mock.patch.object(bulk_edits.runs_service, "claim", claim):
            threads = [threading.Thread(target=go) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        jobs = [each for each in out if isinstance(each, bulk_edits.Job)]
        self.assertEqual(1, len(jobs))
        jobs[0].thread.join(60)
        self.assert_every_photo_shifted_once()


class OneIdentityAcrossRuns(Followup):
    def cancel_in_chunk_two(self):
        gate = T.Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.shift_job(90)["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": handle})
        gate.release.set()
        self.finish(handle)
        self.files.on_write = None
        return handle

    def test_a_resumed_job_that_finished_is_done_and_not_resumable_after_a_restart(self):
        handle = self.cancel_in_chunk_two()
        self.resume_and_finish(handle)
        bulk_edits._jobs.clear()
        seen = self.status(handle)
        self.assertEqual(("done", False, self.PHOTOS), (seen["state"], seen["resumable"], seen["done"]))
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code)

    def test_it_stays_done_when_the_job_leaves_the_jobs_kept_in_memory(self):
        handle = self.cancel_in_chunk_two()
        self.resume_and_finish(handle)
        with mock.patch.object(bulk_edits, "KEPT", 1):
            self.finish(self.tags_job(add=["Trips/Coast"], selection={"ids": self.ids[:2]})["job"])
            self.finish(self.tags_job(add=["Trips/Lakes"], selection={"ids": self.ids[:2]})["job"])
        self.assertNotIn(handle, bulk_edits._held(self.library))
        seen = self.status(handle)
        self.assertEqual(("done", False), (seen["state"], seen["resumable"]))

    def test_a_process_dying_between_forgetting_the_record_and_ending_the_run_promises_no_resume(self):
        handle = self.cancel_in_chunk_two()
        with mock.patch.object(bulk_edits.runs_service, "end", mock.Mock(side_effect=T.Crash())):
            self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        seen = self.status(handle)
        self.assertEqual(False, seen["resumable"])
        self.assertNotIn("Resume it", seen["message"] or "")
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code, "what the status promises is what the route does")

    def test_resumable_means_the_list_and_the_state_both_exist(self):
        handle = self.cancel_in_chunk_two()
        self.assertTrue(self.status(handle)["resumable"])
        bulk_edit.forget_ids(self.library, handle)
        self.assertFalse(self.status(handle)["resumable"])
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code)
        bulk_edits._jobs.clear()
        self.assertFalse(self.status(handle)["resumable"])


class WhatIsKeptAndForHowLong(Followup):
    def cancel_in_chunk_two(self):
        return OneIdentityAcrossRuns.cancel_in_chunk_two(self)

    def age(self, handle, days, only=None):
        then = time.time() - days * 86400
        for name in os.listdir(self.library.bulk_jobs):
            if name.startswith("%d." % handle) and (only is None or name.endswith(only)):
                os.utime(os.path.join(self.library.bulk_jobs, name), (then, then))

    def test_the_reviewers_scenario_an_old_list_with_a_fresh_state_is_not_taken_by_the_next_job(self):
        handle = self.cancel_in_chunk_two()
        self.age(handle, 8, only=".ids")
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(sorted(["%d.ids" % handle, "%d.state.json" % handle]), sorted(os.listdir(self.library.bulk_jobs)))
        self.assertTrue(self.status(handle)["resumable"])
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        self.finish(handle)

    def test_a_resumable_shift_is_kept_a_month_by_its_last_activity_and_then_both_files_go_together(self):
        handle = self.cancel_in_chunk_two()
        self.age(handle, 29)
        self.assertEqual([], bulk_edit.sweep(self.library))
        self.assertEqual(2, len(os.listdir(self.library.bulk_jobs)))
        self.age(handle, 31, only=".ids")
        self.age(handle, 5, only=".state.json")
        self.assertEqual([], bulk_edit.sweep(self.library), "the state was touched five days ago: the job is alive")
        self.age(handle, 31)
        self.assertEqual([handle], bulk_edit.sweep(self.library))
        self.assertEqual([], os.listdir(self.library.bulk_jobs))

    def test_anything_else_is_kept_a_week(self):
        os.makedirs(self.library.bulk_jobs, exist_ok=True)
        for name in ("71.state.json", "72.ids"):
            with open(os.path.join(self.library.bulk_jobs, name), "w") as handle:
                handle.write("{}")
            then = time.time() - 8 * 86400
            os.utime(os.path.join(self.library.bulk_jobs, name), (then, then))
        self.assertEqual([], bulk_edit.sweep(self.library), "a state alone, or a list alone, is not a resumable pair")
        self.assertEqual([], os.listdir(self.library.bulk_jobs))

    def test_a_shift_that_expired_says_so_and_offers_nothing_that_would_be_refused(self):
        handle = self.cancel_in_chunk_two()
        self.age(handle, 31)
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])      # the next job's start sweeps
        seen = self.status(handle)
        self.assertEqual(("expired", False), (seen["state"], seen["resumable"]))
        self.assertIn("Too old to resume", seen["message"])
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code)
        bulk_edits._jobs.clear()
        self.assertEqual("expired", self.status(handle)["state"])

    def test_the_library_keeps_the_first_run_of_a_resumable_job_however_many_runs_follow(self):
        handle = self.cancel_in_chunk_two()
        self.vl.conn.executemany("INSERT INTO job_runs (job, library, started, finished, outcome, changed) VALUES"
                                 " ('bulk edit', 'library', '2026-10-03 11:00:00', '2026-10-03 11:00:01', 'done', '{}')",
                                 [() for _ in range(60)])
        self.vl.conn.commit()
        self.finish(self.tags_job(add=["Trips/Coast"], selection={"ids": self.ids[:2]})["job"])
        self.assertEqual(1, self.vl.rows("SELECT COUNT(*) FROM job_runs WHERE id = ?", handle)[0][0], "the job's own id is still a run")
        seen = self.status(handle)
        self.assertEqual(("cancelled", True), (seen["state"], seen["resumable"]))

    def test_sixty_refused_resumes_use_up_no_run_and_leave_the_job_resumable(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        runs_before = self.vl.rows("SELECT COUNT(*) FROM job_runs")[0][0]
        with self.failing_settle(60):
            for _ in range(60):
                self.assertEqual(409, self.post("resume", {"job": handle}).status_code)
        self.assertEqual(runs_before, self.vl.rows("SELECT COUNT(*) FROM job_runs")[0][0])
        seen = self.status(handle)
        self.assertEqual(("abandoned", True), (seen["state"], seen["resumable"]))
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()


class SmallerThings(Followup):
    def test_the_sequence_number_is_used_up_only_by_a_write_that_worked(self):
        handle = self.shift_job(5)["job"]
        job = bulk_edits._held(self.library)[handle]
        job.thread.join(60)
        before = job.seq
        with mock.patch.object(bulk_edit, "write_state", mock.Mock(side_effect=OSError("disk"))):
            self.assertFalse(job._persist())
        self.assertEqual(before, job.seq)
        self.assertTrue(job._persist())
        self.assertEqual(before + 1, job.seq)

    def test_a_claim_held_by_this_process_is_not_called_another_process(self):
        from tagpup.store import file_journal as journal
        self.vl.conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES"
                             " ('bulk edit', 'library', '2026-10-03 10:00:00', 'running', ?)", (journal.owner(),))
        self.vl.conn.commit()
        refused = self.tags_job(add=["Trips/Coast"], expect=409)
        self.assertIn("in this TagPup", refused["error"])
        self.assertNotIn("another", refused["error"])

    def test_the_journal_queries_agree_with_the_plain_join_on_awkward_rows(self):
        conn = self.vl.conn
        ops = ["bulk time shift (job 1)", "bulk time shift (job 2)", "bulk tags (job 1)", "add to all selected"]
        states = ["done", "planned", "writing", "conflict", "undone", "done"]
        for op in ops:
            for status in ("applied", "undone", "planned"):
                change = conn.execute("INSERT INTO changes (operation, status, schema_version, created, summary)"
                                      " VALUES (?, ?, 20, ?, '{}')", (op, status, "2026-10-03 10:00:00")).lastrowid
                for k, state in enumerate(states):
                    conn.execute("INSERT INTO change_files (change_id, photo_id, path, fields_before, fields_after, state)"
                                 " VALUES (?, ?, 'p', '{}', '{}', ?)", (change, None if k == 5 else change * 10 + k, state))
        conn.commit()
        plain = ("SELECT f.photo_id FROM change_files f JOIN changes c ON c.id = f.change_id"
                 " WHERE c.operation = ? AND f.state = 'done' AND f.photo_id IS NOT NULL")
        for op in ops + ["nothing"]:
            self.assertEqual({row[0] for row in conn.execute(plain, (op,))}, file_journal.photo_ids_done(self.vl.path, op), op)
            wanted = sorted(row[0] for row in conn.execute(
                "SELECT f.id FROM change_files f JOIN changes c ON c.id = f.change_id WHERE c.operation = ? AND f.state = 'conflict'", (op,)))
            self.assertEqual(wanted, sorted(each.id for each in file_journal.files_of_operation(self.library.path, op, "conflict")), op)


if __name__ == "__main__":
    unittest.main()

"""The 9d-1 review's findings #577 to #581, each a test that failed on the code it was found in (tests/test_bulk_edits.py is the
job's own).

#577 a resume whose settle fails must leave the record as it was; #578 the state file and the journal are the record, never one
process's memory; #579 the journal's question of what a job wrote must not scan every file row; #580 a date shifted out of range
is an error, an absurd shift is refused, a slow claim blocks nothing, a finished job keeps nothing, and no exception's text is
kept; #581 a stalled ExifTool costs a chunk one minute and is never retried photo by photo.
Fictional names only.
"""
import itertools
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.files import exiftool_session, field_values  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402


class Crashing(T.Bulk):
    """A process that dies in the middle of a time shift and a restart."""

    def die_at_write(self, number):
        def die(_path, n):
            if n == number:
                raise T.Crash()
        self.files.on_write = die

    def die_after_file(self, number):
        seen, real = [], file_changes._reached

        def die(step):
            real(step)
            if step == "file written":
                seen.append(1)
                if len(seen) == number:
                    raise T.Crash()
        return mock.patch.object(file_changes, "_reached", die)

    def die_reading(self, photo_id):
        doomed = paths.key(self.path(photo_id))

        def die(path, _n):
            if paths.key(path) == doomed:
                raise T.Crash()
        self.files.on_read = die

    def run_until_crash(self, minutes=90):
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            handle = self.shift_job(minutes)["job"]
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        self.files.on_write = self.files.on_read = None
        return handle

    def assert_every_photo_shifted_once(self, minutes=90):
        for photo_id in self.ids:
            key = paths.key(self.path(photo_id))
            self.assertEqual(self.shifted(self.taken[photo_id], minutes), self.files.taken_of(self.path(photo_id)), photo_id)
            self.assertEqual(1, self.files.writes_of[key], "photo %d was written %d times" % (photo_id, self.files.writes_of[key]))

    def shifted(self, taken, minutes=90):
        from tagpup.core import dates
        return dates.shifted(taken, minutes)

    def failing_settle(self, times):
        """A settle that raises `times` times (the library busy under another process's long write), then works."""
        real, calls = file_changes.settle, []

        def settle(*args, **kwargs):
            calls.append(1)
            if len(calls) <= times:
                raise RuntimeError("database is locked")
            return real(*args, **kwargs)
        return mock.patch.object(file_changes, "settle", settle)

    def resume_that_cannot_settle(self, handle, times):
        """`times` resumes, each while the settle fails: each is an error with a sentence, and the record is as it was."""
        for _ in range(times):
            before = bulk_edit.read_state(self.library, handle)
            with self.failing_settle(1):
                reply = self.post("resume", {"job": handle})
            self.assertEqual(409, reply.status_code, reply.get_json())
            self.assertIn("Could not settle the last chunk", reply.get_json()["error"])
            self.assertEqual(before, bulk_edit.read_state(self.library, handle), "the record is exactly as it was")
            seen = self.status(handle)
            self.assertEqual(("abandoned", True), (seen["state"], seen["resumable"]))
            self.assertEqual(0, self.vl.rows("SELECT COUNT(*) FROM job_runs WHERE outcome = 'running'")[0][0],
                             "the claim of the failed resume was given back")
            self.assertEqual([], [job for job in bulk_edits._held(self.library).values() if job.state == bulk_edits.RUNNING])


class ResumeThatCannotSettle(Crashing):
    def test_the_reviewers_scenario_a_failed_resume_does_not_forget_the_chunk_in_flight(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        self.resume_that_cannot_settle(handle, 1)
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        self.assertEqual("done", self.finish(handle)["state"])
        self.assert_every_photo_shifted_once()

    def test_the_state_carries_the_chunk_in_flight_into_a_resumed_job(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        before = bulk_edit.read_state(self.library, handle)
        self.assertEqual((25, 50), (before["done"], before["inflight"]))
        job = bulk_edits.Job(self.library, handle, 99, bulk_edit.Edit.from_json(before["edit"]), [1], "exiftool", None, before)
        self.assertEqual(50, job.inflight)


def _case(crash, number, failures):
    def test(self):
        if crash == "write":
            self.die_at_write(number)
            handle = self.run_until_crash()
        elif crash == "file":
            with self.die_after_file(number):
                handle = self.run_until_crash()
        else:
            self.die_reading(self.ids[number])
            handle = self.run_until_crash()
        self.resume_that_cannot_settle(handle, failures)
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        done = self.finish(handle)
        self.assertEqual(("done", self.PHOTOS, 0), (done["state"], done["done"], done["error_count"]))
        self.assert_every_photo_shifted_once()
    return test


class EveryCrashEveryFailure(Crashing):
    """A crash at each kind of place in a chunk, then a resume whose settle fails 0, 1 and 2 times, then a resume that works:
    every photo is shifted exactly once and every date is taken + the shift."""


for _kind, _numbers in (("write", (26, 30, 38, 50)), ("file", (1, 12, 30)), ("read", (30, 40, 49))):
    for _number, _failures in itertools.product(_numbers, (0, 1, 2)):
        setattr(EveryCrashEveryFailure, "test_crash_%s_%d_then_%d_failed_resumes" % (_kind, _number, _failures),
                _case(_kind, _number, _failures))


class TwoProcessesOneRecord(Crashing):
    PHOTOS = 125

    def test_the_reviewers_scenario_a_resume_in_the_stale_process_shifts_nothing_twice(self):
        # Process A: a shift cancelled during chunk 2.
        gate = T.Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.shift_job(90)["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": handle})
        gate.release.set()
        a = self.finish(handle)
        self.assertEqual(("cancelled", 50), (a["state"], a["done"]))
        job_in_a = bulk_edits._held(self.library)[handle]
        # Process B shares the state file and the journal, has no memory of the job, resumes it and cancels it in chunk 4.
        bulk_edits._jobs.clear()
        gate = T.Gate(80)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": handle})
        gate.release.set()
        b = self.finish(handle)
        self.assertEqual(("cancelled", 100), (b["state"], b["done"]))
        self.files.on_write = None
        # Back in A, whose memory says 50: its status is the file's, and its Resume carries on from 100.
        bulk_edits._jobs.clear()
        bulk_edits._held(self.library)[handle] = job_in_a
        self.assertEqual(100, self.status(handle)["done"], "the record that moved on is the file's, not A's memory")
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        done = self.finish(handle)
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS), (done["state"], done["done"], done["changed"]))
        self.assert_every_photo_shifted_once()

    def test_a_resume_that_finds_the_record_moved_meanwhile_refuses_and_starts_nothing(self):
        self.die_at_write(30)
        handle = self.run_until_crash()
        real = bulk_edits._settle_flight

        def another_process_moves_it(library, job, state):
            real(library, job, state)
            bulk_edit.write_state(library, handle, dict(state, seq=int(state["seq"]) + 5))
        with mock.patch.object(bulk_edits, "_settle_flight", another_process_moves_it):
            reply = self.post("resume", {"job": handle})
        self.assertEqual(409, reply.status_code)
        self.assertIn("moved this job on", reply.get_json()["error"])
        self.assertEqual([], [job for job in bulk_edits._held(self.library).values() if job.state == bulk_edits.RUNNING])
        self.assertEqual(0, self.vl.rows("SELECT COUNT(*) FROM job_runs WHERE outcome = 'running'")[0][0])
        # Looked at again, it resumes from the record as it now is.
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        self.assertEqual("done", self.finish(handle)["state"])
        self.assert_every_photo_shifted_once()

    def test_every_write_of_the_state_moves_its_sequence_number_on(self):
        handle = self.shift_job(5)["job"]
        job = bulk_edits._held(self.library)[handle]
        job.thread.join(60)
        first = job.seq
        job._persist()
        self.assertGreater(job.seq, first)


class TheJournalsQuestion(T.Bulk):
    def test_what_a_job_wrote_is_asked_from_the_changes_not_by_scanning_every_file_row(self):
        conn = self.vl.conn
        conn.executemany("INSERT INTO changes (operation, status, schema_version, created, summary) VALUES (?, 'applied', 20, ?, '{}')",
                         [("bulk time shift (job %d)" % (n % 3 + 1), "2026-10-03 10:00:00") for n in range(2700)])
        rows = [(change_id, change_id * 100 + n) for (change_id,) in conn.execute("SELECT id FROM changes").fetchall()
                for n in range(25)]
        conn.executemany("INSERT INTO change_files (change_id, photo_id, path, fields_before, fields_after, state)"
                         " VALUES (?, ?, 'p', '{}', '{}', 'done')", rows)
        conn.commit()
        seen = []
        real = db.connect

        def watching(*args, **kwargs):
            found = real(*args, **kwargs)
            found.set_trace_callback(seen.append)
            return found
        with mock.patch.object(file_journal.db, "connect", watching):
            found = file_journal.photo_ids_done(self.vl.path, "bulk time shift (job 1)")
        self.assertEqual(900 * 25, len(found))
        # One statement, driven from `changes`: its plan says so, where a time limit measured the machine (#721).
        selects = [text for text in seen if "f.state = 'done'" in text]
        self.assertEqual(1, len(selects))
        plan = [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + selects[0])]
        self.assertFalse([line for line in plan if line.startswith("SCAN f")], plan)
        self.assertTrue([line for line in plan if "idx_change_files_change" in line], plan)


class Bounds(T.Bulk):
    def test_a_shift_out_of_range_is_that_photos_error_with_a_sentence_and_the_others_move(self):
        edge = self.path(self.ids[6])
        self.files.fields[paths.key(edge)]["EXIF:DateTimeOriginal"] = "9999:12:31 23:30:00"
        done = self.finish(self.shift_job(90)["job"])
        self.assertEqual((1, 0, self.PHOTOS - 1), (done["error_count"], done["unchanged"], done["changed"]))
        self.assertEqual(self.ids[6], done["errors"][0]["id"])
        self.assertIn("after the year 9999", done["errors"][0]["why"])
        self.assertEqual("9999:12:31 23:30:00", self.files.taken_of(edge), "not written")
        early = self.path(self.ids[7])
        self.files.fields[paths.key(early)]["EXIF:DateTimeOriginal"] = "0001:01:01 00:10:00"
        done = self.finish(self.shift_job(-90)["job"])
        self.assertIn("before the year 1", done["errors"][0]["why"])

    def test_a_shift_of_more_than_a_hundred_years_is_refused_up_front_with_a_sentence(self):
        for minutes in (52_560_001, -52_560_001, 10 ** 12):
            reply = self.start("time_shift", params={"minutes": minutes}, expect=400)
            self.assertIn("between", reply["error"])
        self.assertTrue(validation.problem("time shift", 52_560_001))
        self.assertIsNone(validation.problem("time shift", 52_560_000))
        self.assertEqual(0, self.files.writes)

    def test_the_folder_shift_still_leaves_such_a_photo_alone(self):
        from tagpup.core import dates
        self.assertIsNone(dates.shifted("9999:12:31 23:30:00", 90))
        with self.assertRaises(dates.ShiftOutOfRange):
            dates.shifted_strictly("9999:12:31 23:30:00", 90)

    def test_a_slow_claim_blocks_neither_a_status_nor_a_cancel(self):
        release = threading.Event()
        entered = threading.Event()
        real = bulk_edits.runs_service.claim

        def slow(*args, **kwargs):
            entered.set()
            release.wait(30)
            return real(*args, **kwargs)
        self.addCleanup(release.set)
        outcome = []
        with mock.patch.object(bulk_edits.runs_service, "claim", slow):
            starter = threading.Thread(target=lambda: outcome.append(bulk_edits.start(
                self.library, bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]}), self.ids, "exiftool")))
            starter.start()
            self.assertTrue(entered.wait(30))
            asked = []
            other = threading.Thread(target=lambda: asked.append((bulk_edits.status(self.library, 777),
                                                                  bulk_edits.running(self.library))))
            other.start()
            other.join(10)
            self.assertFalse(other.is_alive(), "a status waited for the claim")
            self.assertEqual([(None, 0)], asked)
            release.set()
            starter.join(60)
        bulk_edits._held(self.library)[outcome[0].handle].thread.join(60)

    def test_a_job_that_is_over_keeps_nothing_but_one_that_can_be_resumed_keeps_its_record(self):
        for op, params in (("tags", {"add": ["Trips/Coast"]}), ("people", {"add": ["Rowan Thackeray"]}), ("time_shift", {"minutes": 5})):
            handle = self.finish(self.start(op, params=params)["job"])["job"]
            self.assertEqual([], os.listdir(self.library.bulk_jobs), op + " done")
            self.assertEqual("done", self.status(handle)["state"])
        gate = T.Gate(self.files.writes + 2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        cancelled = self.tags_job(add=["Trips/Lakes"])["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": cancelled})
        gate.release.set()
        self.finish(cancelled)
        self.assertEqual([], os.listdir(self.library.bulk_jobs), "a cancelled tags job is started again, not resumed")
        self.files.on_write = None
        gate = T.Gate(self.files.writes + 2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        shift = self.shift_job(7)["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": shift})
        gate.release.set()
        self.finish(shift)
        self.assertEqual(sorted(["%d.ids" % shift, "%d.state.json" % shift]), sorted(os.listdir(self.library.bulk_jobs)))
        self.files.on_write = None
        self.assertEqual(200, self.post("resume", {"job": shift}).status_code)
        self.finish(shift)
        self.assertEqual([], os.listdir(self.library.bulk_jobs))

    def test_records_older_than_a_week_are_swept_at_the_start_of_a_job_and_younger_ones_stay(self):
        os.makedirs(self.library.bulk_jobs, exist_ok=True)
        old, young = (os.path.join(self.library.bulk_jobs, name) for name in ("91.state.json", "92.state.json"))
        for path, days in ((old, 8), (young, 6)):
            with open(path, "w") as handle:
                handle.write("{}")
            then = time.time() - days * 86400
            os.utime(path, (then, then))
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual((False, True), (os.path.exists(old), os.path.exists(young)))

    def test_no_exception_text_is_written_to_the_library_or_the_state_and_an_error_is_capped(self):
        secret = "C:\\Users\\Someone\\Pictures\\Private Folder\\clip.jpg is locked by a program"
        self.files.cannot_start = FileNotFoundError(secret)
        handle = self.finish(self.shift_job(5)["job"])["job"]
        seen = self.status(handle)
        run = self.vl.rows("SELECT note, changed FROM job_runs WHERE id = ?", handle)[0]
        state = bulk_edit.read_state(self.library, handle)
        for kept in (seen["message"], run[0], run[1], str(state)):
            self.assertNotIn("Private Folder", kept)
        self.assertIn("FileNotFoundError", seen["message"])
        self.assertIn("FileNotFoundError", run[0])
        self.files.cannot_start = None
        target = self.path(self.ids[3])

        def fails(path, _n):
            if paths.key(path) == paths.key(target):
                raise RuntimeError("y" * 500)
        self.files.on_write = fails
        done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(200, len(done["errors"][0]["why"]))
        self.assertLessEqual(len(done["errors"][0]["name"]), 200)


class AStalledExifTool(T.Bulk):
    def stall(self, read=False, write_at=None):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s (a read); its process was killed.")
        if read:
            def hook(_path, _n):
                raise stalled
            self.files.on_read = hook
        if write_at is not None:
            def writing(_path, n):
                if n == write_at:
                    raise stalled
            self.files.on_write = writing

    def order(self):
        return self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]

    def test_read_does_not_retry_photo_by_photo_after_a_timeout(self):
        calls = []

        class Stalled:
            timeout = 60

            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer")
        found = field_values.read(Stalled(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(1, len(calls))
        self.assertEqual(3, len(found))
        self.assertTrue(all(isinstance(each, field_values.Unreadable) and "did not answer in 60 s" in str(each) for each in found.values()))

    def test_read_still_retries_singly_a_batch_one_bad_path_made_exiftool_refuse(self):
        calls = []

        class Refuses:
            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                if len(batch) > 1:
                    raise RuntimeError("one of them is no file")
                return [{"SourceFile": batch[0], "File:MIMEType": "image/jpeg"}]
        found = field_values.read(Refuses(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(4, len(calls))
        self.assertFalse(any(isinstance(each, Exception) for each in found.values()))

    def test_a_chunk_gets_its_own_session_with_a_one_minute_deadline(self):
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertTrue(self.files.sessions)
        self.assertEqual({60}, {each.get("timeout") for each in self.files.sessions if "timeout" in each})
        self.assertEqual(3, len([each for each in self.files.sessions if each.get("timeout") == 60]), "one for each chunk")

    def test_a_read_that_times_out_costs_a_chunk_one_timeout_writes_nothing_and_the_rule_stops_the_job(self):
        self.stall(read=True)
        with mock.patch.object(bulk_edits, "CHUNK", 5):
            done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(("failed", 25, 25, 0), (done["state"], done["done"], done["error_count"], done["changed"]))
        self.assertEqual(5, self.files.read_calls, "one command for each chunk, not one for each photo")
        self.assertEqual(0, self.files.writes)
        self.assertIn("did not answer", done["errors"][0]["why"])
        self.assertTrue(file_changes._one_at_a_time.acquire(blocking=False), "the lock is released")
        file_changes._one_at_a_time.release()

    def test_a_write_that_times_out_leaves_the_rest_of_the_chunk_unwritten_and_the_next_chunk_goes_on(self):
        first = self.order()[:25]
        self.stall(write_at=3)
        done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(("done", 23), (done["state"], done["error_count"]))
        self.assertEqual(23, len([photo_id for photo_id in first if "Trips/Coast" not in self.tags(photo_id)]),
                         "two written, the third timed out, the others were not tried")
        self.assertEqual(3 + 35, self.files.writes, "after the stall the chunk began no write: the next chunks wrote their 35")
        for photo_id in self.order()[25:]:
            self.assertIn("Trips/Coast", self.tags(photo_id))

    def test_a_cancel_after_a_stalled_chunk_is_honoured_at_once(self):
        reached, release = threading.Event(), threading.Event()
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")

        def hook(_path, _n):
            reached.set()
            release.wait(30)
            raise stalled
        self.files.on_read = hook
        self.addCleanup(release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(reached.wait(30))
        self.post("cancel", {"job": handle})
        release.set()
        done = self.finish(handle)
        self.assertEqual(("cancelled", 25, 1), (done["state"], done["done"], self.files.read_calls))


if __name__ == "__main__":
    unittest.main()

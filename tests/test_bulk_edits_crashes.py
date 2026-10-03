"""The 9d-1 third follow-up review's findings #595, #596 and #598. A time shift is not safe to repeat, and a resume decides what the
chunk in flight wrote from the journal alone, so the journal must either have finished that chunk or the resume must say it could
not. The crashes here are HARD: the process is gone with its change still owned (by a dead process, or by this live one that
could not release it), and a settle that really cannot finish (`_finish` raising inside the real settle, not `settle` replaced).
Scenarios are the reviewer's. Fictional names only.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402
import test_bulk_edits_followup as F  # noqa: E402

from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402


class HardCrash(F.Followup):
    def hard_crash(self):
        """The process is gone: its run is owned by a process that ended, and so is the change it was carrying out."""
        self.crash()
        self.vl.conn.execute("UPDATE changes SET owner = ? WHERE owner = ?", (T.GHOST, file_journal.owner()))
        self.vl.conn.commit()

    def run_until_hard_crash(self, minutes=90):
        """A job whose process dies: nothing is released and the end is never recorded."""
        with mock.patch.object(file_journal, "release", lambda *a, **k: None):
            with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
                handle = self.shift_job(minutes)["job"]
                bulk_edits._held(self.library)[handle].thread.join(60)
        self.hard_crash()
        self.files.on_write = self.files.on_read = None
        return handle

    def crash_after_file_written(self, number):
        seen, real = [], file_changes._reached

        def die(step):
            real(step)
            if step == "file written":
                seen.append(1)
                if len(seen) == number:
                    raise T.Crash()
        with mock.patch.object(file_changes, "_reached", die):
            return self.run_until_hard_crash()

    def finish_failing(self, times):
        """The real settle, with `_finish` -- one change's own finishing -- raising its first `times` calls (the library busy)."""
        real, calls = file_changes._finish, []

        def finish(*args, **kwargs):
            calls.append(1)
            if len(calls) <= times:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)
        return mock.patch.object(file_changes, "_finish", finish)

    def unfinished_rows(self):
        return self.vl.rows("SELECT COUNT(*) FROM change_files WHERE state IN ('planned', 'writing')")[0][0]


class AChunkTheJournalHasNotFinished(HardCrash):
    def test_a_change_left_by_a_dead_process_is_finished_by_the_resume_and_nothing_is_shifted_twice(self):
        handle = self.crash_after_file_written(30)
        self.assertGreater(self.unfinished_rows(), 0)
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_change_left_owned_by_this_live_process_is_finished_by_the_resume_too(self):
        real, calls = file_changes._record, []

        def record(*args, **kwargs):
            calls.append(1)
            if len(calls) == 30:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)
        with mock.patch.object(file_changes, "_record", record):
            with mock.patch.object(file_journal, "release", lambda *a, **k: None):
                handle = self.shift_job(90)["job"]
                failed = self.finish(handle)
        self.assertEqual("failed", failed["state"])
        owners = self.vl.rows("SELECT owner FROM changes WHERE status = 'planned'")
        self.assertEqual([(file_journal.owner(),)], owners, "the change is still owned by this live process")
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_settle_that_cannot_finish_once_refuses_the_resume_and_the_next_one_finishes_it(self):
        handle = self.crash_after_file_written(30)
        before = bulk_edit.read_state(self.library, handle)
        writes = self.files.writes
        with self.finish_failing(1):
            reply = self.post("resume", {"job": handle})
        self.assertEqual(409, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("Could not settle", reply.get_json()["error"])
        self.assertEqual(before, bulk_edit.read_state(self.library, handle), "the record is as it was")
        self.assertEqual(writes, self.files.writes, "nothing was written")
        self.assertEqual(0, len(self.vl.rows("SELECT 1 FROM job_runs WHERE outcome = 'running'")), "the claim was given back")
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_settle_that_never_finishes_refuses_every_resume_and_writes_nothing(self):
        handle = self.crash_after_file_written(30)
        writes = self.files.writes
        with self.finish_failing(10 ** 6):
            for _ in range(3):
                self.assertEqual(409, self.post("resume", {"job": handle}).status_code)
        self.assertEqual(writes, self.files.writes)
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_live_owned_change_whose_settle_cannot_finish_is_refused_not_shifted_again(self):
        real, calls = file_changes._record, []

        def record(*args, **kwargs):
            calls.append(1)
            if len(calls) == 30:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)
        with mock.patch.object(file_changes, "_record", record):
            with mock.patch.object(file_journal, "release", lambda *a, **k: None):
                handle = self.finish(self.shift_job(90)["job"])["job"]
        writes = self.files.writes
        with self.finish_failing(10 ** 6):
            self.assertEqual(409, self.post("resume", {"job": handle}).status_code)
        self.assertEqual(writes, self.files.writes)
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()


class ARestoredJournal(HardCrash):
    def forget_the_change_in_flight(self, handle):
        """What a snapshot restore does: the journal no longer holds the change of the chunk that was being written."""
        state = bulk_edit.read_state(self.library, handle)
        self.assertGreater(state["inflight"], state["done"])
        newest = self.vl.rows("SELECT MAX(id) FROM changes")[0][0]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("DELETE FROM change_files WHERE change_id = ?", (newest,)),
            conn.execute("DELETE FROM changes WHERE id = ?", (newest,))])

    def test_a_resume_after_the_journal_lost_the_chunk_in_flight_is_refused_and_writes_nothing(self):
        handle = self.crash_after_file_written(30)
        self.forget_the_change_in_flight(handle)
        writes, before = self.files.writes, bulk_edit.read_state(self.library, handle)
        reply = self.post("resume", {"job": handle})
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("journal", reply.get_json()["error"])
        self.assertEqual(writes, self.files.writes)
        self.assertEqual(before, bulk_edit.read_state(self.library, handle))
        self.assertEqual(0, len(self.vl.rows("SELECT 1 FROM job_runs WHERE outcome = 'running'")))

    def test_the_state_carries_the_journals_highest_change_of_the_job(self):
        handle = self.crash_after_file_written(30)
        newest = self.vl.rows("SELECT MAX(id) FROM changes")[0][0]
        self.assertEqual(newest, bulk_edit.read_state(self.library, handle)["journal_high"])

    def test_a_chunk_that_died_before_its_plan_resumes_though_it_has_no_change(self):
        real, calls = bulk_edit._shift, []

        def shift(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:                 # the first chunk is written; the second dies before it is planned
                raise T.Crash()
            return real(*args, **kwargs)
        with mock.patch.object(bulk_edit, "_shift", shift):
            handle = self.run_until_hard_crash()
        state = bulk_edit.read_state(self.library, handle)
        self.assertEqual((25, 50), (state["done"], state["inflight"]))
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()


if __name__ == "__main__":
    unittest.main()

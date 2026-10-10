"""The 9d-1 third follow-up review's findings #595, #596 and #598. A time shift is not safe to repeat, and a resume decides what the
chunk in flight wrote from the journal alone, so the journal must either have finished that chunk or the resume must say it could
not. The crashes here are HARD: the process is gone with its change still owned (by a dead process, or by this live one that
could not release it), and a settle that really cannot finish (`_finish` raising inside the real settle, not `settle` replaced).
Scenarios are the reviewer's. Fictional names only.
"""
import os
import socket
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402
import test_bulk_edits_progress_record as F  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
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

    def crash_with_chunk_two_applied(self):
        real, calls = bulk_edits.Job._take, []

        def take(self_, out, size):
            calls.append(1)
            if len(calls) == 2:          # chunk 2's change is applied; the process dies before the job records it done
                raise T.Crash()
            return real(self_, out, size)
        with mock.patch.object(bulk_edits.Job, "_take", take):
            return self.run_until_hard_crash()

    def unfinished_rows(self):
        return self.vl.rows("SELECT COUNT(*) FROM change_files WHERE state IN ('planned', 'writing')")[0][0]


class AChunkTheJournalHasNotFinished(HardCrash):
    def test_guard_a_change_left_by_a_dead_process_is_finished_by_the_resume_and_nothing_is_shifted_twice(self):
        # A guard, not a regression test: the old code passes it too (a dead owner was always taken over).
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

    def test_the_state_carries_the_change_of_the_chunk_in_flight(self):
        handle = self.crash_after_file_written(30)
        newest = self.vl.rows("SELECT MAX(id) FROM changes")[0][0]
        self.assertEqual(newest, bulk_edit.read_state(self.library, handle)["journal_chunk"])

    def test_a_resume_after_the_journal_lost_an_older_change_though_it_holds_newer_ones_is_refused(self):
        handle = self.crash_after_file_written(30)
        high = bulk_edit.read_state(self.library, handle)["journal_chunk"]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("DELETE FROM change_files WHERE change_id = ?", (high,)),
            conn.execute("DELETE FROM changes WHERE id = ?", (high,)),
            conn.execute("INSERT INTO changes (id, operation, status, schema_version, created, summary)"
                         " VALUES (?, 'some other change', 'applied', 1, '2026-10-03 12:00:00', '{}')", (high + 50,))])
        writes = self.files.writes
        reply = self.post("resume", {"job": handle})
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual(writes, self.files.writes)

    def test_no_prune_takes_the_changes_of_a_job_that_can_be_resumed_and_says_so(self):
        handle = self.crash_with_chunk_two_applied()
        others = self.vl.rows("SELECT COUNT(*) FROM changes WHERE operation NOT LIKE 'bulk time shift%'")[0][0]
        dry = journal_service.prune(self.library, 0)
        self.assertEqual(others, dry.attempted)
        self.assertEqual({bulk_edit.operation_of("time_shift", handle)}, journal_service.kept_operations(self.library))
        self.assertEqual(2, dry.details["kept"])
        self.assertEqual([int(handle)], [int(each) for each in dry.details["kept_jobs"]])
        self.assertIn("kept for a resumable bulk job: 2", dry.details["note"])
        applied = journal_service.prune(self.library, 0, apply=True)
        self.assertEqual(others, applied.changed)
        self.assertEqual(0, self.vl.rows(
            "SELECT COUNT(*) FROM changes WHERE status = 'pruned' AND operation LIKE 'bulk time shift%'")[0][0])
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()
        self.assertEqual(3, journal_service.prune(self.library, 0, apply=True).changed, "once the job is done its changes go too")

    def test_guard_a_change_pruned_some_other_way_is_refused_saying_so_and_writes_nothing(self):
        # A defence: no prune reaches it any more (the test above); here the rows are edited as a journal changed otherwise would be.
        handle = self.crash_with_chunk_two_applied()
        chunk = bulk_edit.read_state(self.library, handle)["journal_chunk"]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("DELETE FROM change_files WHERE change_id = ?", (chunk,)),
            conn.execute("UPDATE changes SET status = 'pruned' WHERE id = ?", (chunk,))])
        writes = self.files.writes
        reply = self.post("resume", {"job": handle})
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("pruned", reply.get_json()["error"])
        self.assertEqual(writes, self.files.writes)

    def test_a_snapshot_restored_below_the_record_with_other_operations_reusing_the_ids_is_refused(self):
        handle = self.crash_after_file_written(55)
        chunk = bulk_edit.read_state(self.library, handle)["journal_chunk"]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("DELETE FROM change_files WHERE change_id >= ?", (chunk,)),
            conn.execute("DELETE FROM changes WHERE id >= ?", (chunk,))])
        for taken in range(chunk, chunk + 3):    # work done since the restore reuses the ids, under other names
            db.write_with_connection(self.library.path, lambda conn, taken=taken: conn.execute(
                "INSERT INTO changes (id, operation, status, schema_version, created, summary)"
                " VALUES (?, 'refresh_rows', 'applied', 1, '2026-10-03 12:00:00', '{}')", (taken,)))
        self.assertGreaterEqual(file_journal.highest(self.library.path), chunk)
        writes = self.files.writes
        reply = self.post("resume", {"job": handle})
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("no longer holds", reply.get_json()["error"])
        self.assertEqual(writes, self.files.writes)

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
        self.assertIsNone(state["journal_chunk"], "cleared at the write before the chunk: the previous chunk's change is not its")
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_the_previous_chunks_change_removed_does_not_stop_a_resume_of_a_chunk_that_died_before_its_plan(self):
        real, calls = bulk_edit._shift, []

        def shift(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise T.Crash()
            return real(*args, **kwargs)
        with mock.patch.object(bulk_edit, "_shift", shift):
            handle = self.run_until_hard_crash()
        first = self.vl.rows("SELECT MIN(id) FROM changes WHERE operation = ?", bulk_edit.operation_of("time_shift", handle))[0][0]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("DELETE FROM change_files WHERE change_id = ?", (first,)),
            conn.execute("UPDATE changes SET status = 'pruned' WHERE id = ?", (first,))])
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_a_record_that_cannot_name_the_planned_change_fails_the_job_and_the_end_records_it(self):
        real, failed = bulk_edit.write_state, []

        def flaky(library, job, state):
            if state.get("inflight") == 50 and state.get("journal_chunk") is not None and len(failed) < 5:
                failed.append(1)
                raise PermissionError(5, "Access is denied")
            return real(library, job, state)
        with mock.patch.object(bulk_edit, "write_state", flaky):
            handle = self.shift_job(90)["job"]
            failed_job = self.finish(handle)
        self.assertEqual("failed", failed_job["state"])
        newest = self.vl.rows("SELECT MAX(id) FROM changes")[0][0]
        self.assertEqual(newest, bulk_edit.read_state(self.library, handle)["journal_chunk"])
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_the_resume_says_a_chunk_you_undid_is_shifted_again(self):
        handle = self.crash_with_chunk_two_applied()
        reply = self.post("resume", {"job": handle})
        self.assertEqual(200, reply.status_code)
        self.assertIn("A chunk you undid is shifted again", reply.get_json()["message"])
        self.finish(handle)

    def test_the_prune_asks_what_to_keep_inside_its_transaction(self):
        handle = self.crash_with_chunk_two_applied()
        operation = bulk_edit.operation_of("time_shift", handle)
        real, calls = journal_service.kept_operations, []

        def late(library):
            calls.append(1)
            return set() if len(calls) == 1 else real(library)     # a job began between the first look and the write
        with mock.patch.object(journal_service, "kept_operations", late):
            journal_service.prune(self.library, 0, apply=True)
        self.assertEqual(0, self.vl.rows("SELECT COUNT(*) FROM changes WHERE status = 'pruned' AND operation = ?", operation)[0][0])

    def test_a_sync_applys_prune_leaves_a_record_older_than_ninety_days_its_changes(self):
        from tagpup.services import maintenance
        from tagpup.store import journal
        handle = self.crash_with_chunk_two_applied()
        operation = bulk_edit.operation_of("time_shift", handle)
        db.write_with_connection(self.library.path, lambda conn: conn.execute(
            "UPDATE changes SET created = '2020-01-01 00:00:00'"))
        photo_id = self.ids[0]
        caption = self.vl.rows("SELECT captions FROM photos WHERE id = ?", photo_id)[0][0]

        def plan(library):
            return maintenance.Plan(size=1, counts={"things": 1}, ids={"things": [photo_id]}, work=[photo_id])

        def edits(planned):
            return [journal.update("photos", (photo_id,), {"captions": caption}, {"captions": '["Harbour"]'})]
        self.assertTrue(maintenance.run(self.library, "test_op", plan, edits, apply=True).changed)
        self.assertEqual(0, self.vl.rows("SELECT COUNT(*) FROM changes WHERE status = 'pruned' AND operation = ?", operation)[0][0])
        self.assertGreater(self.vl.rows("SELECT COUNT(*) FROM change_files")[0][0], 0)
        self.resume_and_finish(handle)
        self.assert_every_photo_shifted_once()

    def test_prune_journal_on_the_command_line_says_what_it_kept(self):
        import tagpup_cli
        from click.testing import CliRunner
        self.crash_with_chunk_two_applied()
        for flags in ([], ["--apply"]):
            with mock.patch.object(tagpup_cli, "_existing_library", lambda ctx: self.library):
                out = CliRunner().invoke(tagpup_cli.prune_journal, ["--days", "0"] + flags)
            self.assertEqual(0, out.exit_code, out.output)
            self.assertIn("kept for a resumable bulk job: 2", out.output)


class AnUndoIsNotAResumesToFinish(HardCrash):
    def test_a_chunk_being_undone_by_another_live_process_is_unsettled_and_the_resume_is_refused(self):
        handle = self.crash_with_chunk_two_applied()
        second = self.vl.rows("SELECT id FROM changes WHERE operation = ? ORDER BY id", bulk_edit.operation_of("time_shift", handle))[1][0]
        parent = os.getppid()
        other = "%s:%d:%s" % (socket.gethostname(), parent, processes.started(parent))
        undone = [row[0] for row in self.vl.rows("SELECT id FROM change_files WHERE change_id = ? ORDER BY id LIMIT 10", second)]
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("UPDATE changes SET status = 'planned', undone = ?, owner = ? WHERE id = ?",
                         ("2026-10-03 12:00:00", other, second)),
            conn.execute("UPDATE change_files SET state = 'undone' WHERE id IN (%s)" % ",".join("?" * len(undone)), undone)])
        self.assertEqual([("done", 15), ("undone", 10)], self.vl.rows(
            "SELECT state, COUNT(*) FROM change_files WHERE change_id = ? GROUP BY state ORDER BY state", second))
        writes = self.files.writes
        reply = self.post("resume", {"job": handle})
        self.assertEqual(409, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("Could not settle", reply.get_json()["error"])
        self.assertEqual(writes, self.files.writes)

    def test_a_resume_leaves_an_undo_another_live_process_is_carrying_out_alone(self):
        handle = self.crash_after_file_written(55)
        first = self.vl.rows("SELECT MIN(id) FROM changes WHERE operation = ?", bulk_edit.operation_of("time_shift", handle))[0][0]
        parent = os.getppid()
        other = "%s:%d:%s" % (socket.gethostname(), parent, processes.started(parent))
        self.assertTrue(file_journal.owner_alive(other))
        db.write_with_connection(self.library.path, lambda conn: [
            conn.execute("UPDATE changes SET status = 'planned', undone = ?, owner = ? WHERE id = ?",
                         ("2026-10-03 12:00:00", other, first)),
            conn.execute("UPDATE change_files SET state = 'writing' WHERE id IN"
                         " (SELECT id FROM change_files WHERE change_id = ? LIMIT 3)", (first,))])
        self.post("resume", {"job": handle})
        self.assertEqual([("planned", other)], self.vl.rows("SELECT status, owner FROM changes WHERE id = ?", first))
        self.assertEqual([("writing", 3)], self.vl.rows(
            "SELECT state, COUNT(*) FROM change_files WHERE change_id = ? AND state = 'writing' GROUP BY state", first))
        self.finish(handle)


if __name__ == "__main__":
    unittest.main()

"""A run nests under the run that queued it and says how far it got (D9; DECISIONS 2026-10-28, "ACTIVITY REWORK";
docs/PLAN_ACTIVITY.md, B2; tagpup.services.job_runs, tagpup.store.job_runs, tagpup.core.runs.parent_run).

A run claimed while a run of the same library is under way on the thread is its child (`job_runs.parent`); a run of
another library, or a sync, which names no row while it runs, is not a parent. A library at 35, which the owner's server
has not yet opened, is never written by a claim: it is behind, and the claim says so.

A run says how far it got (`services.job_runs.progress`): a phase and counts, never a path, at most once every 10 s unless
the phase changes, skipped -- not waited for -- while the library is being written, by this process or another; a run that
is gone, or a library a restore has put back at 35 and not yet migrated, is not written and nothing raises. Libraries are
made in homes of their own; the names are fictional.
"""
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402

from tagpup.core import runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.store import db, file_journal, job_runs, schema  # noqa: E402

NOW = 1790000000.0


def rows(db_path, sql, params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class ARunNestsUnderTheRunThatQueuedIt(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        self.other = Library(self.home.library("lighthouse.db"))
        for each in (self.library, self.other):
            schema.ensure(each.path)

    def parent_of(self, run_id):
        return rows(self.library.path, "SELECT parent FROM job_runs WHERE id = ?", (run_id,))[0][0]

    def test_a_run_claimed_under_a_run_of_its_library_is_its_child_and_the_innermost_wins(self):
        sync = runs_service.claim(self.library, "sync", self.library.name, NOW)
        with runs.running(runs.job_tag(self.library.name, sync.run_id)):
            index = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
            with runs.running(runs.job_tag(self.library.name, index.run_id)):
                bake = runs_service.claim(self.library, "bake", self.library.name, NOW + 2)
        after = runs_service.claim(self.library, "snapshots", self.library.name, NOW + 3)
        self.assertEqual((None, sync.run_id, index.run_id, None),
                         tuple(self.parent_of(each.run_id) for each in (sync, index, bake, after)))

    def test_a_run_of_another_library_or_a_sync_is_not_a_parent(self):
        theirs = runs_service.claim(self.other, "sync", self.other.name, NOW)
        with runs.running(runs.job_tag(self.other.name, theirs.run_id),
                          runs.sync_tag(self.library.name, job_runs.stamp(NOW))):
            mine = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        self.assertIsNone(self.parent_of(mine.run_id))

    def test_a_process_started_for_a_run_claims_under_it(self):
        """The CLI's index, started by the queue, is told its runs in its environment (runs.ENV)."""
        sync = runs_service.claim(self.library, "sync", self.library.name, NOW)
        told = runs.job_tag(self.library.name, sync.run_id)
        previous = os.environ.get(runs.ENV)
        os.environ[runs.ENV] = told
        try:
            index = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        finally:
            if previous is None:
                os.environ.pop(runs.ENV)
            else:
                os.environ[runs.ENV] = previous
        self.assertEqual(sync.run_id, self.parent_of(index.run_id))

    def test_two_runs_of_one_job_the_second_is_refused_while_the_first_runs(self):
        first = runs_service.claim(self.library, "index", self.library.name, NOW)
        second = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        self.assertTrue(first)
        self.assertEqual("running", second.why)
        self.assertEqual([(1,)], rows(self.library.path, "SELECT COUNT(*) FROM job_runs"))

    def test_a_library_at_35_is_not_written_by_a_claim(self):
        """No writer reaches a library the owner's server has not migrated: a claim finds it behind and writes nothing."""
        at_35 = Library(self.home.library("breakwater.db"))
        with migration_names.up_to(35):
            schema.ensure(at_35.path)
        schema.forget()
        claim = runs_service.claim(at_35, "index", at_35.name, NOW)
        self.assertEqual(("behind", None), (claim.why, claim.run_id))
        self.assertEqual([(35, 0)], rows(at_35.path, "SELECT (SELECT MAX(version) FROM schema_version),"
                                                     " (SELECT COUNT(*) FROM job_runs)"))


class ARunSaysHowFarItGot(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        schema.ensure(self.library.path)
        self.run_id = runs_service.claim(self.library, "index", self.library.name, NOW).run_id

    def said(self, library=None, run_id=None):
        heartbeat, progress = rows((library or self.library).path, "SELECT heartbeat, progress FROM job_runs WHERE id = ?",
                                   (run_id or self.run_id,))[0]
        return heartbeat, json.loads(progress) if progress else None

    def test_a_run_says_its_phase_and_counts_at_most_every_10_seconds_and_at_once_when_its_phase_changes(self):
        say = runs_service.progress
        self.assertTrue(say(self.library, self.run_id, {"folders_done": 1, "folders": 9}, "walking", now=NOW))
        self.assertFalse(say(self.library, self.run_id, {"folders_done": 2, "folders": 9}, "walking", now=NOW + 5))
        self.assertEqual((job_runs.stamp(NOW), {"phase": "walking", "counts": {"folders": 9, "folders_done": 1}}),
                         self.said())
        self.assertTrue(say(self.library, self.run_id, {"photos_done": 3}, "metadata", now=NOW + 6))
        self.assertEqual({"phase": "metadata", "counts": {"photos_done": 3}}, self.said()[1])
        self.assertTrue(say(self.library, self.run_id, {"photos_done": 40}, "metadata", now=NOW + 16))
        self.assertEqual((job_runs.stamp(NOW + 16), {"phase": "metadata", "counts": {"photos_done": 40}}), self.said())

    def test_a_path_or_a_text_in_the_counts_is_never_written(self):
        folder = os.path.join(self.home.root, "Pictures", "Harbour Day")
        self.assertTrue(runs_service.progress(self.library, self.run_id, {
            "folders_done": 2, "folder": folder, folder: 1, "Shown": 4, "share_away": True, "per_folder": {"a": 1},
            "seconds": 1.5}, "walking", now=NOW))
        text = rows(self.library.path, "SELECT progress FROM job_runs WHERE id = ?", (self.run_id,))[0][0]
        self.assertEqual({"phase": "walking", "counts": {"folders_done": 2, "seconds": 1.5}}, json.loads(text))
        self.assertNotIn("Harbour", text)

    def test_a_phase_not_in_the_vocabulary_is_a_mistake_in_the_code(self):
        with self.assertRaises(ValueError):
            runs_service.progress(self.library, self.run_id, {}, "C:/Pictures", now=NOW)

    def test_two_runs_with_one_id_in_two_libraries_each_keep_their_own_progress(self):
        other = Library(self.home.library("lighthouse.db"))
        schema.ensure(other.path)
        theirs = runs_service.claim(other, "index", other.name, NOW).run_id
        self.assertEqual(self.run_id, theirs)
        self.assertTrue(runs_service.progress(self.library, self.run_id, {"photos_done": 1}, "faces", now=NOW))
        self.assertTrue(runs_service.progress(other, theirs, {"photos_done": 7}, "faces", now=NOW + 1))
        self.assertEqual({"photos_done": 1}, self.said()[1]["counts"])
        self.assertEqual({"photos_done": 7}, self.said(other, theirs)[1]["counts"])

    def test_a_run_that_is_gone_or_ended_is_not_written_and_nothing_raises(self):
        self.assertTrue(runs_service.discard(self.library, self.run_id))
        self.assertFalse(runs_service.progress(self.library, self.run_id, {"photos_done": 1}, "bake", now=NOW))
        ended = runs_service.claim(self.library, "bake", self.library.name, NOW).run_id
        self.assertTrue(runs_service.end(self.library, ended, NOW + 1, {"written": 2}))
        self.assertFalse(runs_service.progress(self.library, ended, {"written": 3}, "bake", now=NOW + 2))
        self.assertEqual((None, None), self.said(run_id=ended))

    def test_while_another_thread_writes_the_library_progress_is_skipped_not_waited_for(self):
        held, release = threading.Event(), threading.Event()

        def write():
            with db.lock_for(self.library.path):
                held.set()
                release.wait(30)

        writer = threading.Thread(target=write)
        writer.start()
        try:
            self.assertTrue(held.wait(10))
            began = time.monotonic()
            self.assertFalse(runs_service.progress(self.library, self.run_id, {"photos_done": 1}, "faces", now=NOW))
            self.assertLess(time.monotonic() - began, 1.0)
        finally:
            release.set()
            writer.join()
        self.assertEqual((None, None), self.said())
        # Not written is not throttled: the next says it.
        self.assertTrue(runs_service.progress(self.library, self.run_id, {"photos_done": 2}, "faces", now=NOW + 1))

    def test_while_another_process_writes_the_library_progress_is_skipped_not_waited_for(self):
        """Another connection's open write transaction is what another process's write is to SQLite."""
        other = db.connect(self.library.path)
        try:
            db.begin(other, immediate=True)
            began = time.monotonic()
            self.assertFalse(runs_service.progress(self.library, self.run_id, {"photos_done": 1}, "faces", now=NOW))
            self.assertLess(time.monotonic() - began, 1.0)
        finally:
            other.rollback()
            other.close()
        self.assertEqual((None, None), self.said())

    def test_a_library_a_restore_has_put_back_at_35_is_not_written_and_nothing_raises(self):
        """snapshots.restore lets go of the write lock before it brings the snapshot to 36: a progress write in that gap
        meets the columns' absence."""
        at_35 = Library(self.home.library("breakwater.db"))
        with migration_names.up_to(35):
            schema.ensure(at_35.path)
        schema.forget()
        # A run of this process, as a claim at 35 stored it.
        conn = db.connect(at_35.path)
        try:
            run_id = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, ?, ?, 'running', ?)",
                                  ("index", at_35.name, job_runs.stamp(NOW), file_journal.owner())).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(runs_service.progress(at_35, run_id, {"photos_done": 1}, "faces", now=NOW))
        self.assertEqual([(35,)], rows(at_35.path, "SELECT MAX(version) FROM schema_version"))


if __name__ == "__main__":
    unittest.main()

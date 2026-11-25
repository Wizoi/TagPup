"""A run nests under the run that queued it and says how far it got (D9; DECISIONS 2026-10-28, "ACTIVITY REWORK";
docs/PLAN_ACTIVITY.md, B2; tagpup.services.job_runs, tagpup.store.job_runs, tagpup.core.runs.parent_run).

A run claimed while a run of the same library is under way on the thread is its child (`job_runs.parent`); a run of
another library, or a sync, which names no row while it runs, is not a parent. A library at 35, which the owner's server
has not yet opened, is never written by a claim: it is behind, and the claim says so.

A run says how far it got (`services.job_runs.progress`): a phase and counts, never a path, at most once every 10 s unless
the phase changes, skipped -- not waited for -- while the library is being written, by this process or another
(a change of phase alone waits a moment for it: a sync tells its last phase once); a run that
is gone, or a library a restore has put back at 35 and not yet migrated, is not written and nothing raises. Libraries are
made in homes of their own; the names are fictional.

The runs are read as trees (`services.job_runs.tree`): the newest top runs -- nothing queued them, or their parent is gone,
pruned or not in a snapshot a restore put back -- and every run under them; a library a restore put back at 35 reads as
its newest runs, each a top run.

A run left `running` by a process that has ended -- a server killed mid-run, a CLI run that died -- is marked abandoned
as the server starts (`libraries.bring_up_to_date_in_background`), with the one sentence that says what it means; a run
of a live process, or of another machine, is kept, and a failure of the sweep does not stop the start.
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402

from tagpup.core import processes, runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, file_journal, job_runs, schema, snapshots  # noqa: E402

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
        """A sync's; an index's is recorded beside (job_runs.RECORDS_ONLY; tests/test_an_index_is_one_run_under_its_sync.py)."""
        first = runs_service.claim(self.library, "sync", self.library.name, NOW)
        second = runs_service.claim(self.library, "sync", self.library.name, NOW + 1)
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
        # Not written waits its 10 s as a write does (#2196): the next line is not tried, the one 10 s on says it.
        self.assertFalse(runs_service.progress(self.library, self.run_id, {"photos_done": 2}, "faces", now=NOW + 1))
        self.assertTrue(runs_service.progress(self.library, self.run_id, {"photos_done": 3}, "faces",
                                              now=NOW + runs_service.PROGRESS_EVERY))

    def hold_the_library(self, seconds):
        """Another connection (what another process's write is to SQLite) holds the library's write for `seconds`;
        the thread is started once it does."""
        held = threading.Event()

        def write():
            conn = db.connect(self.library.path)
            try:
                db.begin(conn, immediate=True)
                held.set()
                time.sleep(seconds)   # wait: the scenario, another writer holds the library this long
                conn.rollback()
            finally:
                conn.close()

        holder = threading.Thread(target=write)
        holder.start()
        self.assertTrue(held.wait(10))
        self.addCleanup(holder.join)
        return holder

    def test_a_sync_s_one_tell_of_the_phase_after_the_walk_is_written_though_another_writer_holds_the_library_a_second(self):
        """#2213: a sync tells the end of its walk once (sync._Walked.ended); a write the library was busy for was lost and
        the row stayed at 'walking' for the rest of the sync."""
        from tagpup.services import sync as sync_service
        with runs_service.recording(self.library, self.run_id):
            walked = sync_service._Walked(sync_service.WALKING)
            self.assertEqual("walking", self.said()[1]["phase"])
            self.hold_the_library(1.0)
            walked.ended()
        self.assertEqual("after_the_walk", self.said()[1]["phase"])

    def test_a_phase_change_the_library_stays_busy_for_is_logged_once_and_not_tried_at_every_line(self):
        """#2214: a phase change waits at most the claim's two seconds; one not written is not tried again by the 100
        lines that follow it, which wait the 10 s of their phase as a write does (#2196)."""
        say = runs_service.progress
        self.assertTrue(say(self.library, self.run_id, {"folders": 9}, "walking", now=NOW))
        self.hold_the_library(job_runs.RECORD_WITHIN + 1.5)
        with mock.patch.object(job_runs, "progress", wraps=job_runs.progress) as store, \
                self.assertLogs("tagpup.services.job_runs", level="WARNING"):
            began = time.monotonic()
            self.assertFalse(say(self.library, self.run_id, {"folders": 9}, "after_the_walk", now=NOW + 1))
            self.assertLess(time.monotonic() - began, job_runs.RECORD_WITHIN + 1.0)
            for i in range(100):
                self.assertFalse(say(self.library, self.run_id, {"folders": 9}, "after_the_walk", now=NOW + 1 + i * 0.05))
        self.assertEqual(1, store.call_count)
        self.assertEqual("walking", self.said()[1]["phase"])

    def test_a_phase_change_for_a_run_that_is_gone_is_tried_once_not_at_every_line(self):
        say = runs_service.progress
        self.assertTrue(say(self.library, self.run_id, {"folders": 9}, "walking", now=NOW))
        # Taken away as a restore does, not ended by this process (which would forget the run).
        conn = db.connect(self.library.path)
        try:
            conn.execute("DELETE FROM job_runs WHERE id = ?", (self.run_id,))
            conn.commit()
        finally:
            conn.close()
        with mock.patch.object(job_runs, "progress", wraps=job_runs.progress) as store:
            for i in range(100):
                self.assertFalse(say(self.library, self.run_id, {"folders": 9}, "after_the_walk", now=NOW + 1 + i * 0.05))
        self.assertEqual(1, store.call_count)

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


class TheRunsAreReadAsTrees(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        schema.ensure(self.library.path)

    def claim(self, job, at, under=None):
        """A run of `job` claimed at `at`, under the run `under` as the queue's thread holds it."""
        if under is None:
            return runs_service.claim(self.library, job, self.library.name, at).run_id
        with runs.running(runs.job_tag(self.library.name, under)):
            return runs_service.claim(self.library, job, self.library.name, at).run_id

    def shape(self, limit=20):
        return [(run.id, run.job, run.parent) for run in runs_service.tree(self.library, limit)]

    def test_a_sync_its_index_and_the_index_s_bake_read_as_one_tree_with_their_progress(self):
        sync = self.claim("sync", NOW)
        index = self.claim("index", NOW + 1, under=sync)
        bake = self.claim("bake", NOW + 2, under=index)
        self.assertTrue(runs_service.progress(self.library, bake, {"folders_done": 2, "folders": 5}, "bake", now=NOW + 3))
        self.assertEqual([(bake, "bake", index), (index, "index", sync), (sync, "sync", None)], self.shape())
        said = runs_service.tree(self.library)[0]
        self.assertEqual((job_runs.stamp(NOW + 3), {"phase": "bake", "counts": {"folders": 5, "folders_done": 2}}),
                         (said.heartbeat, said.progress))

    def test_a_run_whose_parent_was_pruned_is_a_top_run(self):
        with mock.patch.object(job_runs, "KEEP_RUNS", 1):
            sync = self.claim("sync", NOW)
            index = self.claim("index", NOW + 1, under=sync)
            self.assertTrue(runs_service.end(self.library, sync, NOW + 2, {}))
            later = self.claim("sync", NOW + 3)
            self.assertTrue(runs_service.end(self.library, later, NOW + 4, {}))
        self.assertEqual([], rows(self.library.path, "SELECT id FROM job_runs WHERE id = ?", (sync,)))
        self.assertEqual([(later, "sync", None), (index, "index", sync)], self.shape())

    def test_two_trees_newest_first_and_the_limit_counts_top_runs_with_their_children(self):
        first = self.claim("sync", NOW)
        first_index = self.claim("index", NOW + 1, under=first)
        for each in (first_index, first):
            self.assertTrue(runs_service.end(self.library, each, NOW + 2, {}))
        second = self.claim("sync", NOW + 3)
        second_index = self.claim("index", NOW + 4, under=second)
        self.assertEqual([(second_index, "index", second), (second, "sync", None), (first_index, "index", first),
                          (first, "sync", None)], self.shape())
        self.assertEqual([(second_index, "index", second), (second, "sync", None)], self.shape(limit=1))
        self.assertEqual([], self.shape(limit=0))

    def test_after_a_restore_the_snapshot_s_tree_is_read_its_runs_under_way_abandoned(self):
        sync = self.claim("sync", NOW)
        index = self.claim("index", NOW + 1, under=sync)
        taken, _ = snapshots.take_one(self.library.path, snapshots.DAILY, NOW + 2)
        self.claim("snapshots", NOW + 3)
        snapshots.restore(self.library.path, taken.path)
        found = runs_service.tree(self.library)
        self.assertEqual([(index, "index", sync), (sync, "sync", None)], [(r.id, r.job, r.parent) for r in found])
        self.assertEqual({job_runs.ABANDONED}, {run.outcome for run in found})

    def test_a_library_at_35_reads_as_its_newest_runs_each_a_top_run(self):
        """The Activity page's look reads a library the server has not migrated: nothing raises."""
        at_35 = Library(self.home.library("breakwater.db"))
        with migration_names.up_to(35):
            schema.ensure(at_35.path)
        schema.forget()
        conn = db.connect(at_35.path)
        try:
            for job in ("sync", "index", "bake"):
                conn.execute("INSERT INTO job_runs (job, library, started, finished, outcome, changed)"
                             " VALUES (?, ?, ?, ?, 'done', '{}')", (job, at_35.name, job_runs.stamp(NOW), job_runs.stamp(NOW)))
            conn.commit()
        finally:
            conn.close()
        found = runs_service.tree(at_35, limit=2)
        self.assertEqual([("bake", None, None), ("index", None, None)], [(r.job, r.parent, r.progress) for r in found])
        self.assertEqual([(35,)], rows(at_35.path, "SELECT MAX(version) FROM schema_version"))


class ARunWhoseProcessEndedIsAbandonedAsTheServerStarts(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        schema.ensure(self.library.path)

    def left_by(self, owner, job):
        """A run of `job` claimed by the process `owner` and left running, as claim stores one."""
        conn = db.connect(self.library.path)
        try:
            run_id = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, ?, ?, 'running', ?)",
                                  (job, self.library.name, job_runs.stamp(NOW), owner)).lastrowid
            conn.commit()
            return run_id
        finally:
            conn.close()

    def start_the_server(self):
        library_actions.bring_up_to_date_in_background([self.library]).join(60)

    def outcomes(self):
        return dict((run_id, (outcome, note)) for run_id, outcome, note in
                    rows(self.library.path, "SELECT id, outcome, note FROM job_runs ORDER BY id"))

    def test_a_server_killed_mid_run_and_a_cli_run_that_died_are_abandoned_and_the_live_runs_kept(self):
        here, pid = socket.gethostname(), os.getpid()
        # This process's id with another start: the process that had the id before, which has ended.
        killed = self.left_by("%s:%d:%d" % (here, pid, processes.started(pid) - 1), "index")
        died = self.left_by("%s:%d:%d" % (here, pid, processes.started(pid) - 2), "embed")
        alive = self.left_by("%s:%d:%d" % (here, os.getppid(), processes.started(os.getppid())), "snapshots")
        elsewhere = self.left_by("lighthouse-pc:4242:1790000000", "sync")
        mine = runs_service.claim(self.library, "bake", self.library.name, NOW).run_id
        self.start_the_server()
        found = self.outcomes()
        self.assertEqual({killed: ("abandoned", job_runs.STOPPED), died: ("abandoned", job_runs.STOPPED),
                          alive: ("running", None), elsewhere: ("running", None), mine: ("running", None)}, found)
        self.assertEqual([(None, None)], rows(self.library.path, "SELECT DISTINCT owner, progress FROM job_runs"
                                                                 " WHERE outcome = 'abandoned'"))

    def test_after_the_sweep_the_job_is_claimed_again_with_nothing_left_to_abandon(self):
        here, pid = socket.gethostname(), os.getpid()
        self.left_by("%s:%d:%d" % (here, pid, processes.started(pid) - 1), "index")
        self.start_the_server()
        claim = runs_service.claim(self.library, "index", self.library.name, NOW + 60)
        self.assertTrue(claim)
        self.assertEqual(0, claim.abandoned)

    def test_a_start_with_nothing_running_writes_nothing(self):
        before = rows(self.library.path, "PRAGMA data_version")
        self.assertEqual(0, runs_service.sweep_abandoned(self.library, NOW))
        self.assertEqual(before, rows(self.library.path, "PRAGMA data_version"))

    def test_a_sweep_that_fails_is_logged_and_the_start_goes_on(self):
        with mock.patch.object(job_runs, "sweep_abandoned", side_effect=OSError("disk gone")), \
                self.assertLogs("tagpup.services.libraries", "WARNING") as said:
            self.start_the_server()
        self.assertIn("abandoned runs", "\n".join(said.output))
        self.assertFalse(library_actions.bringing_up_to_date())


if __name__ == "__main__":
    unittest.main()

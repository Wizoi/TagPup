"""A background run that failed says what happened and what to do next, from its row alone (I10; DECISIONS 2026-10-28,
"ACTIVITY REWORK"; docs/PLAN_ACTIVITY.md, B3; tagpup.services.job_runs.failure, FAILURES).

The owner's morning: the share dropped overnight, and he opens Activity. Each failed run's row holds its kind of failure
and its sentence, written by the one mapper (`job_runs.failure`) from where the knowledge lives: a share that did not
answer, a library another program held, a refusal, an error -- and TagPup's own work in the way (`waited`), which the
next run settles and never needs him. The daily sync whose share stopped answering between its walk and its index queue
fails with the share's sentence (it ended done, in step by nothing, before). The libraries are made as the indexer leaves
them, in homes of their own; the names are fictional.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity, job_runs, maintenance  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db, file_journal, schema  # noqa: E402
from tagpup.store import job_runs as stored_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

HOUR = 3600

NIGHT = 1_790_000_000


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class _Libraries(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="failed_run_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        shares.forget()
        self.addCleanup(shares.forget)

    def library(self, name, photos=("IMG_0001.jpg",), new=()):
        """A library holding `photos` in a folder under a root of its own, each a row as a whole index leaves it, and the
        `new` files beside them that no index has read yet."""
        folder = os.path.join(self.pictures, name, name.title() + " Regatta")
        os.makedirs(folder, exist_ok=True)
        db_path = self.home.library(name + ".db")
        library_actions.create(db_path)
        conn = db.connect(db_path)
        try:
            for each in list(photos) + list(new):
                path = os.path.join(folder, each)
                with open(path, "wb") as handle:
                    handle.write(b"jpeg " + each.encode())
                os.utime(path, (NIGHT, NIGHT))
                if each in photos:
                    photo_rows.add_indexed(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        library = Library(db_path)
        self.addCleanup(indexing_jobs.forget, library)
        return library

    def nightly_sync(self, library, queue=None, at=NIGHT):
        """The daily `sync` job run on the recurring runner for `library`, its new folders handed to `queue`: its run."""
        pictures = os.path.join(self.pictures, library.name)

        class Runtime:
            def sync(self, library, folder=None, apply=False):
                return sync.sync(library, folder=folder, apply=apply, queue=queue, roots=[pictures])
        runner = recurring.Runner(lambda: [library], clock=Clock(at), given={"runtime": Runtime()})
        self.assertTrue(runner.run_job(recurring.JOBS.get(recurring.SYNC), library).ran)
        return job_runs.runs(library, recurring.SYNC, 1)[0]

    def share_drops(self, library):
        """An index queue of `library` that finds the share not answering: the sync's walk saw it, the queue does not."""
        queue = indexing_jobs.queue_for(library)

        def queued(folders):
            with mock.patch.object(photo_actions, "folder_state", lambda folder: "unanswered"):
                return queue.start(folders, lambda folders, report: None, together=True)
        return queued

    def failing(self, library, at):
        (entry,) = [each for each in recurring.overview(library, now=at) if each["name"] == recurring.SYNC]
        return entry["failing"]


class AFailedRunRecordsWhatHappened(_Libraries):
    def test_the_share_drops_between_the_walk_and_the_queue_and_the_sync_fails_with_the_share_s_sentence(self):
        """The walk saw the new file's folder; the index queue then found the share not answering and refused it. Break:
        services/sync._sync reading only the queue's changed and skipped -- the run ended done, recorded in step by
        nothing, and nothing said the share went away."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        run = self.nightly_sync(harbour, self.share_drops(harbour))
        self.assertEqual((job_runs.FAILED, job_runs.SHARE), (run.outcome, job_runs.kind_of(run)))
        self.assertTrue(run.note.startswith("The network share"), run.note)
        self.assertFalse(sync.last(harbour)["last_run"]["in_step"])
        self.assertFalse(indexing_jobs.queue_for(harbour).busy(), "a folder of the share away was queued")

    def test_a_write_another_program_held_the_library_for_is_locked_and_says_so(self):
        harbour = self.library("harbour", photos=("IMG_0001.jpg", "IMG_0002.jpg"))
        os.remove(os.path.join(self.pictures, "harbour", "Harbour Regatta", "IMG_0002.jpg"))   # a removal: the sync writes
        with mock.patch.object(maintenance.journal, "apply", side_effect=sqlite3.OperationalError("database is locked")):
            run = self.nightly_sync(harbour)
        self.assertEqual((job_runs.FAILED, job_runs.LOCKED), (run.outcome, job_runs.kind_of(run)))
        self.assertEqual("OperationalError: database is locked", run.note)

    def test_roots_changed_by_another_process_is_tagpup_s_own_work_waited_for_not_an_error(self):
        harbour = self.library("harbour")
        with mock.patch.object(sync, "_sync", side_effect=roots_service.RootsChanged()):
            run = self.nightly_sync(harbour)
        self.assertEqual((job_runs.FAILED, job_runs.WAITED), (run.outcome, job_runs.kind_of(run)))
        self.assertEqual(roots_service.STOPPED, run.note)
        self.assertEqual(([], False), (activity.needs_you([harbour]), self.failing(harbour, NIGHT)),
                         "TagPup's own work in the way asked the owner to act")


class NeedsYouInTheMorning(_Libraries):
    """The share dropped overnight; the owner opens Activity: what happened and the next step, no log read."""

    def test_two_libraries_one_failed_overnight_and_one_unreadable_the_failure_is_said_with_its_next_step(self):
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        breakwater = self.library("breakwater")
        self.nightly_sync(harbour, self.share_drops(harbour))
        self.assertEqual(job_runs.DONE, self.nightly_sync(breakwater).outcome)
        unreadable = Library(self.home.library("jetty.db"))
        with open(unreadable.path, "wb") as handle:
            handle.write(b"not a library")
        with self.assertLogs("tagpup.services.activity", "WARNING"):
            (group,) = activity.needs_you([harbour, unreadable, breakwater])
        means, next_step, _asks = job_runs.FAILURES[job_runs.SHARE]
        self.assertEqual((job_runs.SHARE, means, next_step), (group["kind"], group["means"], group["next_step"]))
        (item,) = group["items"]
        self.assertEqual(("harbour", recurring.SYNC, 1), (item["library"], item["job"], item["count"]))
        self.assertTrue(item["sentence"].startswith("The network share"), item["sentence"])
        self.assertEqual(stored_runs.stamp(NIGHT), item["since"])
        app = web.create_app("tagpup", lifecycle=Lifecycle(background=runtimes.Background([])))
        app.testing = True
        reply = app.test_client().get("/api/activity/jobs")
        self.assertEqual(200, reply.status_code)
        answered = reply.get_json()
        self.assertEqual([[("harbour", recurring.SYNC, False)]],
                         [[(each["library"], each["job"], each["run_now"]) for each in kind["items"]]
                          for kind in answered["needs_you"]], "this server runs no recurring jobs: no Run now")
        (harbours,) = [each for each in answered["libraries"] if each["name"] == "harbour"]
        (sync_job,) = [each for each in harbours["jobs"] if each["name"] == recurring.SYNC]
        self.assertEqual((job_runs.SHARE, True), (sync_job["runs"][0]["failure"], sync_job["failing"]))

    def test_failed_abandoned_and_failed_again_is_one_item_of_two_until_a_run_is_done(self):
        """Break: an abandoned run counted as failing or as settling it (recurring.overview's `failing` counted an
        abandoned newest run as failing, while what is due reads only done and failed)."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        self.nightly_sync(harbour, self.share_drops(harbour))
        claim = job_runs.claim(harbour, recurring.SYNC, harbour.name, NIGHT + HOUR)
        with mock.patch.object(file_journal, "owner_alive", return_value=False):
            self.assertEqual(1, job_runs.sweep_abandoned(harbour, NIGHT + HOUR + 60))   # the server was killed mid-run
        self.assertEqual(stored_runs.ABANDONED, job_runs.get(harbour, claim.run_id).outcome)
        (group,) = activity.needs_you([harbour])
        self.assertEqual(([1], True), ([each["count"] for each in group["items"]], self.failing(harbour, NIGHT + HOUR)))
        self.nightly_sync(harbour, self.share_drops(harbour), at=NIGHT + 2 * HOUR)
        (group,) = activity.needs_you([harbour])
        self.assertEqual([(2, stored_runs.stamp(NIGHT))], [(each["count"], each["since"]) for each in group["items"]])
        self.assertEqual(2, len([run for run in job_runs.runs(harbour, recurring.SYNC) if run.outcome == "failed"]))
        self.assertEqual(job_runs.DONE, self.nightly_sync(harbour, at=NIGHT + 3 * HOUR).outcome)   # the share is back
        job_runs.claim(harbour, recurring.SYNC, harbour.name, NIGHT + 4 * HOUR)
        with mock.patch.object(file_journal, "owner_alive", return_value=False):
            job_runs.sweep_abandoned(harbour, NIGHT + 4 * HOUR + 60)
        self.assertEqual(([], False), (activity.needs_you([harbour]), self.failing(harbour, NIGHT + 4 * HOUR)))

    def test_a_library_at_35_reads_its_failed_run_as_an_error_that_needs_you(self):
        """A run that failed before kinds were written -- the 12 live rows -- and a library the page's look reads without
        migrating it."""
        at_35 = Library(self.home.library("breakwater.db"))
        with migration_names.up_to(35):
            schema.ensure(at_35.path)
        schema.forget()
        conn = db.connect(at_35.path)
        try:
            run_id = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, ?, ?, 'running', ?)",
                                  (recurring.SYNC, at_35.name, stored_runs.stamp(NIGHT), file_journal.owner())).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(stored_runs.finish(at_35.path, run_id, stored_runs.FAILED, NIGHT + 60, {"errors": 1},
                                           "RuntimeError: the walk stopped"))
        (group,) = activity.needs_you([at_35])
        self.assertEqual([(job_runs.ERROR, "breakwater", "RuntimeError: the walk stopped")],
                         [(group["kind"], each["library"], each["sentence"]) for each in group["items"]])


if __name__ == "__main__":
    unittest.main()

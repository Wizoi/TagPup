"""A background run that failed says what happened and what to do next, from its row alone (I10; DECISIONS 2026-10-28,
"ACTIVITY REWORK"; docs/PLAN_ACTIVITY.md, B3; tagpup.services.job_runs.failure, FAILURES).

The owner's morning: the share dropped overnight, and he opens Activity. Each failed run's row holds its kind of failure
and its sentence, written by the one mapper (`job_runs.failure`) from where the knowledge lives: a share that did not
answer, a library another program held, a refusal, an error -- and TagPup's own work in the way (`waited`), which the
next run settles and never needs him. The next step names how each job is run again, and no button that is not there.
The daily sync whose share stopped answering between its walk and its index queue fails with the share's sentence; one
whose new folder was gone by then skips it. The libraries are made as the indexer leaves them, in homes of their own; the
names are fictional.
"""
import os
import shutil
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity, job_runs, maintenance  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db, file_journal, journal, schema  # noqa: E402
from tagpup.store import job_runs as stored_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

HOUR = 3600
SNAPSHOTS = "snapshots"

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

    def folder_of(self, name):
        return os.path.join(self.pictures, name, name.title() + " Regatta")

    def library(self, name, photos=("IMG_0001.jpg",), new=(), away=()):
        """A library holding `photos` in a folder under a root of its own, each a row as a whole index leaves it, the
        `new` files beside them that no index has read yet, and the rows of `away` in a folder of its outside the root."""
        folder = self.folder_of(name)
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
            for each in away:
                photo_rows.add_indexed(conn, os.path.join(self.away_of(name), each), {})
            conn.commit()
        finally:
            conn.close()
        library = Library(db_path)
        self.addCleanup(indexing_jobs.forget, library)
        return library

    def away_of(self, name):
        """The folder of `name`'s rows that a share holds, outside its root."""
        return os.path.join(self.home.root, "Share", name.title() + " Quay")

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

    def failing(self, library, at, limit=1):
        """Whether the Activity page lists `library`'s sync failing, its last `limit` runs listed (?runs=)."""
        (entry,) = [each for each in recurring.overview(library, now=at, limit=limit) if each["name"] == recurring.SYNC]
        return entry["failing"]


class AFailedRunRecordsWhatHappened(_Libraries):
    def test_new_files_whose_share_stops_answering_before_the_queue_fail_the_sync_and_a_folder_gone_is_skipped(self):
        """The walk saw the new files' folders; the index queue then found one share not answering and the other folder
        gone. Break: services/sync._sync reading only the queue's changed and skipped (the run ended done, in step by
        nothing, and nothing said the share went away), or failing the sync for a folder that is gone, which the next
        sync settles by removing its rows (it read "Something went wrong" and asked the owner to act)."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        run = self.nightly_sync(harbour, self.share_drops(harbour))
        self.assertEqual((job_runs.FAILED, job_runs.SHARE), (run.outcome, job_runs.kind_of(run)))
        self.assertTrue(run.note.startswith("The network share"), run.note)
        self.assertFalse(sync.last(harbour)["last_run"]["in_step"])
        self.assertFalse(indexing_jobs.queue_for(harbour).busy(), "a folder of the share away was queued")

        lighthouse = self.library("lighthouse", new=("IMG_0002.jpg",))
        queue = indexing_jobs.queue_for(lighthouse)

        def gone_first(folders):
            shutil.rmtree(self.folder_of("lighthouse"))             # moved away between the walk and the queue
            return queue.start(folders, lambda folders, report: None, together=True)
        run = self.nightly_sync(lighthouse, gone_first)
        self.assertEqual((job_runs.DONE, None, 1), (run.outcome, job_runs.kind_of(run), run.changed["skipped"]))
        self.assertFalse(queue.busy(), "a folder that is gone was queued")
        self.assertEqual([], activity.needs_you([lighthouse]))


class WhatHappenedSaysASyncOnce(_Libraries):
    """What happened, the morning after: the daily sync ran (the `sync` job, which writes its run's row) and its sync wrote
    its record (`sync-whole`); the owner sees one line of it, or of its failure, never two (activity.timeline_and_children)."""

    def syncs_of(self, library):
        return [entry for entry in activity.timeline(library) if entry["kind"] in ("sync", "job")]

    def test_the_daily_sync_is_one_entry_its_run_and_its_record_together(self):
        """Break: the job's row and its sync's record each an entry (the sync shown twice, the same finish), or the
        record's id shown as a count ("record 2")."""
        harbour = self.library("harbour", photos=("IMG_0001.jpg", "IMG_0002.jpg"))
        os.remove(os.path.join(self.folder_of("harbour"), "IMG_0002.jpg"))
        run = self.nightly_sync(harbour)
        self.assertEqual(job_runs.DONE, run.outcome)
        (entry,) = self.syncs_of(harbour)
        self.assertEqual(("sync", "sync of the whole library", "in step", None),
                         (entry["kind"], entry["what"], entry["outcome"], entry["error"]))
        self.assertEqual((run.id, activity.runs.job_tag(harbour.name, run.id), run.started, run.finished),
                         (entry["id"], entry["run"], entry["time"], entry["finished"]))
        self.assertEqual(1, entry["counts"]["found_missing"], entry["counts"])
        self.assertNotIn(sync.RECORD, entry["counts"])

    def test_a_sync_whose_share_drops_is_one_failed_entry_saying_why(self):
        """Break: the record's "not in step" said beside a failed job, or the failure said without its sentence."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        self.nightly_sync(harbour, self.share_drops(harbour))
        (entry,) = self.syncs_of(harbour)
        self.assertEqual(("sync", job_runs.FAILED), (entry["kind"], entry["outcome"]))
        self.assertTrue(entry["error"].startswith("The network share"), entry["error"])

    def test_a_sync_no_job_ran_keeps_its_record_alone(self):
        """The CLI's sync, or a watcher's sync of a folder, writes its record and no job run: its entry is the record's."""
        harbour = self.library("harbour")
        result = sync.sync(harbour, apply=True, roots=[os.path.join(self.pictures, "harbour")])
        (entry,) = self.syncs_of(harbour)
        self.assertEqual(("sync", result.details[sync.RECORD], activity.runs.sync_tag(harbour.name, entry["time"])),
                         (entry["kind"], entry["id"], entry["run"]))


class NeedsYouInTheMorning(_Libraries):
    """The share dropped overnight; the owner opens Activity: what happened and the next step, no log read."""

    def test_three_libraries_failed_three_ways_each_is_said_with_its_next_step_and_the_one_that_waited_is_not(self):
        """Harbour's share dropped before its index queue; breakwater's write found the library held by another program
        while a folder of its share did not answer, and a newer TagPup (the CLI from the repository) recorded a failure
        of a kind this version does not know; jetty's write met a row the bake changed after the plan read it, and the
        next night its roots changed under it; quay, at 35, failed before kinds were written; pier is unreadable.
        Break: a Result not ok losing to Job.failed (breakwater read `share`); an unknown kind raising KeyError (the
        whole of breakwater left out of Needs you, and its jobs `[]` on the page); a journal refusal at the apply read
        as `refused` (jetty asked the owner to act); "run it now" said where there is no Run now."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        self.nightly_sync(harbour, self.share_drops(harbour))

        breakwater = self.library("breakwater", photos=("IMG_0001.jpg", "IMG_0002.jpg"), away=("IMG_0003.jpg",))
        newer = job_runs.claim(breakwater, SNAPSHOTS, breakwater.name, NIGHT - HOUR)
        stored_runs.finish(breakwater.path, newer.run_id, stored_runs.FAILED, NIGHT - HOUR + 60,
                           {"errors": 1, job_runs.FAILURE: "quota"}, "QuotaError: the disk allows no more")
        moved_to = os.path.join(self.pictures, "breakwater", "Moved")      # a photo moved: the sync relinks its row
        os.makedirs(moved_to)
        os.rename(os.path.join(self.folder_of("breakwater"), "IMG_0002.jpg"), os.path.join(moved_to, "IMG_0002.jpg"))
        asleep, real = paths.key(self.away_of("breakwater")), shares.Walk.folder_state

        def walked(walk, folder, *args, **kwargs):
            return "unanswered" if paths.key(folder) == asleep else real(walk, folder, *args, **kwargs)
        with mock.patch.object(shares.Walk, "folder_state", walked), \
                mock.patch.object(maintenance.journal, "apply", side_effect=sqlite3.OperationalError("database is locked")):
            run = self.nightly_sync(breakwater)
        self.assertEqual((job_runs.LOCKED, "OperationalError: database is locked"), (job_runs.kind_of(run), run.note))

        jetty = self.library("jetty", photos=("IMG_0001.jpg", "IMG_0002.jpg"))
        os.remove(os.path.join(self.folder_of("jetty"), "IMG_0002.jpg"))
        with mock.patch.object(maintenance.journal, "apply", side_effect=journal.Refusal(["photos 2 changed"])):
            run = self.nightly_sync(jetty)
        self.assertEqual((job_runs.FAILED, job_runs.WAITED), (run.outcome, job_runs.kind_of(run)))
        with mock.patch.object(sync, "_sync", side_effect=roots_service.RootsChanged()):
            run = self.nightly_sync(jetty, at=NIGHT + HOUR)
        self.assertEqual((job_runs.WAITED, roots_service.STOPPED), (job_runs.kind_of(run), run.note))
        self.assertFalse(self.failing(jetty, NIGHT + HOUR), "TagPup's own work in the way asked the owner to act")

        quay = Library(self.home.library("quay.db"))
        with migration_names.up_to(35):
            schema.ensure(quay.path)
        schema.forget()
        conn = db.connect(quay.path)
        try:
            old = conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, ?, ?, 'running', ?)",
                               (recurring.SYNC, quay.name, stored_runs.stamp(NIGHT), file_journal.owner())).lastrowid
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(stored_runs.finish(quay.path, old, stored_runs.FAILED, NIGHT + 60, {"errors": 1},
                                           "RuntimeError: the walk stopped"))
        pier = Library(self.home.library("pier.db"))
        with open(pier.path, "wb") as handle:
            handle.write(b"not a library")

        with self.assertLogs("tagpup.services.activity", "WARNING"):
            found = activity.needs_you([harbour, pier, breakwater, jetty, quay], recurring.JOBS.names())
        self.assertEqual([(job_runs.SHARE, [("harbour", recurring.SYNC, 1)]),
                          (job_runs.LOCKED, [("breakwater", recurring.SYNC, 1)]),
                          (job_runs.ERROR, [("breakwater", SNAPSHOTS, 1), ("quay", recurring.SYNC, 1)])],
                         [(group["kind"], [(each["library"], each["job"], each["count"]) for each in group["items"]])
                          for group in found])
        self.assertEqual([job_runs.FAILURES[group["kind"]][0] for group in found], [group["means"] for group in found])
        (shared,), (held,) = found[0]["items"], found[1]["items"]
        self.assertTrue(shared["sentence"].startswith("The network share"), shared["sentence"])
        self.assertEqual((stored_runs.stamp(NIGHT), True), (shared["since"], shared["run_now"]))
        self.assertIn("awake", shared["next_step"])
        self.assertIn("run it now", held["next_step"])
        self.assertEqual("RuntimeError: the walk stopped", found[2]["items"][1]["sentence"])

        app = web.create_app("tagpup", lifecycle=Lifecycle(background=runtimes.Background([])))
        app.testing = True
        reply = app.test_client().get("/api/activity/jobs?runs=1")
        self.assertEqual(200, reply.status_code)
        answered = reply.get_json()
        items = [each for kind in answered["needs_you"] for each in kind["items"]]
        self.assertEqual([("harbour", False), ("breakwater", False), ("breakwater", False), ("quay", False)],
                         [(each["library"], each["run_now"]) for each in items], "this server runs no recurring jobs")
        self.assertEqual([], [each["next_step"] for each in items if "run it now" in each["next_step"]])
        jobs = {each["name"]: {job["name"]: job for job in each["jobs"]} for each in answered["libraries"]}
        self.assertEqual((job_runs.SHARE, True), (jobs["harbour"]["sync"]["runs"][0]["failure"],
                                                  jobs["harbour"]["sync"]["failing"]))
        self.assertEqual((job_runs.ERROR, True), (jobs["breakwater"]["snapshots"]["runs"][0]["failure"],
                                                  jobs["breakwater"]["snapshots"]["failing"]))

        timeline = app.test_client().get("/api/activity/timeline").get_json()
        self.assertEqual(["pier"], [each["library"] for each in timeline["unreadable"]],
                         "a library that cannot be read was only logged: What happened said nothing of it")
        self.assertTrue(timeline["unreadable"][0]["error"])
        (harbour_sync,) = [each for each in timeline["entries"] if each["library"] == "harbour" and each["kind"] != "change"]
        self.assertEqual(("sync", job_runs.FAILED), (harbour_sync["kind"], harbour_sync["outcome"]))

    def test_failed_abandoned_and_failed_again_is_one_item_of_two_until_a_run_is_done(self):
        """Break: an abandoned run counted as failing or as settling it (recurring.overview's `failing` counted an
        abandoned newest run as failing, while what is due reads only done and failed), or `failing` read from fewer
        rows than Needs you (?runs=1 listing the abandoned run alone said it was not failing)."""
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


if __name__ == "__main__":
    unittest.main()

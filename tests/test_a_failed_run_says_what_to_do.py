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
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import job_runs, maintenance  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

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
        """A library holding `photos` in a folder of its own, each a row as a whole index leaves it, and the `new` files
        beside them that no index has read yet."""
        folder = os.path.join(self.pictures, name.title() + " Regatta")
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
        pictures = self.pictures

        class Runtime:
            def sync(self, library, folder=None, apply=False):
                return sync.sync(library, folder=folder, apply=apply, queue=queue, roots=[pictures])
        runner = recurring.Runner(lambda: [library], clock=Clock(at), given={"runtime": Runtime()})
        self.assertTrue(runner.run_job(recurring.JOBS.get(recurring.SYNC), library).ran)
        return job_runs.runs(library, recurring.SYNC, 1)[0]


class AFailedRunRecordsWhatHappened(_Libraries):
    def test_the_share_drops_between_the_walk_and_the_queue_and_the_sync_fails_with_the_share_s_sentence(self):
        """The walk saw the new file's folder; the index queue then found the share not answering and refused it. Break:
        services/sync._sync reading only the queue's changed and skipped -- the run ended done, recorded in step by
        nothing, and nothing said the share went away."""
        harbour = self.library("harbour", new=("IMG_0002.jpg",))
        queue = indexing_jobs.queue_for(harbour)

        def queued(folders):
            with mock.patch.object(photo_actions, "folder_state", lambda folder: "unanswered"):
                return queue.start(folders, lambda folders, report: None, together=True)
        run = self.nightly_sync(harbour, queued)
        self.assertEqual((job_runs.FAILED, job_runs.SHARE), (run.outcome, job_runs.kind_of(run)))
        self.assertTrue(run.note.startswith("The network share"), run.note)
        self.assertFalse(sync.last(harbour)["last_run"]["in_step"])
        self.assertFalse(queue.busy(), "a folder of the share away was queued")

    def test_a_write_another_program_held_the_library_for_is_locked_and_says_so(self):
        harbour = self.library("harbour", photos=("IMG_0001.jpg", "IMG_0002.jpg"))
        os.remove(os.path.join(self.pictures, "Harbour Regatta", "IMG_0002.jpg"))   # a removal: the sync writes
        with mock.patch.object(maintenance.journal, "apply", side_effect=sqlite3.OperationalError("database is locked")):
            run = self.nightly_sync(harbour)
        self.assertEqual((job_runs.FAILED, job_runs.LOCKED), (run.outcome, job_runs.kind_of(run)))
        self.assertEqual("OperationalError: database is locked", run.note)

    def test_roots_changed_by_another_process_is_tagpup_s_own_work_waited_for_not_an_error(self):
        harbour = self.library("harbour")
        with mock.patch.object(sync, "_sync", side_effect=roots_service.RootsChanged()):
            run = self.nightly_sync(harbour)
        self.assertEqual((job_runs.FAILED, job_runs.WAITED), (run.outcome, job_runs.kind_of(run)))
        self.assertFalse(job_runs.FAILURES[job_runs.WAITED][2])
        self.assertEqual(roots_service.STOPPED, run.note)


if __name__ == "__main__":
    unittest.main()

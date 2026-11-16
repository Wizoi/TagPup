"""Refresh on a folder syncs it, and a file it finds gone starts the whole-library sync that removes it (I12; plan
docs/plans/folder-refresh-syncs-the-folder.md; tagpup.jobs.recurring.refresh_folder, POST /api/sync with a folder,
applied).

A sync of one folder never removes a row (DECISIONS 2026-10-28): a file gone from it may have moved to a folder it did not
look at. Only the whole sync removes, after pairing the moves, so the folder's Refresh starts one on the server's recurring
runner -- a run of the `sync` job, claimed, started once however often Refresh is pressed. The folder's sync takes the
library's walk as the watcher's does: a Refresh while the daily sync walks syncs nothing and says so. A share that does not
answer leaves its rows not looked at, never missing, and asks for nothing.

The library is made as the indexer leaves it, in the test's own home; fictional names. The server is Flask's test client.
The whole sync is held on an event while the test looks; its thread (Runner.start_job's) is joined, never slept on.
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, job_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

THEN = 1_700_000_000
WAIT = 20
PORTS = {"tagpup": 8090, "tuner": 8080}


class HeldRuntime:
    """The process's Runtime as the recurring runner is given it, its whole syncs held on `release` once `entered` is
    set (when the test holds them)."""

    def __init__(self, runtime, hold=False):
        self.runtime, self.hold = runtime, hold
        self.entered, self.release = threading.Event(), threading.Event()
        self.wholes = 0

    def sync(self, library, folder=None, apply=False):
        if folder is None:
            self.wholes += 1
            self.entered.set()
            if self.hold:
                self.release.wait(WAIT)
        return self.runtime.sync(library, folder=folder, apply=apply)


class ARefreshOfAFolder(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="refresh_")
        photos = os.path.join(self.home.root, "Photos")
        self.regatta = os.path.join(photos, "2019 Regatta")
        self.harvest = os.path.join(photos, "2020 Harvest")
        db_path = self.home.library("regatta.db")
        library_actions.create(db_path)
        self.library = Library(db_path)
        conn = db.connect(db_path)
        try:
            for folder, count in ((self.regatta, 2), (self.harvest, 1)):
                os.makedirs(folder)
                for n in range(count):
                    path = os.path.join(folder, "IMG_%04d.jpg" % n)
                    with open(path, "wb") as handle:
                        handle.write(b"jpeg %s %d" % (os.path.basename(folder).encode(), n))
                    os.utime(path, (THEN, THEN))
                    photo_rows.add_indexed(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        self.indexed = []

        def index_folder(library, subfolders=True):
            def index(folder, report):
                self.indexed.extend([folder] if isinstance(folder, str) else list(folder))
                return Result(attempted=1, changed=1)
            return index
        patcher = mock.patch("tagpup.runtime.index_folder", side_effect=index_folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = runtimes.Runtime()
        # The index queue's thread ends before the home goes: a Refresh queues the new files' folder each time.
        self.addCleanup(lambda: self.runtime.index_queues.of(self.library).wait())

    def client(self, runner=None):
        """TagPup's server as the owner runs it: the process's Runtime, and its recurring runner (None: one that runs
        no recurring jobs, TAGPUP_NO_JOBS)."""
        background = runtimes.Background([(runtimes.RECURRING_JOBS, runner)] if runner is not None else [])
        app = web.create_app("tagpup", ports=PORTS, runtime=self.runtime,
                             lifecycle=Lifecycle(background=background, runtime=self.runtime))
        app.testing = True
        return app.test_client()

    def runner(self, hold=False):
        held = HeldRuntime(self.runtime, hold)
        self.addCleanup(held.release.set)
        return recurring.Runner(lambda: [self.library], given={"runtime": held}), held

    def refresh(self, client, folder=None):
        reply = client.post("/regatta/api/sync", json={"folder": folder or self.regatta, "apply": True})
        self.assertIn(reply.status_code, (200, 400), reply.get_data(as_text=True))
        return reply.get_json()

    def whole_ended(self):
        """Join the run-now threads (Runner.start_job's): the whole sync, its run ended."""
        for thread in threading.enumerate():
            if thread.name == "RunNowThread":
                thread.join(WAIT)
                self.assertFalse(thread.is_alive(), "the whole sync never ended")

    def rows(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return {paths.key(path): photo_id for photo_id, path in conn.execute("SELECT id, path FROM photos")}
        finally:
            conn.close()

    def sync_runs(self):
        return job_runs.runs(self.library.path, recurring.SYNC)

    def test_a_file_gone_and_one_new_the_folder_synced_the_whole_started_once_and_it_removes_the_row(self):
        """Break: the route's folder+apply arm not calling refresh_folder (no `whole`, nothing started), the folder's
        sync not taking the library's walk (the second Refresh syncs beside the whole), or refresh_folder starting the
        whole sync while the folder found nothing gone (the third Refresh starts a second run)."""
        runner, held = self.runner(hold=True)
        client = self.client(runner)
        gone = os.path.join(self.regatta, "IMG_0000.jpg")
        os.remove(gone)
        new = os.path.join(self.regatta, "IMG_0107.jpg")
        with open(new, "wb") as handle:
            handle.write(b"a new photo of another size")
        before = self.rows()

        first = self.refresh(client)
        self.assertEqual(("synced", "started", True), (first["folder"], first["whole"], first["success"]), first)
        self.assertEqual((1, 1, 1), (first["counts"]["missing"], first["counts"]["new"], first["queued"]), first)
        self.assertIsNone(first["whole_why"])
        self.assertTrue(held.entered.wait(WAIT), "the whole sync never started")
        self.assertEqual(before, self.rows(), "the folder's sync removed a row itself")
        self.assertEqual([(first["whole_run_id"], job_runs.RUNNING)], [(r.id, r.outcome) for r in self.sync_runs()])

        # Pressed again while the whole sync walks: the walk is the whole's; nothing synced, nothing asked.
        again = self.refresh(client)
        self.assertEqual({"success": False, "dry_run": False, "folder": "running", "whole": None, "whole_run_id": None,
                          "whole_why": None}, again)
        held.release.set()
        self.whole_ended()
        self.assertEqual(1, held.wholes)
        self.assertEqual([job_runs.DONE], [r.outcome for r in self.sync_runs()])
        self.assertNotIn(paths.key(gone), self.rows(), "the whole sync did not remove the gone file's row")
        self.runtime.index_queues.of(self.library).wait()
        self.assertIn(paths.stored(self.regatta), self.indexed, "the new file's folder was not queued")

        # Pressed once more: nothing gone now, nothing started.
        last = self.refresh(client)
        self.assertEqual(("synced", "not needed", 0), (last["folder"], last["whole"], last["counts"]["missing"]), last)
        self.assertEqual(1, len(self.sync_runs()))

    def test_a_file_moved_to_a_sibling_folder_keeps_its_row_through_the_whole_sync(self):
        """The folder's sync cannot see where the file went; the whole sync pairs the move and the row follows it.
        Break: the whole sync not started, so the row never follows the move."""
        runner, _held = self.runner()
        client = self.client(runner)
        source = os.path.join(self.regatta, "IMG_0001.jpg")
        destination = os.path.join(self.harvest, "IMG_0001.jpg")
        photo_id = self.rows()[paths.key(source)]
        os.replace(source, destination)
        reply = self.refresh(client)
        self.assertEqual(("synced", "started", 1), (reply["folder"], reply["whole"], reply["counts"]["missing"]), reply)
        self.whole_ended()
        rows = self.rows()
        self.assertNotIn(paths.key(source), rows)
        self.assertEqual(photo_id, rows.get(paths.key(destination)), "the moved file's row did not follow it")
        self.assertEqual(3, len(rows))

    def test_a_folder_that_does_not_answer_is_not_looked_at_and_asks_for_nothing(self):
        """A share that stopped answering as the folder is listed. Break: refresh_folder starting the whole sync on
        anything but a file found gone."""
        runner, held = self.runner()
        client = self.client(runner)
        before = self.rows()
        real, away = os.scandir, os.path.normcase(self.regatta)

        def scandir(path="."):
            if os.path.normcase(str(path)) == away:
                raise OSError(64, "The specified network name is no longer available")
            return real(path)
        with mock.patch.object(os, "scandir", scandir):
            reply = self.refresh(client)
        self.assertEqual("not needed", reply["whole"], reply)
        self.assertEqual(0, (reply.get("counts") or {}).get("missing", 0), reply)
        self.assertEqual(before, self.rows())
        self.assertEqual((0, []), (held.wholes, self.sync_runs()))

    def test_a_folder_the_library_does_not_hold_is_refused_and_the_whole_sync_is_not_assessed(self):
        """A folder outside the library's roots. Break: refresh_folder answering "not needed" for a sync that did not
        succeed, as if the whole sync had been looked at."""
        runner, held = self.runner()
        client = self.client(runner)
        outside = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(outside)
        reply = client.post("/regatta/api/sync", json={"folder": outside, "apply": True})
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        answer = reply.get_json()
        self.assertEqual(("not synced", None, None, None),
                         (answer["folder"], answer["whole"], answer["whole_run_id"], answer["whole_why"]), answer)
        self.assertEqual((0, []), (held.wholes, self.sync_runs()))

    def test_a_server_with_no_runner_syncs_the_folder_and_says_so_as_run_now_does(self):
        """Break: the route reaching for a runner of its own, or saying "no runner" in words of its own."""
        client = self.client()
        os.remove(os.path.join(self.regatta, "IMG_0000.jpg"))
        reply = self.refresh(client)
        self.assertEqual(("synced", "no runner", 1), (reply["folder"], reply["whole"], reply["counts"]["missing"]), reply)
        run_now = client.post("/api/activity/jobs/run", json={"job": recurring.SYNC, "library": "regatta"})
        self.assertEqual(409, run_now.status_code)
        self.assertEqual(run_now.get_json()["error"], reply["whole_why"])
        self.assertEqual([], self.sync_runs())

    def test_a_whole_sync_another_process_runs_is_not_started_twice_and_both_routes_say_why_alike(self):
        """The CLI's `sync --apply` holds the claim of the `sync` job in another process (stood in for by a claim
        this live process holds). Break: the two routes wording the refusal each their own way."""
        runner, held = self.runner()
        client = self.client(runner)
        claim = job_runs.claim(self.library.path, recurring.SYNC, self.library.name, THEN)
        self.assertTrue(claim.run_id)
        os.remove(os.path.join(self.regatta, "IMG_0000.jpg"))
        reply = self.refresh(client)
        self.assertEqual(("synced", "running"), (reply["folder"], reply["whole"]), reply)
        run_now = client.post("/api/activity/jobs/run", json={"job": recurring.SYNC, "library": "regatta"}).get_json()
        self.assertFalse(run_now["started"], run_now)
        self.assertEqual(run_now["why"], reply["whole_why"])
        self.whole_ended()
        self.assertEqual(0, held.wholes)
        self.assertEqual([claim.run_id], [r.id for r in self.sync_runs()])


if __name__ == "__main__":
    unittest.main()

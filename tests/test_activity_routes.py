"""The Activity page's routes (tagpup.web.activity_routes; docs/ARCHITECTURE.md, phase 8.5):
this PC only; what runs now; each job's runs, failures kept in view until a later success,
and Run now; the syncs, the watcher, the snapshots; one timeline of what was done; and the
logs, read from the end and never whole, by level, text and run.

Every library is made in a home of the test's own; the runs, syncs and changes are
seeded as the store records them (tagpup.store.job_runs, its syncs; a journaled change of
the settings), never through what is under test.
"""
import logging
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from waiting import wait_until  # noqa: E402

from tagpup import launcher, logs  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity  # noqa: E402
from tagpup.services import job_runs as job_runs_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import file_journal, job_runs, snapshots  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

PORTS = {"tagpup": 8090, "tuner": 8080}


def seconds(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


class FakeWatcher:
    def __init__(self, found):
        self.found = found

    def start(self):
        pass

    def stop(self, timeout=30):
        return True

    def busy(self):
        return False

    def status(self):
        return self.found


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="activity_")
        #: The server's runtime: its index queues.
        self.runtime = runtimes.Runtime()
        self.harbour = self.made("harbour")
        self.regatta = self.made("regatta")

    def made(self, name):
        path = self.home.library(name + ".db")
        library_actions.create(path)
        return Library(path)

    def client(self, tasks=(), kind="tagpup"):
        background = runtimes.Background(list(tasks))
        app = web.create_app(kind, ports=PORTS, runtime=self.runtime,
                             lifecycle=Lifecycle(background=background, runtime=self.runtime))
        app.testing = True
        return app.test_client()

    def get(self, url, **kwargs):
        reply = self.client(**kwargs).get(url)
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()


class ThisPcOnly(Base):
    """The page and its routes answer this PC alone, whatever the server listens on: another address is refused by
    tagpup.web.security.THIS_PC_ONLY (tests/test_every_route_answers_this_pc_or_says_why.py)."""

    def test_both_apps_serve_it_under_no_library(self):
        for kind in ("tagpup", "tuner"):
            self.assertEqual(200, self.client(kind=kind).get("/api/activity/now").status_code, kind)

    def test_activity_is_no_librarys_name(self):
        made = library_actions.create(self.home.library("activity.db"))
        self.assertTrue(made.refused, "a library called activity would never be reached")


class ThePage(Base):
    """Served at /activity/ by both apps, its modules beside it and the shared ones at
    common/, uncached; under no library, whatever the server was started on."""

    def test_the_page_its_modules_and_the_shared_ones(self):
        for kind in ("tagpup", "tuner"):
            client = self.client(kind=kind)
            page = client.get("/activity/")
            self.assertEqual(200, page.status_code)
            self.assertIn('<script type="module" src="main.js">', page.get_data(as_text=True))
            self.assertIn("no-store", page.headers["Cache-Control"])
            module = client.get("/activity/main.js")
            self.assertEqual(200, module.status_code)
            self.assertTrue(module.content_type.startswith("application/javascript"))
            self.assertEqual(200, client.get("/activity/common/api.js").status_code)
            self.assertEqual(200, client.get("/activity/style.css").status_code)
            self.assertIn(client.get("/activity").status_code, (301, 308))

    def test_nothing_else_is_reached(self):
        client = self.client()
        for url in ("/activity/nothing.js", "/activity/..%2Fapp.js", "/activity/common/nothing.js"):
            self.assertEqual(404, client.get(url).status_code, url)

    def test_a_server_started_on_a_library_serves_it_too(self):
        app = web.create_app("tagpup", startup=self.harbour, ports=PORTS, runtime=self.runtime)
        app.testing = True
        client = app.test_client()
        self.assertEqual(200, client.get("/activity/").status_code)
        self.assertEqual(200, client.get("/api/activity/now").status_code)


class Now(Base):
    def test_the_index_queue_the_running_jobs_and_suggest_by_library(self):
        release = threading.Event()
        queue = self.runtime.index_queues.of(self.harbour)

        def index(folder, report):
            report("Indexing: 40% (4/10)", 40)
            release.wait(10)
            return Result(attempted=1, changed=1)

        folder_a = os.path.join(self.home.root, "Regatta 2019")
        folder_b = os.path.join(self.home.root, "Harbour Walk")
        os.makedirs(folder_a)
        os.makedirs(folder_b)
        queue.start([folder_a, folder_b], index)
        wait_until(lambda: (queue.now()["running"] or {}).get("percent") == 40, what="the run at 40%")
        claim = job_runs.claim(self.regatta.path, "snapshots", "regatta", time.time())
        try:
            found = self.get("/api/activity/now")
        finally:
            release.set()
            queue.wait()
        by_name = {entry["name"]: entry for entry in found["libraries"]}
        self.assertEqual({"harbour", "regatta"}, set(by_name))
        indexing = by_name["harbour"]["indexing"]
        self.assertEqual("Regatta 2019", indexing["running"]["name"])
        self.assertEqual(40, indexing["running"]["percent"])
        self.assertTrue(runs.is_tag(indexing["running"]["run"]))
        self.assertEqual([{"name": "Harbour Walk", "folders": 1}], indexing["queued"])
        self.assertEqual([], by_name["harbour"]["jobs"])
        self.assertEqual([{"job": "snapshots", "run_id": claim.run_id, "started": by_name["regatta"]["jobs"][0]["started"],
                           "run": "job:regatta:%d" % claim.run_id, "parent": None, "progress": None,
                           "logs": [logs.SERVER_LOG], "library": "regatta", "source": "row", "state": "running"}],
                         by_name["regatta"]["jobs"])
        self.assertEqual([logs.run_log("indexer", indexing["running"]["run"]), logs.SERVER_LOG],
                         indexing["running"]["logs"])
        self.assertIsNone(by_name["regatta"]["indexing"]["running"])
        self.assertTrue(found["server"]["taking_work"])

    def test_the_watchers_sync_under_way(self):
        """A whole sync's phase and counts are its run's row, the folder it is at this process's; a sync of one folder
        says no progress; a library whose runs cannot be read leaves the others' shown."""
        now = time.time()
        claim = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", now)
        job_runs.progress(self.harbour.path, claim.run_id, "walking_again", {"folders": 812, "files": 20417}, now)
        whole = {"library": "Harbour", "folder": None, "started": "x", "walking_at": "D:/Photos/Regatta"}
        watcher = FakeWatcher({"running": True, "roots": [], "libraries": {}, "syncing": whole})
        found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertTrue(found["watching"])
        self.assertEqual({"library": "Harbour", "folder": None, "started": "x",
                          "progress": {"phase": "walking the folders again", "folders": 812, "files": 20417,
                                       "folder": "D:/Photos/Regatta", "progressed": job_runs.stamp(now)},
                          "state": "running"},
                         found["syncing"])
        watcher.found["syncing"] = dict(whole, folder="D:/Photos/Regatta")
        self.assertIsNone(self.get("/api/activity/now", tasks=[("folder watcher", watcher)])["syncing"]["progress"])
        watcher.found["syncing"] = whole
        real = activity.under_way

        def unreadable(library):
            if library.name.lower() == "harbour":
                raise OSError("the library is locked")
            return real(library)

        with mock.patch.object(activity, "under_way", unreadable):
            found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertIsNone(found["syncing"]["progress"])
        self.assertEqual({"harbour", "regatta"}, {entry["name"] for entry in found["libraries"]})

    def test_a_failure_after_the_sync_s_row_was_read_does_not_make_the_library_unreadable(self):
        """#2215: only the runs of a library that cannot be listed leave its sync unknown (null); the index runs of a
        sync failing to read later in the block do not."""
        now = time.time()
        claim = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", now)
        job_runs.progress(self.harbour.path, claim.run_id, "walking", {"folders": 5, "files": 40}, now)
        whole = {"library": "Harbour", "folder": None, "started": "x", "walking_at": None}
        watcher = FakeWatcher({"running": True, "roots": [], "libraries": {}, "syncing": whole})
        with mock.patch.object(activity, "index_runs_under", side_effect=OSError("the library is locked")):
            found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertEqual("walking the folders", found["syncing"]["progress"]["phase"])
        self.assertEqual({"harbour", "regatta"}, {entry["name"] for entry in found["libraries"]})

    def test_the_folder_the_walk_is_at_shows_before_the_row_has_said_anything(self):
        """#2203: the walk's folder is the process's memory; the row's counts trail it by up to 10 s, or are a newer
        version's: the folder shows alone, the page's own words (no phase) for the rest."""
        claim = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", time.time())
        whole = {"library": "Harbour", "folder": None, "started": "x", "walking_at": "D:/Photos/Regatta"}
        watcher = FakeWatcher({"running": True, "roots": [], "libraries": {}, "syncing": whole})
        found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertEqual({"folder": "D:/Photos/Regatta"}, found["syncing"]["progress"])
        watcher.found["syncing"] = dict(whole, walking_at=None)
        self.assertIsNone(self.get("/api/activity/now", tasks=[("folder watcher", watcher)])["syncing"]["progress"])
        # A row written by a newer version: a phase this one does not know says nothing, the folder still shows.
        job_runs.progress(self.harbour.path, claim.run_id, "somewhere_new", {"folders": 3}, time.time())
        watcher.found["syncing"] = whole
        found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertEqual({"folder": "D:/Photos/Regatta"}, found["syncing"]["progress"])

    def test_a_sync_silent_for_12_minutes_in_a_phase_this_version_does_not_know_is_stuck(self):
        """The watcher's whole sync is its row's run: stuck by the row's heartbeat, 700 s old, although the phase is a
        newer version's (no counts to read) and the watcher is at a folder."""
        now = time.time()
        claim = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", now - 900)
        job_runs.progress(self.harbour.path, claim.run_id, "somewhere_new", {"folders": 3}, now - 700)
        whole = {"library": "Harbour", "folder": None, "started": "x", "walking_at": "D:/Photos/Regatta"}
        watcher = FakeWatcher({"running": True, "roots": [], "libraries": {}, "syncing": whole})
        found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertEqual(({"folder": "D:/Photos/Regatta"}, "stuck"), (found["syncing"]["progress"], found["syncing"]["state"]))
        (harbour,) = [each for each in found["libraries"] if each["name"] == "harbour"]
        self.assertEqual([(claim.run_id, "watcher", "stuck")],
                         [(row["run_id"], row["source"], row["state"]) for row in harbour["jobs"]])

    def index_here(self, library, tag, parents, progressed):
        """This process's index of `library` as its queue holds it (IndexQueue.now): run `tag`, queued under `parents`,
        last said how far it got at `progressed`; nothing waits behind it."""
        running = {"run": tag, "parents": parents, "started": job_runs.stamp(progressed - 60), "folders": 1,
                   "name": "Regatta 2019", "percent": 15, "message": "Reading metadata: 15% (120/800)",
                   "progressed": job_runs.stamp(progressed)}
        queue = mock.Mock(library=library.name,
                          now=mock.Mock(return_value={"running": running, "queued": [{"name": "Harbour Walk", "folders": 1}]}))
        return mock.patch.object(self.runtime.index_queues, "values", return_value=[queue])

    def test_the_own_index_before_its_row_is_read_is_under_the_run_that_queued_it(self):
        """#2253: the queue's own index has no row yet (its claim not committed, retried on a busy library, or never
        recorded): it is drawn under the run its queue's tags name, by the rule its claim records its parent by
        (runs.parent_of: the innermost job tag of its library), not at the top level; a tag of another library is none."""
        now = time.time()
        sync = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", now - 600)
        tag = runs.index_tag("harbour", now)
        parents = ["sync:harbour:20260921T151320", runs.job_tag("harbour", sync.run_id), runs.job_tag("regatta", 99)]
        with self.index_here(self.harbour, tag, parents, now):
            found = self.get("/api/activity/now")
        (harbour,) = [each for each in found["libraries"] if each["name"] == "harbour"]
        own = harbour["indexing"]["running"]
        self.assertEqual((tag, None, sync.run_id), (own["run"], own["run_id"], own["parent"]))
        self.assertEqual("running", own["state"])
        self.assertEqual([sync.run_id], [row["run_id"] for row in harbour["jobs"]])
        self.assertEqual([{"name": "Harbour Walk", "folders": 1}], harbour["indexing"]["queued"])

    def test_an_index_here_and_one_in_another_process_each_once_the_stuck_one_said(self):
        """Two indexes of one library: this process's (its row and its queue's memory, one run) and the CLI's (its row
        alone), which said nothing for 12 minutes: stuck, by the server, once (activity.STUCK_AFTER). The own run is
        found by its tag, never by an id a restore can give another row."""
        now = time.time()
        sync = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", now - 900)
        cli_tag, own_tag = runs.index_tag("harbour", now - 800), runs.index_tag("harbour", now)
        cli = job_runs.claim(self.harbour.path, job_runs.INDEX, "harbour", now - 800, changed={"run": cli_tag})
        job_runs.progress(self.harbour.path, cli.run_id, "bake", {"done": 3, "of": 9}, now - 12 * 60)
        own = job_runs.claim(self.harbour.path, job_runs.INDEX, "harbour", now - 60, parent=sync.run_id,
                             changed={"run": own_tag})
        job_runs.progress(self.harbour.path, own.run_id, "metadata", {"done": 120, "of": 800}, now - 3)
        with self.index_here(self.harbour, own_tag, [runs.job_tag("harbour", sync.run_id)], now - 3):
            found = self.get("/api/activity/now")
        (harbour,) = [each for each in found["libraries"] if each["name"] == "harbour"]
        self.assertEqual([(sync.run_id, "running"), (cli.run_id, "stuck")],
                         [(row["run_id"], row["state"]) for row in harbour["jobs"]])
        running = harbour["indexing"]["running"]
        self.assertEqual((own.run_id, sync.run_id, "reading metadata", "running"),
                         (running["run_id"], running["parent"], running["said"], running["state"]))

    def test_a_library_that_cannot_be_read_says_why_and_its_index_here_still_shows(self):
        """A locked library: the other library's runs are listed, and this process's index of it -- memory, no row to
        read -- still shows, under the run its tags name. Its card says why from /api/activity/jobs' `error`
        (tests/test_activity_page_replies.py); Now says it nowhere (#2273: a key no page read)."""
        now = time.time()
        claim = job_runs.claim(self.regatta.path, "snapshots", "regatta", now)
        tag = runs.index_tag("harbour", now)
        real = activity.under_way

        def locked(library):
            if library.name == "harbour":
                raise OSError("the library is locked")
            return real(library)
        with self.index_here(self.harbour, tag, [runs.job_tag("harbour", 7)], now), \
                mock.patch.object(activity, "under_way", locked):
            found = self.get("/api/activity/now")
        by_name = {each["name"]: each for each in found["libraries"]}
        self.assertNotIn("error", by_name["harbour"])
        self.assertEqual((tag, 7), (by_name["harbour"]["indexing"]["running"]["run"],
                                    by_name["harbour"]["indexing"]["running"]["parent"]))
        self.assertNotIn("error", by_name["regatta"])
        self.assertEqual([claim.run_id], [row["run_id"] for row in by_name["regatta"]["jobs"]])

    def test_an_abandoned_run_is_not_running(self):
        """A run whose server was killed, swept abandoned at the next start, is in the timeline, not in Now."""
        killed = job_runs.claim(self.harbour.path, recurring.SYNC, "harbour", time.time() - 3600)
        with mock.patch.object(file_journal, "owner_alive", return_value=False):
            self.assertEqual(1, job_runs.sweep_abandoned(self.harbour.path, time.time()))
        (harbour,) = [each for each in self.get("/api/activity/now")["libraries"] if each["name"] == "harbour"]
        self.assertEqual([], harbour["jobs"])
        self.assertIn(killed.run_id, [entry["id"] for entry in self.get("/api/activity/timeline")["entries"]])

    def test_a_progress_this_version_does_not_know_is_not_read(self):
        """#2206: a row of a newer version (another phase, counts not numbers) is None, never an error."""
        said = {"phase": "walking", "counts": {"folders": 4, "files": 9}, "progressed": "t"}
        self.assertEqual({"phase": sync_service.WALKING, "folders": 4, "files": 9, "progressed": "t"},
                         sync_service.walked(said))
        for odd in (dict(said, phase="somewhere_new"), dict(said, counts={"folders": 4}),
                    dict(said, counts={"folders": "four", "files": 9}), dict(said, counts=None), {}):
            self.assertIsNone(sync_service.walked(odd), odd)

    def test_the_timeline_reads_a_librarys_index_runs_once(self):
        """#2207: the entries and the logs of the runs an index is part of come from one read of the index rows."""
        real = job_runs.runs
        asked = []

        def counting(path, job=None, limit=20, exclude=()):
            if path == self.harbour.path and (job == job_runs.INDEX or (job is None and job_runs.INDEX not in exclude)):
                asked.append((job, limit))
            return real(path, job, limit, exclude)

        with mock.patch.object(job_runs, "runs", counting):
            self.get("/api/activity/timeline")
        self.assertEqual(1, len(asked), asked)


class ScheduledJobs(Base):
    NOON = seconds("2026-09-20 12:00:00")

    def ran(self, library, job, at, outcome, changed=None, note=None, took=60):
        claim = job_runs.claim(library.path, job, library.name, at)
        job_runs.finish(library.path, claim.run_id, outcome, at + took, changed or {}, note)
        return claim.run_id

    def jobs_of(self, found, library):
        entry = [each for each in found["libraries"] if each["name"] == library][0]
        return {job["name"]: job for job in entry["jobs"]}

    def test_each_run_with_its_counts_duration_error_and_tag(self):
        first = self.ran(self.harbour, "snapshots", self.NOON, "done", {"changed": 1, "bytes": 2048}, took=11)
        failed = self.ran(self.harbour, "snapshots", self.NOON + 86400, "failed", {"errors": 1},
                          note="OSError: the disk is full", took=3)
        jobs = self.jobs_of(self.get("/api/activity/jobs"), "harbour")
        snapshot = jobs["snapshots"]
        self.assertEqual("safety", snapshot["reason"])
        self.assertEqual([failed, first], [run["id"] for run in snapshot["runs"]])
        self.assertEqual("OSError: the disk is full", snapshot["runs"][0]["error"])
        self.assertEqual(3, snapshot["runs"][0]["seconds"])
        self.assertEqual({"changed": 1, "bytes": 2048}, snapshot["runs"][1]["changed"])
        self.assertEqual("job:harbour:%d" % failed, snapshot["runs"][0]["run"])
        self.assertTrue(snapshot["failing"], "a failed last run is flagged")
        self.assertEqual([], jobs["sync"]["runs"])
        self.assertFalse(jobs["sync"]["failing"])
        # The other library's runs are its own.
        self.assertEqual([], self.jobs_of(self.get("/api/activity/jobs"), "regatta")["snapshots"]["runs"])

    def test_how_many_runs_are_listed(self):
        for day in range(4):
            self.ran(self.harbour, "snapshots", self.NOON + day * 86400, "done")
        jobs = self.jobs_of(self.client().get("/api/activity/jobs?runs=2").get_json(), "harbour")
        self.assertEqual(2, len(jobs["snapshots"]["runs"]))


class RunNow(Base):
    def runner(self, gate=None):
        registry = recurring.Registry()
        self.calls = []

        @registry.job("tidy", recurring.DAILY, reason=recurring.SAFETY)
        def _tidy(library, run):
            self.calls.append((library.name, run.forced))
            if gate is not None:
                gate.wait(10)
            return Result(attempted=2, changed=2)

        runner = recurring.Runner(lambda: [self.harbour, self.regatta], registry=registry)
        return runner

    def test_run_now_runs_the_job_for_the_library_and_says_its_run(self):
        runner = self.runner()
        client = self.client(tasks=[("recurring jobs", runner)])
        reply = client.post("/api/activity/jobs/run", json={"job": "tidy", "library": "regatta"}).get_json()
        self.assertTrue(reply["started"], reply)
        self.assertEqual("job:regatta:%d" % reply["run_id"], reply["run"])
        wait_until(lambda: [run.outcome for run in job_runs.runs(self.regatta.path, "tidy")][:1] not in ([], ["running"]),
                   what="the tidy run to end")
        self.assertEqual([("regatta", True)], self.calls)
        self.assertEqual({"attempted": 2, "changed": 2, "skipped": 0, "errors": 0},
                         job_runs.runs(self.regatta.path, "tidy")[0].changed)
        self.assertEqual([], job_runs.runs(self.harbour.path, "tidy"))

    def test_a_job_already_running_is_not_started_twice(self):
        gate = threading.Event()
        runner = self.runner(gate)
        client = self.client(tasks=[("recurring jobs", runner)])
        try:
            first = client.post("/api/activity/jobs/run", json={"job": "tidy", "library": "harbour"}).get_json()
            second = client.post("/api/activity/jobs/run", json={"job": "tidy", "library": "harbour"}).get_json()
            self.assertEqual([(first["run_id"], "running")],
                             [(run.id, run.outcome) for run in job_runs.runs(self.harbour.path, "tidy")])
        finally:
            gate.set()
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertIn("under way already", second["why"])

    def test_what_cannot_be_run(self):
        client = self.client(tasks=[("recurring jobs", self.runner())])
        self.assertEqual(404, client.post("/api/activity/jobs/run", json={"job": "nothing", "library": "harbour"}).status_code)
        self.assertEqual(404, client.post("/api/activity/jobs/run", json={"job": "tidy", "library": "moor"}).status_code)
        # A server that runs no recurring jobs runs none now either.
        self.assertEqual(409, self.client().post("/api/activity/jobs/run",
                                                 json={"job": "tidy", "library": "harbour"}).status_code)


class SyncSnapshotsAndTheTimeline(Base):
    def test_the_last_syncs_the_review_count_and_the_roots(self):
        job_runs.record_sync(self.harbour.path, "2026-09-25 08:00:00", whole=True, in_step=True,
                         found={"review_folders": 3, "missing": 0}, changed={"rows": 0})
        job_runs.record_sync(self.harbour.path, "2026-09-26 09:00:00", whole=False, in_step=False,
                         found={"new": 4}, changed={"rows": 2, "queued_folders": 1})
        root = os.path.join(self.home.root, "Photos")
        os.makedirs(root)
        gone = os.path.join(self.home.root, "Unplugged")
        settings.change(self.harbour, {"library.roots": "\n".join([root, gone])})
        watcher = FakeWatcher({"running": True, "syncing": None,
                               "roots": [{"path": root, "watched": True, "absent": False, "libraries": ["harbour"]}],
                               "libraries": {"harbour": {"last_event": "2026-09-26 09:59:00", "pending_folders": 0,
                                                         "whole_pending": False, "last_sync": None,
                                                         "not_watched": False}}})
        found = self.get("/api/activity/sync", tasks=[("folder watcher", watcher)])
        harbour = [each for each in found["libraries"] if each["name"] == "harbour"][0]
        self.assertEqual(3, harbour["review_folders"])
        self.assertEqual("2026-09-25 08:00:00", harbour["last_whole"]["started"])
        self.assertEqual("sync:harbour:20260925T080000", harbour["last_whole"]["run"])
        self.assertEqual({"rows": 2, "queued_folders": 1}, harbour["last_folder"]["changed"])
        self.assertIsNotNone(harbour["last_in_step"])
        self.assertEqual([{"path": root, "there": True, "answering": True, "watched": True},
                          {"path": gone, "there": False, "answering": True, "watched": False}], harbour["roots"])
        self.assertEqual("2026-09-26 09:59:00", harbour["watcher"]["last_event"])
        self.assertEqual("http://localhost:8080/harbour/?review=1", harbour["review_url"])
        regatta = [each for each in found["libraries"] if each["name"] == "regatta"][0]
        self.assertIsNone(regatta["last_whole"])
        self.assertIsNone(regatta["review_folders"])

    def test_a_root_spelled_another_way_is_the_watched_folder(self):
        """The settings keep a root as it was typed -- forward slashes, a trailing
        separator -- and the watcher as stored: one folder, compared by paths.key."""
        root = os.path.join(self.home.root, "Photos")
        os.makedirs(root)
        typed = root.replace("\\", "/") + "/"
        settings.change(self.harbour, {"library.roots": typed})
        watcher = FakeWatcher({"running": True, "syncing": None, "libraries": {},
                               "roots": [{"path": root, "watched": True, "absent": False, "libraries": ["harbour"]}]})
        found = self.get("/api/activity/sync", tasks=[("folder watcher", watcher)])
        harbour = [each for each in found["libraries"] if each["name"] == "harbour"][0]
        self.assertEqual([True], [each["watched"] for each in harbour["roots"]])

    def test_snapshots_by_kind_age_and_size(self):
        daily = os.path.join(self.harbour.snapshots, "daily")
        os.makedirs(daily)
        with open(os.path.join(daily, "harbour-20260925_090000.db"), "wb") as handle:
            handle.write(b"x" * 1000)
        with open(os.path.join(daily, "harbour-20260926_090000.db"), "wb") as handle:
            handle.write(b"x" * 1500)
        found = self.get("/api/activity/snapshots")
        harbour = [each for each in found["libraries"] if each["name"] == "harbour"][0]
        self.assertEqual(["daily/20260926_090000", "daily/20260925_090000"], [s["name"] for s in harbour["snapshots"]])
        self.assertEqual(2500, harbour["bytes"])
        self.assertEqual(2500, found["bytes"])
        self.assertEqual(snapshots.disk(self.harbour.path), harbour["bytes"])

    def test_one_timeline_newest_first_a_syncs_change_its_syncs(self):
        claim = job_runs.claim(self.harbour.path, "snapshots", "harbour", seconds("2026-09-20 12:00:00"))
        job_runs.finish(self.harbour.path, claim.run_id, "failed", seconds("2026-09-20 12:00:05"), {"errors": 1},
                        "OSError: full")
        changed = settings.change(self.regatta, {"candidates.tags": "harbour, regatta"})
        job_runs.record_sync(self.harbour.path, "2026-09-21 08:00:00", whole=True, in_step=True,
                         found={"new": 0, "missing": 2}, changed={"rows": 0, "queued_folders": 0},
                         change_id=None)
        found = self.get("/api/activity/timeline")
        times = [entry["time"] for entry in found["entries"]]
        self.assertEqual(sorted(times, reverse=True), times, "newest first")
        kinds = [(entry["kind"], entry["library"]) for entry in found["entries"]]
        # Made now, as each library's stamp of its settings was: newer than the rest.
        self.assertEqual({("change", "regatta"), ("change", "harbour")}, set(kinds[:3]))
        self.assertIn(("sync", "harbour"), kinds)
        self.assertIn(("job", "harbour"), kinds)
        self.assertLess(kinds.index(("sync", "harbour")), kinds.index(("job", "harbour")))
        job = [entry for entry in found["entries"] if entry["kind"] == "job"][0]
        self.assertEqual("OSError: full", job["error"])
        self.assertEqual(5, job["seconds"])
        self.assertEqual("job:harbour:%d" % claim.run_id, job["run"])
        change = [entry for entry in found["entries"] if entry["kind"] == "change" and entry["library"] == "regatta"
                  and entry["id"] == changed.details["change"]][0]
        self.assertEqual("change settings", change["what"])
        sync = [entry for entry in found["entries"] if entry["kind"] == "sync"][0]
        self.assertEqual(2, sync["counts"]["found_missing"])
        self.assertEqual(1, len(self.client().get("/api/activity/timeline?limit=1").get_json()["entries"]))

    def test_a_syncs_logs_include_the_indexing_it_queued(self):
        """A sync queues its new files' folders; the run of the indexer that indexes them
        is the sync's too: it carries the sync's tag, its indexer is told both, and the
        sync's "Logs for this run" includes the indexer's own log."""
        started = "2026-09-26 09:00:00"
        sync_tag = runs.sync_tag("harbour", started)
        job_runs.record_sync(self.harbour.path, started, whole=True, in_step=True, found={}, changed={})
        queue = self.runtime.index_queues.of(self.harbour)
        seen = {}

        def index(folder, report):
            seen["tags"] = runs.current()
            return Result(attempted=1, changed=1)

        with runs.running(sync_tag):
            queue.start([self.home.root], index, together=True)
        queue.wait()
        self.assertEqual(2, len(seen["tags"]), seen)
        self.assertEqual(sync_tag, seen["tags"][0])
        index_tag = seen["tags"][1]
        self.assertTrue(index_tag.startswith("index:harbour:"))
        entries = self.get("/api/activity/timeline")["entries"]
        sync = [entry for entry in entries if entry["kind"] == "sync"][0]
        self.assertEqual([logs.SERVER_LOG, logs.run_log("indexer", index_tag)], sync["logs"])
        harbour = [each for each in self.get("/api/activity/sync")["libraries"] if each["name"] == "harbour"][0]
        self.assertEqual([logs.SERVER_LOG, logs.run_log("indexer", index_tag)], harbour["last_whole"]["logs"])

    def test_every_run_says_which_logs_hold_its_lines(self):
        claim = job_runs.claim(self.harbour.path, "snapshots", "harbour", seconds("2026-09-20 12:00:00"))
        job_runs.finish(self.harbour.path, claim.run_id, "done", seconds("2026-09-20 12:00:05"), {})
        job_runs.record_sync(self.harbour.path, "2026-09-21 08:00:00", whole=True, in_step=True, found={}, changed={})
        entries = self.get("/api/activity/timeline")["entries"]
        for entry in entries:
            self.assertEqual([logs.SERVER_LOG] if entry["run"] else [], entry["logs"], entry)
        jobs = self.get("/api/activity/jobs")["libraries"][0]["jobs"]
        run = [job for job in jobs if job["name"] == "snapshots"][0]["runs"][0]
        self.assertEqual([logs.SERVER_LOG], run["logs"])
        harbour = [each for each in self.get("/api/activity/sync")["libraries"] if each["name"] == "harbour"][0]
        self.assertEqual([logs.SERVER_LOG], harbour["last_whole"]["logs"])
        self.assertEqual(logs.SERVER_LOG, self.get("/api/activity/logs")["server"])


def a_line(stamp, level, message, logger="tagpup.web", run_tags=(), thread="MainThread"):
    tag = " [run %s]" % ",".join(run_tags) if run_tags else ""
    return "%s,123 [%s] %s %s%s - %s\n" % (stamp, level, thread, logger, tag, message)


class Logs(Base):
    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.home.data, "logs")
        os.makedirs(self.folder)
        lines = [
            a_line("2026-09-26 09:00:00", "INFO", "started"),
            a_line("2026-09-26 09:00:01", "WARNING", "slow: GET /api/tags took 1.20s", logger="tagpup.requests"),
            a_line("2026-09-26 09:00:02", "INFO", "Running the job snapshots for harbour", run_tags=["job:harbour:7"],
                   thread="Thread-5 (run_job)"),
            a_line("2026-09-26 09:00:03", "ERROR", "The job snapshots for harbour failed", run_tags=["job:harbour:7"]),
            "Traceback (most recent call last):\n",
            "  File \"x.py\", line 1, in <module>\n",
            "OSError: the disk is full\n",
            a_line("2026-09-26 09:00:04", "INFO", "Synced", run_tags=["job:harbour:8", "sync:harbour:20260926T090004"]),
        ]
        self.write("tagpup_web.log", "".join(lines))
        self.write("tagpup_web.log.1", a_line("2026-09-25 09:00:00", "INFO", "older"))
        self.write("tagpup_web.console.log", "Traceback (most recent call last):\nKeyboardInterrupt\n")
        self.write(INDEXED, a_line("2026-09-26 08:00:00", "INFO", "indexing"))
        self.write("run_tests-20260926-080000.log", "FAIL: test_x\n")
        self.client_ = self.client()

    def write(self, name, text):
        with open(os.path.join(self.folder, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def lines(self, query=""):
        reply = self.client_.get("/api/activity/logs/tagpup_web.log" + query)
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()

    def test_the_logs_by_source_with_their_rotated_copies(self):
        found = self.get("/api/activity/logs")
        by_name = {entry["name"]: entry for entry in found["logs"]}
        self.assertEqual({"tagpup_web.log", "tagpup_web.console.log", INDEXED, "run_tests-20260926-080000.log"},
                         set(by_name))
        self.assertEqual("Web server", by_name["tagpup_web.log"]["source"])
        self.assertEqual("Indexer (harbour, 09-26 08:00)", by_name[INDEXED]["source"])
        self.assertEqual(["tagpup_web.log.1"], [each["name"] for each in by_name["tagpup_web.log"]["rotated"]])

    def test_the_installed_servers_start_up_output_is_said_in_words(self):
        """What a server an install started printed before its own log, in the file the launcher writes it to, is a
        program's own log, said in words, not by its file's name (#2291: the launcher named the file and the listing
        matched its own spelling of it, so a rename of one listed it by its file's name; both read logs.STARTED_LOG)."""
        name = os.path.basename(launcher.started_log(self.home.root))
        self.assertEqual(logs.STARTED_LOG, name)
        self.write(name, "Traceback (most recent call last):\nOSError: port 8080 is held\n")
        (entry,) = [each for each in self.get("/api/activity/logs")["logs"] if each["name"] == name]
        self.assertEqual(("system", "Server start-up output (installed copy)"), (entry["group"], entry["source"]))

    def test_a_programs_own_logs_apart_from_the_runs_and_the_runs_by_library_and_kind(self):
        """The owner tells what a file is: a program's own log, or a run's, of which library and kind (the page filters
        the runs' logs by them, from this one listing)."""
        by_name = {entry["name"]: entry for entry in self.get("/api/activity/logs")["logs"]}
        self.assertEqual(("system", None, None), self.group(by_name["tagpup_web.log"]))
        self.assertEqual(("system", None, None), self.group(by_name["tagpup_web.console.log"]))
        self.assertEqual(("run", "indexer", "harbour"), self.group(by_name[INDEXED]))
        self.assertEqual(("run", "run_tests", None), self.group(by_name["run_tests-20260926-080000.log"]))
        self.assertEqual(logs.KEEP_RUN_LOGS, self.get("/api/activity/logs")["keep_runs"])
        # Each kind of run the listing holds, in the words the page's Kind filter offers (it holds no copy).
        self.assertEqual({"indexer": "Indexer runs", "run_tests": "Test runs"}, self.get("/api/activity/logs")["kinds"])

    def group(self, entry):
        return entry["group"], entry["kind"], entry["library"]

    def ran(self, library, started, ended, n=1):
        """A run of the indexer's log, as it is left: named for when it `started`, last written when it `ended`
        ("HH:MM" on 2026-09-26, local time, as runs.index_tag names it). Its file name."""
        name = "indexer-%s-20260926T%s00-4242-%d.log" % (library, started.replace(":", ""), n)
        self.write(name, a_line("2026-09-26 %s:00" % ended, "INFO", "indexing"))
        when = time.mktime(time.strptime("2026-09-26 " + ended, "%Y-%m-%d %H:%M"))
        os.utime(os.path.join(self.folder, name), (when, when))
        return name

    def test_a_runs_log_that_is_not_there_says_why(self):
        """A run's log removed as the server started (five newer of its library and kind kept) says so; one its run
        has not written yet says that, never that it was removed."""
        for minute in range(1, logs.KEEP_RUN_LOGS + 1):
            self.ran("harbour", "09:%02d" % minute, "09:%02d" % (minute + 1))
        removed = self.client_.get("/api/activity/logs/indexer-harbour-20260926T070000-4242-1.log")
        self.assertEqual(404, removed.status_code)
        self.assertTrue(removed.get_json()["removed"])
        self.assertIn("removed", removed.get_json()["error"])
        unwritten = self.client_.get("/api/activity/logs/indexer-harbour-20260926T100000-4242-2.log").get_json()
        self.assertFalse(unwritten["removed"])
        self.assertIn("no log of its own", unwritten["error"])
        self.assertFalse(self.client_.get("/api/activity/logs/nothing.log").get_json()["removed"])

    def test_a_short_run_beside_a_long_one_removed_at_the_start_says_it_was_removed(self):
        """Runs overlapping: A indexed 10:00-12:00 while B..F ran 10:30-11:05; the server restarting at 12:30 removes
        B, the one last written longest ago -- not the one that started first -- and the page asking for B's log is told
        it was removed, by the same ordering, not that B wrote none. A run starting after them has written none yet."""
        self.ran("regatta", "10:00", "12:00")
        short = [self.ran("regatta", started, ended, n) for n, (started, ended) in enumerate(
            [("10:30", "10:35"), ("10:40", "10:45"), ("10:50", "10:55"), ("10:55", "11:00"), ("11:00", "11:05")], 2)]
        self.assertEqual(1, logs.retire_run_logs())
        self.assertFalse(os.path.exists(os.path.join(self.folder, short[0])))
        reply = self.client_.get("/api/activity/logs/" + short[0])
        self.assertEqual(404, reply.status_code)
        self.assertTrue(reply.get_json()["removed"], reply.get_json())
        later = self.client_.get("/api/activity/logs/indexer-regatta-20260926T123000-4242-9.log").get_json()
        self.assertFalse(later["removed"], later)

    def test_records_newest_first_parsed_with_their_tracebacks(self):
        records = self.lines()["records"]
        self.assertEqual(["Synced", "The job snapshots for harbour failed"], [r["message"].split("\n")[0] for r in records[:2]])
        failed = records[1]
        self.assertEqual("ERROR", failed["level"])
        self.assertEqual("tagpup.web", failed["logger"])
        self.assertIn("OSError: the disk is full", failed["message"])
        self.assertEqual("2026-09-26 09:00:03", failed["time"])
        running = records[2]
        self.assertEqual("Thread-5 (run_job)", running["thread"])
        self.assertEqual(["job:harbour:7"], running["runs"])

    def test_filtered_by_level_text_and_run(self):
        warned = self.lines("?level=WARNING")["records"]
        self.assertEqual(["ERROR", "WARNING"], [r["level"] for r in warned])
        self.assertEqual(["slow: GET /api/tags took 1.20s"], [r["message"] for r in self.lines("?text=API/TAGS")["records"]])
        run = self.lines("?run=job:harbour:7")["records"]
        self.assertEqual(2, len(run))
        nested = self.lines("?run=sync:harbour:20260926T090004")["records"]
        self.assertEqual(["Synced"], [r["message"] for r in nested])
        self.assertEqual(400, self.client_.get("/api/activity/logs/tagpup_web.log?level=LOUD").status_code)
        self.assertEqual(400, self.client_.get("/api/activity/logs/tagpup_web.log?run=nonsense").status_code)

    def test_older_and_newer_lines_page_by_offset(self):
        first = self.lines("?limit=2")
        self.assertEqual(2, len(first["records"]))
        self.assertFalse(first["complete"])
        older = self.lines("?limit=10&before=%d" % first["start"])
        self.assertEqual(["started", "slow: GET /api/tags took 1.20s", "Running the job snapshots for harbour"],
                         [r["message"] for r in reversed(older["records"])])
        self.assertTrue(older["complete"])
        # Followed: only what was written since.
        self.assertEqual([], self.lines("?after=%d" % first["end"])["records"])
        with open(os.path.join(self.folder, "tagpup_web.log"), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(a_line("2026-09-26 09:00:09", "WARNING", "new"))
        newer = self.lines("?after=%d" % first["end"])
        self.assertEqual(["new"], [r["message"] for r in newer["records"]])
        # A file smaller than where the page read to has rotated: read from its end again.
        rotated = self.lines("?after=%d" % (first["end"] * 10))
        self.assertTrue(rotated["rotated"])
        self.assertEqual("new", rotated["records"][0]["message"])

    def test_a_log_without_the_format_is_one_record_a_line_at_any_level(self):
        reply = self.client_.get("/api/activity/logs/tagpup_web.console.log?level=ERROR").get_json()
        self.assertEqual(["KeyboardInterrupt", "Traceback (most recent call last):"],
                         [r["message"] for r in reply["records"]])
        self.assertIsNone(reply["records"][0]["level"])

    def test_a_big_log_is_read_from_its_end_and_no_further(self):
        line = a_line("2026-09-26 09:00:00", "INFO", "x" * 200)
        self.write("big.log", line * 20000 + a_line("2026-09-26 10:00:00", "WARNING", "the last"))
        size = os.path.getsize(os.path.join(self.folder, "big.log"))
        self.assertGreater(size, 4 * 1024 * 1024)
        found = self.client_.get("/api/activity/logs/big.log?level=WARNING").get_json()
        self.assertEqual(["the last"], [r["message"] for r in found["records"]])
        self.assertLessEqual(found["looked_at"], logs.READ_AT_MOST, "a read looked at more than its bound")
        self.assertFalse(found["complete"])
        raw = self.client_.get("/api/activity/logs/big.log/raw")
        self.assertLessEqual(len(raw.data), logs.RAW_AT_MOST)
        self.assertTrue(raw.get_data(as_text=True).endswith("the last\n"))
        self.assertTrue(raw.get_data(as_text=True).startswith("2026-09-26"), "the raw view starts at a line")

    def test_download_is_the_whole_file_and_nothing_else_is_reached(self):
        reply = self.client_.get("/api/activity/logs/tagpup_web.log/download")
        self.assertEqual(200, reply.status_code)
        self.assertIn("attachment", reply.headers["Content-Disposition"])
        with open(os.path.join(self.folder, "tagpup_web.log"), "rb") as handle:
            self.assertEqual(handle.read(), reply.data)
        reply.close()
        with open(os.path.join(self.home.data, "secret.log"), "w", encoding="utf-8") as handle:
            handle.write("not a log of data/logs")
        for name in ("..%2Fsecret.log", "..\\secret.log", "secret.txt", "nothing.log"):
            for suffix in ("", "/raw", "/download"):
                self.assertEqual(404, self.client_.get("/api/activity/logs/%s%s" % (name, suffix)).status_code,
                                 name + suffix)


#: A run of the indexer's own log, as tagpup.logs.run_log names it.
INDEXED = "indexer-harbour-20260926T080000-4242-1.log"


class ASyncsLogSaysWhatItDid(Base):
    """A finished sync's "Logs for this run" (#1914, #1915): the sync writes a line as it starts and one as it ends into
    the server's log, with its run's tag, so the log the timeline names for it, read for its tag, shows them -- while it
    runs, the line that it began; a failed one, why, in the words its run's failure is said in."""

    def setUp(self):
        super().setUp()
        root = logging.getLogger()
        level = root.level
        path = logs.to_file(logs.SERVER_PROGRAM)
        handler = [each for each in root.handlers if getattr(each, "baseFilename", None) == path][0]
        self.addCleanup(lambda: (root.removeHandler(handler), handler.close(), root.setLevel(level)))
        self.client_ = self.client()

    def lines_of(self, name, run):
        reply = self.client_.get("/api/activity/logs/%s?run=%s" % (name, run))
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return [record["message"] for record in reversed(reply.get_json()["records"])]

    def test_a_finished_sync_s_log_shows_what_it_looked_at_found_and_how_it_ended(self):
        seen = {}
        real = sync_service._sync

        def watched(*args):
            # Clicked while it runs: the line that it began is there already.
            seen["while"] = self.lines_of(logs.SERVER_LOG, runs.current()[-1])
            return real(*args)

        with mock.patch.object(sync_service, "_sync", side_effect=watched):
            done = sync_service.sync(self.harbour, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(["Sync of the whole library began."], seen["while"])
        entry = [each for each in self.get("/api/activity/timeline")["entries"] if (each["run"] or "").startswith("sync:")][0]
        self.assertEqual([logs.SERVER_LOG], entry["logs"])
        said = self.lines_of(entry["logs"][0], entry["run"])
        self.assertEqual(2, len(said), said)
        self.assertRegex(said[1], r"^Sync of the whole library ended in \d+ s: looked at 0 folder\(s\) and 0 photo "
                                  r"file\(s\), found nothing to do; changed 0 row\(s\), queued 0 folder\(s\) to index; "
                                  r"in step\.$")
        self.assertEqual([], self.lines_of(logs.SERVER_LOG, runs.sync_tag("regatta", "2026-09-26 09:00:00")))

    def test_a_failed_sync_says_why_as_its_failure_is_said(self):
        seen = {}

        def share_away(library, folder, apply, exiftool_path, queue, roots, ignored, started):
            seen["run"] = runs.current()[-1]
            result = Result()
            result.fail("walking the folders", "the share did not answer in 10 s")
            result.details.update(counts={}, in_step=False, **{job_runs_service.FAILURE: job_runs_service.SHARE})
            return result

        with mock.patch.object(sync_service, "_sync", side_effect=share_away):
            failed = sync_service.sync(self.harbour, apply=True)
        found = job_runs_service.failure(result=failed)
        reply = self.client_.get("/api/activity/logs/%s?run=%s&level=WARNING" % (logs.SERVER_LOG, seen["run"]))
        records = reply.get_json()["records"]
        self.assertEqual(1, len(records))
        self.assertRegex(records[0]["message"], r"^Sync of the whole library failed after \d+ s: ")
        self.assertTrue(records[0]["message"].endswith(": %s %s" % (job_runs_service.FAILURES[job_runs_service.SHARE][0],
                                                                   found.sentence)), records[0]["message"])

    def test_a_sync_that_raised_says_so_and_raises(self):
        with mock.patch.object(sync_service, "_sync", side_effect=OSError("the disk is full")), \
                self.assertRaises(OSError):
            sync_service.sync(self.harbour, apply=True)
        with open(os.path.join(self.home.data, "logs", logs.SERVER_LOG), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(" s: %s OSError: the disk is full" % job_runs_service.FAILURES[job_runs_service.ERROR][0], text)
        self.assertIn("[WARNING]", text)


class TheWatcherSays(unittest.TestCase):
    """The watcher's own account of what it watches (tagpup.jobs.watching.Watcher.status)."""

    def test_its_roots_when_it_last_heard_and_its_last_sync(self):
        from tagpup.files import images
        from tagpup.jobs import watching
        home = own_home.for_test(self, prefix="watcher_status_")
        root = os.path.join(home.root, "Photos")
        os.makedirs(os.path.join(root, "Regatta"))

        class Stand:
            name = key = "harbour"

        synced = []
        watcher = watching.Watcher(lambda: [Stand()], lambda library: [root],
                                   lambda library, folder: synced.append(folder) or Result(changed=1),
                                   images.is_photo, debounce=0.3, recheck=0.2, tick=0.05)
        self.addCleanup(watcher.stop, 10)
        watcher.start()
        wait_until(lambda: synced, what="the catch-up")
        with open(os.path.join(root, "Regatta", "new.jpg"), "wb") as handle:
            handle.write(b"\xff\xd8\xff")
        wait_until(lambda: len(synced) >= 2, what="the folder's sync")
        # The fake sync appends inside the sync; the watcher clears `syncing` after it returns (#2257).
        wait_until(lambda: watcher.status()["syncing"] is None, what="the watcher to say no sync is under way")
        found = watcher.status()
        self.assertTrue(found["running"])
        self.assertEqual([{"path": root, "watched": True, "absent": False, "libraries": ["harbour"]}],
                         [dict(each, path=each["path"]) for each in found["roots"]])
        harbour = found["libraries"]["harbour"]
        self.assertIsNotNone(harbour["last_event"])
        self.assertEqual(1, harbour["last_sync"]["changed"])
        self.assertIsNone(found["syncing"])


class TheActivityPageIsNotSomebodyUsingTheApp(unittest.TestCase):
    """An open Activity page polls every few seconds; an update waits for a quiet moment
    (no request for two minutes). Its reads must not hold the update back."""

    def test_its_reads_leave_the_quiet_moment_and_an_action_does_not(self):
        clock = [1000.0]
        lifecycle = Lifecycle(clock=lambda: clock[0], work=lambda: [], runtime=runtimes.Runtime())
        app = lambda environ, start_response: start_response("200 OK", []) or [b""]  # noqa: E731
        gate = lifecycle.wrap(app)

        def ask(method, path):
            body = gate({"REQUEST_METHOD": method, "PATH_INFO": path}, lambda *a: None)
            list(body)

        ask("GET", "/api/activity/now")
        ask("GET", "/activity/main.js")
        clock[0] += 5
        # Nothing but the page's reads: the quiet moment is there.
        self.assertTrue(lifecycle.drain(seconds=1, quiet=120, lease=60)["drained"])
        lifecycle.resume()
        ask("POST", "/api/activity/jobs/run")
        clock[0] += 5
        self.assertFalse(lifecycle.drain(seconds=1, quiet=120, lease=60)["drained"])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()

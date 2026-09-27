"""The Activity page's routes (tagpup.web.activity_routes; docs/ARCHITECTURE.md, phase 8.5):
this PC only; what runs now; each job's runs, failures kept in view until a later success,
and Run now; the syncs, the watcher, the snapshots; one timeline of what was done; and the
logs, read from the end and never whole, by level, text and run.

Every library is made in a home of the test's own; the runs, syncs and changes are
seeded as the store records them (tagpup.store.job_runs, sync_runs; a journaled change of
the settings), never through what is under test.
"""
import json
import logging
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import logs  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import job_runs, snapshots, sync_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

ELSEWHERE = {"REMOTE_ADDR": "192.168.1.20"}
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
        self.harbour = self.made("harbour")
        self.regatta = self.made("regatta")

    def made(self, name):
        path = self.home.library(name + ".db")
        library_actions.create(path)
        return Library(path)

    def client(self, tasks=(), kind="tagpup"):
        background = runtimes.Background(list(tasks))
        app = web.create_app(kind, ports=PORTS, lifecycle=Lifecycle(background=background))
        app.testing = True
        return app.test_client()

    def get(self, url, **kwargs):
        reply = self.client(**kwargs).get(url)
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()


class ThisPcOnly(Base):
    """The page and its routes answer this PC alone, whatever the server listens on."""

    def test_a_request_from_another_address_is_refused(self):
        client = self.client()
        for url in ("/api/activity/now", "/api/activity/jobs", "/api/activity/logs", "/activity/",
                    "/api/activity/logs/tagpup_web.log/download"):
            self.assertEqual(403, client.get(url, environ_base=ELSEWHERE).status_code, url)
        self.assertEqual(403, client.post("/api/activity/jobs/run", json={"job": "sync", "library": "harbour"},
                                          environ_base=ELSEWHERE).status_code)

    def test_this_pc_by_either_address_is_answered(self):
        client = self.client()
        for address in ("127.0.0.1", "::1"):
            self.assertEqual(200, client.get("/api/activity/now", environ_base={"REMOTE_ADDR": address}).status_code)

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
        app = web.create_app("tagpup", startup=self.harbour, ports=PORTS)
        app.testing = True
        client = app.test_client()
        self.assertEqual(200, client.get("/activity/").status_code)
        self.assertEqual(200, client.get("/api/activity/now").status_code)


class Now(Base):
    def test_the_index_queue_the_running_jobs_and_suggest_by_library(self):
        release = threading.Event()
        queue = indexing_jobs.queue_for(self.harbour)
        self.addCleanup(indexing_jobs.forget, self.harbour)

        def index(folder, cluster, report):
            report("Indexing: 40% (4/10)", 40)
            release.wait(10)
            return Result(attempted=1, changed=1)

        folder_a = os.path.join(self.home.root, "Regatta 2019")
        folder_b = os.path.join(self.home.root, "Harbour Walk")
        os.makedirs(folder_a)
        os.makedirs(folder_b)
        queue.start([folder_a, folder_b], index)
        deadline = time.time() + 10
        while queue.now()["running"] is None or queue.now()["running"]["percent"] != 40:
            self.assertLess(time.time(), deadline)
            time.sleep(0.01)
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
                           "run": "job:regatta:%d" % claim.run_id}], by_name["regatta"]["jobs"])
        self.assertIsNone(by_name["regatta"]["indexing"]["running"])
        self.assertTrue(found["server"]["taking_work"])

    def test_the_supervisors_state_is_shown_without_its_token(self):
        state = {"pid": 4242, "state": "running", "since": "2026-09-26 09:00:00", "version": "20260926-090000-abc1234",
                 "server_token": "a-secret", "update_refused": None, "crashes": 1}
        with open(os.path.join(self.home.data, "supervisor.json"), "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        found = self.get("/api/activity/now")["supervisor"]
        self.assertEqual("running", found["state"])
        self.assertEqual(1, found["crashes"])
        self.assertNotIn("server_token", found)
        self.assertNotIn("a-secret", json.dumps(self.get("/api/activity/server")))

    def test_the_watchers_sync_under_way(self):
        watcher = FakeWatcher({"running": True, "roots": [], "libraries": {},
                               "syncing": {"library": "harbour", "folder": "D:/Photos/Regatta", "started": "x"}})
        found = self.get("/api/activity/now", tasks=[("folder watcher", watcher)])
        self.assertTrue(found["watching"])
        self.assertEqual("harbour", found["syncing"]["library"])


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

    def test_a_failure_stays_flagged_until_a_later_run_is_done(self):
        self.ran(self.harbour, "sync", self.NOON, "failed", note="boom")
        claim = job_runs.claim(self.harbour.path, "sync", "harbour", self.NOON + 7200)
        self.assertTrue(self.jobs_of(self.get("/api/activity/jobs"), "harbour")["sync"]["failing"],
                        "a run under way is not yet a success")
        job_runs.finish(self.harbour.path, claim.run_id, "done", self.NOON + 7300, {"changed": 0})
        self.assertFalse(self.jobs_of(self.get("/api/activity/jobs"), "harbour")["sync"]["failing"])

    def test_how_many_runs_are_listed(self):
        for day in range(4):
            self.ran(self.harbour, "prune-journal", self.NOON + day * 86400, "done")
        jobs = self.jobs_of(self.client().get("/api/activity/jobs?runs=2").get_json(), "harbour")
        self.assertEqual(2, len(jobs["prune-journal"]["runs"]))


class RunNow(Base):
    def runner(self, gate=None):
        registry = recurring.Registry()
        self.calls = []

        @registry.job("tidy", recurring.DAILY, reason=recurring.RETENTION)
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
        deadline = time.time() + 10
        while not job_runs.runs(self.regatta.path, "tidy") or job_runs.runs(self.regatta.path, "tidy")[0].outcome == "running":
            self.assertLess(time.time(), deadline)
            time.sleep(0.02)
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
            self.assertEqual([{"job": "tidy", "library": "harbour", "run_id": first["run_id"]}],
                             [{k: each[k] for k in ("job", "library", "run_id")} for each in runner.status()])
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
        sync_runs.record(self.harbour.path, "2026-09-25 08:00:00", whole=True, in_step=True,
                         found={"review_folders": 3, "missing": 0}, changed={"rows": 0})
        sync_runs.record(self.harbour.path, "2026-09-26 09:00:00", whole=False, in_step=False,
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
        self.assertEqual([{"path": root, "there": True, "watched": True},
                          {"path": gone, "there": False, "watched": False}], harbour["roots"])
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
        sync_runs.record(self.harbour.path, "2026-09-21 08:00:00", whole=True, in_step=True,
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

    def test_the_index_queues_runs_are_in_the_timeline(self):
        queue = indexing_jobs.queue_for(self.harbour)
        self.addCleanup(indexing_jobs.forget, self.harbour)
        queue.start([self.home.root], lambda folder, cluster, report: Result(attempted=1, changed=1))
        queue.wait()
        entries = [e for e in self.get("/api/activity/timeline")["entries"] if e["kind"] == "index"]
        self.assertEqual(1, len(entries))
        self.assertEqual("completed", entries[0]["outcome"])
        self.assertTrue(entries[0]["run"].startswith("index:harbour:"))


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
        self.write("indexer-harbour.log", a_line("2026-09-26 08:00:00", "INFO", "indexing"))
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
        self.assertEqual({"tagpup_web.log", "tagpup_web.console.log", "indexer-harbour.log"}, set(by_name))
        self.assertEqual("Web server", by_name["tagpup_web.log"]["source"])
        self.assertEqual("Indexer (harbour)", by_name["indexer-harbour.log"]["source"])
        self.assertEqual(["tagpup_web.log.1"], [each["name"] for each in by_name["tagpup_web.log"]["rotated"]])

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
        deadline = time.time() + 20
        while not synced:
            self.assertLess(time.time(), deadline)
            time.sleep(0.02)
        with open(os.path.join(root, "Regatta", "new.jpg"), "wb") as handle:
            handle.write(b"\xff\xd8\xff")
        while len(synced) < 2:
            self.assertLess(time.time(), deadline)
            time.sleep(0.02)
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
        lifecycle = Lifecycle(clock=lambda: clock[0], work=lambda: [])
        app = lambda environ, start_response: start_response("200 OK", []) or [b""]  # noqa: E731
        gate = lifecycle.wrap(app)

        def ask(method, path):
            body = gate({"REQUEST_METHOD": method, "PATH_INFO": path}, lambda *a: None)
            list(body)

        ask("GET", "/api/activity/now")
        ask("GET", "/activity/main.js")
        clock[0] += 5
        # Nothing but the page's reads: the quiet moment is there.
        self.assertTrue(lifecycle.drain(seconds=1, quiet=120)["drained"])
        lifecycle.resume()
        ask("POST", "/api/activity/jobs/run")
        clock[0] += 5
        self.assertFalse(lifecycle.drain(seconds=1, quiet=120)["drained"])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()

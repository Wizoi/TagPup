"""The replies the Activity page reads (web/activity/), made by the real routes from libraries seeded through the real
writers, and held in tests/frontend/fixtures/activity.json for the page's test (tests/frontend/activity-page.test.mjs)
to answer from, so the page is held to what the server sends and not to a reply written by hand (docs/OWNERS.md,
contract 22). A reply that differs from the file fails, naming the command that writes it again (page_fixtures). An
index says how far it got as the indexer's lines are read (tagpup.services.indexing.index_folder, the fake indexer of
test_an_index_is_one_run_under_its_sync printing them): no count of its is spelled here. Each scenario is a morning the
owner opens the page on:

    quiet       nothing runs; last night's sync was done.
    long_sync   the watcher's whole sync of harbour walks the share again; this process's own index of what it found
                (its queue's, tagpup.jobs.indexing) runs beneath it, reading metadata, another folder waiting.
    stuck       the CLI's index of lighthouse last said how far it got 12 minutes ago, in its bake (#2210).
    morning     breakwater's share dropped overnight (Needs you, Run it now); jetty's server was killed mid-sync (an
                abandoned run says why); harbour's sync and its index ended (a tree); pier is no library at all. Its
                logs (/api/activity/logs): the web server's and the MCP server's own, the runs' of harbour's and
                lighthouse's indexes, written by tagpup.logs, and a failed test run's (tools/run_tests.py names it);
                the index of the folder that waited wrote none, and reading its log answers why (`missing_log`).

The clocks are the scenarios' own (time.time is the scenario's throughout -- the index queue's thread and the claim
of its run among it -- the journal's clock the day before while each library is made, an index's tag the scenario's
time; the home's folder, the process id in a run's tag and in a log's name (tagpup.logs.run_log) and the count after it
are renumbered), so the file is the same on every run: the test makes it twice, in two homes, and the two are one text.
An index's tag holds the stand-in process id as it is made, not only in the text: the tag is in every line of the
index's log, whose size the logs reply gives, and a process id of 3 or 4 digits made it a byte or two shorter.
Fictional names only.
"""
import itertools
import json
import logging
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402,F401
from waiting import wait_until  # noqa: E402
import page_fixtures  # noqa: E402
from test_a_failed_run_says_what_to_do import NIGHT, Clock, _Libraries  # noqa: E402
from test_activity_routes import FakeWatcher  # noqa: E402
from test_an_index_is_one_run_under_its_sync import FAKE_CLI, ROOT  # noqa: E402

from tagpup import logs  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import dates, progress_line, runs  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity, bake_after_index, indexing, job_runs, sync  # noqa: E402
from tagpup.store import file_journal, journal  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

FIXTURE = page_fixtures.path_of("activity.json")
MINUTE, HOUR = 60, 3600
ROUTES = ("now", "jobs", "timeline", "logs")
#: The process id the fixture holds wherever this process's is spelled.
STAND_IN_PID = "4242"
#: Set to 1 to write the fixture again from the replies, when a change of the server's is meant to change them.


class RepliesForTheActivityPage(_Libraries):
    def library(self, name, **kwargs):
        """A library made the day before the scenarios: the change that stamps its settings is recorded at the journal's
        clock (dates.recorded, time.localtime, which reads no time.time)."""
        day_before = NIGHT - 24 * HOUR
        with mock.patch("time.time", Clock(day_before)), \
                mock.patch.object(journal, "_now", lambda: dates.recorded(day_before)):
            return super().library(name, **kwargs)

    def replies(self, libraries, at, watcher=None, missing=None):
        """What the page asks, as the real routes answer it, at the scenario's clock `at`; and the read of the log
        `missing`, which is not there (`missing_log`)."""
        tasks = [(runtimes.RECURRING_JOBS, recurring.Runner(lambda: libraries, clock=Clock(at)))]
        if watcher is not None:
            tasks.append((runtimes.FOLDER_WATCHER, FakeWatcher(watcher)))
        app = web.create_app("tagpup", runtime=self.runtime,
                             lifecycle=Lifecycle(background=runtimes.Background(tasks), runtime=self.runtime))
        app.testing = True
        client = app.test_client()
        found = {}
        with mock.patch("time.time", Clock(at)):
            for route in ROUTES:
                reply = client.get("/api/activity/" + route)
                self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
                found[route] = reply.get_json()
            if missing:
                reply = client.get("/api/activity/logs/" + missing + "?limit=200&level=INFO")
                self.assertEqual(404, reply.status_code, reply.get_data(as_text=True))
                found["missing_log"] = reply.get_json()
        return found

    def write_log(self, program, tag, message, at):
        """A line logged to the log of `program` (a file name, as tagpup.logs names it) by its writer
        (tagpup.logs.to_file), under the run `tag` (or none), at the scenario's clock; the file last written at `at`."""
        root = logging.getLogger()
        level = root.level
        path = logs.to_file(program)
        (handler,) = [each for each in root.handlers if getattr(each, "baseFilename", None) == path]
        try:
            with runs.running(*([tag] if tag else [])):
                logging.getLogger("tagpup.indexing").info(message)
        finally:
            root.removeHandler(handler)
            handler.close()
            root.setLevel(level)
        os.utime(path, (at, at))

    def indexer(self, name, lines):
        """The code folder of an indexer that prints `lines` as the CLI's `index` prints them (FAKE_CLI; "WAIT <file>"
        waits for the file) and exits 0: what index_folder runs."""
        code = os.path.join(self.home.root, name)
        os.makedirs(code, exist_ok=True)
        with open(os.path.join(code, "tagpup_cli.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_CLI)
        with open(os.path.join(code, "plan.json"), "w", encoding="utf-8") as handle:
            json.dump({"root": ROOT, "lines": lines, "exit": 0}, handle)
        return code

    def index_row(self, library):
        rows = [run for run in job_runs.runs(library, job_runs.INDEX, 5) if run.outcome == "running"]
        return rows[0] if rows else None

    def walking_sync(self, library, started, at):
        """The watcher's whole sync of `library` (a run of the `sync` job, recurring.sync_whole) walking the folders
        again, and this process's index of what its first walk found, queued under it ten minutes in, which said its
        metadata bar 3 s ago and runs on; a second folder waits behind it: the sync's run id, and what lets the index
        end (it ends at the scenario's clock then)."""
        sync = job_runs.claim(library, recurring.SYNC, library.name, started)
        job_runs.progress(library, sync.run_id, {"folders": 812, "files": 20417}, "walking_again", now=at - 5)
        folders = [os.path.join(self.pictures, library.name, each) for each in ("Regatta 2019", "Harbour Walk")]
        for each in folders:
            os.makedirs(each, exist_ok=True)
        go = [os.path.join(self.home.root, "go-%d" % number) for number in (1, 2)]
        bar = progress_line.say(indexing.phase_fact("metadata", 120, 800))
        running = self.indexer("indexer-regatta", ["WAIT " + go[0], bar, "WAIT " + go[1]])
        waiting = self.indexer("indexer-walk", [])
        queue = self.runtime.index_queues.of(library)

        def release():
            for each in go:
                open(each, "w").close()
            queue.wait()
        self.addCleanup(release)
        self.clock.now = started + 10 * MINUTE
        with runs.running(runs.job_tag(library.name, sync.run_id)):
            self.assertTrue(queue.start(folders[:1], lambda folders, told: indexing.index_folder(
                library, folders, running, told), together=True).ok)
        wait_until(lambda: self.index_row(library) is not None, 60, what="the queue to claim a run of its index")
        self.assertTrue(queue.start(folders[1:], lambda folders, told: indexing.index_folder(
            library, folders, waiting, told), together=True).ok)
        self.clock.now = at - 3
        open(go[0], "w").close()
        wait_until(lambda: (self.index_row(library).progress or {}).get("phase") == "metadata", 60,
                   what="the index's row to say the metadata bar")
        return sync.run_id, release

    def cli_index(self, library, started, said_at):
        """An index of `library` the CLI runs (another process's: its run is no queue's here), its bake at the third of
        nine folders, said at `said_at` through the reader of the indexer's lines (index_folder, the run being
        recorded): its run, and what its row holds beside its counts."""
        held = {"run": runs.index_tag(library.name, started), "folders": 1}
        cli = job_runs.claim(library, job_runs.INDEX, library.name, started, changed=held)
        folder = self.folder_of(library.name)
        line = progress_line.say(bake_after_index.folder_told(3, 9, bake_after_index.Baked(written=14, current=40), folder))
        self.clock.now = said_at
        with job_runs.recording(library, cli.run_id):
            self.assertTrue(indexing.index_folder(library, [folder], self.indexer("indexer-cli", [line])).ok)
        return cli, held

    def test_the_replies_the_page_reads_are_the_page_tests_fixture(self):
        text = self.written()
        self.home = own_home.for_test(self, prefix="failed_run_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        shares.forget()
        self.assertEqual(text, self.written(), "the replies differ from one run to the next")
        page_fixtures.hold_to(self, FIXTURE, text, "tests.test_activity_page_replies", "the Activity page")

    def written(self):
        """The four mornings' replies, as the fixture's text: every time.time the scenario's (self.clock), an index's
        tag and the clock of a recorded run (job_runs.run, whose default is time.time as imported) read at it."""
        self.clock = Clock(NIGHT)
        tag, recorded = runs.index_tag, job_runs.run
        pid = "-%d-" % os.getpid()

        def now():
            return time.time()

        def run(library, job, run_for, clock=now, *rest, **kwargs):
            return recorded(library, job, run_for, clock, *rest, **kwargs)
        with mock.patch.object(runs, "_counter", itertools.count(1)), mock.patch("time.time", self.clock), \
                mock.patch.object(runs, "index_tag", lambda library, at=None: tag(
                    library, now() if at is None else at).replace(pid, "-" + STAND_IN_PID + "-")), \
                mock.patch.object(job_runs, "run", run):
            return self.mornings()

    def mornings(self):
        found = {}
        harbour = self.library("harbour")
        lighthouse = self.library("lighthouse")

        # Quiet: last night's sync was done, nothing runs.
        self.nightly_sync(harbour, at=NIGHT)
        found["quiet"] = self.replies([harbour], NIGHT + 7 * HOUR)

        # A long sync: the watcher's walk of the share, this process's index beneath it.
        started = NIGHT + 8 * HOUR
        at = started + 14 * MINUTE
        sync_id, release = self.walking_sync(harbour, started, at)
        walking = {"running": True, "roots": [], "libraries": {},
                   "syncing": {"library": harbour.name, "folder": None, "started": job_runs.job_runs.stamp(started),
                               "walking_at": os.path.join(self.pictures, "harbour", "2019", "Regatta")}}
        found["long_sync"] = self.replies([harbour, lighthouse], at, watcher=walking)

        # Stuck: the CLI's index of lighthouse said nothing for 12 minutes.
        cli, cli_held = self.cli_index(lighthouse, started, at - 12 * MINUTE)
        found["stuck"] = self.replies([harbour, lighthouse], at, watcher=walking)

        # The morning after: the long sync and its index ended, the stuck index failed; breakwater's share dropped in
        # the night, jetty's server was killed mid-sync, and pier is no library. Harbour's index is ended by its queue,
        # and the folder waiting behind it indexed after it. The long sync ends as the sync job ends it: its sync's
        # record written, the run's counts the Result's (job_runs.counts), which name the record it pairs with.
        self.clock.now = at + 20 * MINUTE
        release()
        self.runtime.index_queues.forget(harbour)
        walked = sync.sync(harbour, apply=True, roots=[os.path.join(self.pictures, "harbour")])
        self.assertIn(sync.RECORD, walked.details, "the long sync wrote no record")
        job_runs.end(harbour, sync_id, at + 25 * MINUTE, job_runs.counts(walked))
        job_runs.end(lighthouse, cli.run_id, at + 30 * MINUTE, dict({"errors": 1}, **cli_held),
                     failed=job_runs.Failure(job_runs.ERROR, "The indexer stopped answering; its process was ended."))
        night = started + 6 * HOUR
        breakwater = self.library("breakwater", new=("IMG_0002.jpg",))
        jetty = self.library("jetty")
        self.nightly_sync(jetty, at=night - HOUR)
        self.nightly_sync(breakwater, self.share_drops(breakwater), at=night)
        killed = job_runs.claim(jetty, recurring.SYNC, jetty.name, night + HOUR)
        self.assertTrue(killed)
        with mock.patch.object(file_journal, "owner_alive", return_value=False):
            self.assertEqual(1, job_runs.sweep_abandoned(jetty, night + 2 * HOUR))   # the server's next start
        with open(self.home.library("pier.db"), "wb") as handle:
            handle.write(b"not a library")
        # The logs: the servers' own, and the runs' of the two indexes that ran; the one that waited wrote none.
        index_tags = {run.parent: run.changed.get("run") for run in job_runs.runs(harbour, job_runs.INDEX, 5)}
        self.write_log(logs.SERVER_LOG, None, "Serving on port 8080", night + 4 * HOUR - 60)
        self.write_log("tagpup_mcp.log", None, "MCP server ready", night - HOUR)
        self.write_log(logs.run_log(indexing.INDEXER_LOG, index_tags[sync_id]), index_tags[sync_id],
                       "Indexed 1 folder", at + 20 * MINUTE)
        self.write_log(logs.run_log(indexing.INDEXER_LOG, cli_held["run"]), cli_held["run"],
                       "The indexer stopped answering", at + 30 * MINUTE)
        self.write_log("run_tests-20260921-150500.log", None, "FAILED (failures=1)", night - 2 * HOUR)
        waited = logs.run_log(indexing.INDEXER_LOG, index_tags[None])
        found["morning"] = self.replies([harbour, lighthouse, breakwater, jetty], night + 4 * HOUR, missing=waited)

        morning = found["morning"]
        items = [each for group in morning["jobs"]["needs_you"] for each in group["items"]]
        # Run it now only for a job of the recurring runner: an index is run again by syncing its folder.
        self.assertEqual([("breakwater", "sync", True), ("lighthouse", "index", False)],
                         [(each["library"], each["job"], each["run_now"]) for each in items])
        self.assertTrue(items[0]["sentence"].startswith("The network share"), items[0]["sentence"])
        self.assertIn("pier", {each["name"] for each in morning["jobs"]["libraries"] if each.get("error")})
        self.assertIn("abandoned", {each["outcome"] for each in morning["timeline"]["entries"]})
        (index,) = [each for each in morning["timeline"]["entries"] if each["kind"] == "index" and each["parent"]]
        self.assertEqual(sync_id, index["parent"])
        # The long sync is one entry, its run and its record together, and its index nests beneath it by the run's id.
        (long,) = [each for each in morning["timeline"]["entries"]
                   if each["kind"] == "sync" and each["library"] == harbour.name and each["id"] == sync_id]
        self.assertNotIn(sync.RECORD, long["counts"])
        self.assertTrue(index["logs"], "an ended index names its log")
        (long_harbour,) = [each for each in found["long_sync"]["now"]["libraries"] if each["name"] == "harbour"]
        # This process's own index is its queue's, once, with its row's id, its parent's and its phase in words.
        own = long_harbour["indexing"]["running"]
        self.assertEqual(["sync"], [run["job"] for run in long_harbour["jobs"]])
        self.assertEqual((sync_id, sync_id), (long_harbour["jobs"][0]["run_id"], own["parent"]))
        self.assertEqual((own["run"], own["run_id"]), (index["run"], index["id"]))
        self.assertEqual([{"name": "Harbour Walk", "folders": 1}], long_harbour["indexing"]["queued"])
        # The phase is worded by the server, once (the page holds no copy), and every phase has its words.
        self.assertEqual(["walking the folders again", "reading metadata"],
                         [long_harbour["jobs"][0]["progress"]["said"], own["said"]])
        self.assertEqual(set(job_runs.PHASES), set(activity.PHASE_SAID))
        # What finished last in a library is read of that library alone, whatever the timeline of every library holds.
        for library in (harbour, lighthouse, breakwater, jetty):
            (block,) = [each for each in morning["jobs"]["libraries"] if each["name"] == library.name]
            newest = max((entry for entry in activity.timeline(library) if entry["finished"]),
                         key=lambda entry: entry["finished"])
            self.assertEqual((newest["what"], newest["finished"]),
                             (block["last_finished"]["what"], block["last_finished"]["finished"]), library.name)
        text = "{\n" + ",\n".join("%s: %s" % (json.dumps(key), json.dumps(found[key], sort_keys=True))
                                  for key in sorted(found)) + "\n}\n"
        text = text.replace("-%d-" % os.getpid(), "-%s-" % STAND_IN_PID).replace(
            "-%d.log" % os.getpid(), "-%s.log" % STAND_IN_PID)
        for spelled in (self.home.root, self.home.root.replace("\\", "/")):
            text = text.replace(json.dumps(spelled)[1:-1], "D:\\\\TagPup")
        return text


if __name__ == "__main__":
    unittest.main()

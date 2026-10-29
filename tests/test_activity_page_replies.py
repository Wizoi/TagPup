"""The replies the Activity page reads (web/activity/), made by the real routes from libraries seeded through the real
writers, and written to tests/frontend/fixtures/activity.json for the page's test (tests/frontend/activity-page.test.mjs)
to answer from, so the page is held to what the server sends and not to a reply written by hand (docs/OWNERS.md,
contract 22). Each scenario is a morning the owner opens the page on:

    quiet       nothing runs; last night's sync was done.
    long_sync   the watcher's whole sync of harbour walks the share again, an index of what it found beneath it.
    stuck       the CLI's index of lighthouse last said how far it got 12 minutes ago (#2210).
    morning     breakwater's share dropped overnight (Needs you, Run it now); jetty's server was killed mid-sync (an
                abandoned run says why); harbour's sync and its index ended (a tree); pier is no library at all.

The clocks are the scenarios' own (time.time is the scenario's while the real sync runs and the routes answer, the
journal's clock the day before while each library is made; the home's folder, the process id in a run's tag and in a log's
name (tagpup.logs.run_log) and the count after it are renumbered), so the file is the same on every run: the test makes it twice, in two homes, and the two are one text.
Fictional names only.
"""
import itertools
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402,F401
from test_a_failed_run_says_what_to_do import NIGHT, Clock, _Libraries  # noqa: E402
from test_activity_routes import FakeWatcher  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import dates, runs  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity, job_runs  # noqa: E402
from tagpup.store import file_journal, journal  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "fixtures", "activity.json")
MINUTE, HOUR = 60, 3600
ROUTES = ("now", "jobs", "timeline")


class RepliesForTheActivityPage(_Libraries):
    def library(self, name, **kwargs):
        """A library made the day before the scenarios: the change that stamps its settings is recorded at the journal's
        clock (dates.recorded, time.localtime, which reads no time.time)."""
        day_before = NIGHT - 24 * HOUR
        with mock.patch("time.time", Clock(day_before)), \
                mock.patch.object(journal, "_now", lambda: dates.recorded(day_before)):
            return super().library(name, **kwargs)

    def nightly_sync(self, library, queue=None, at=NIGHT):
        """The daily sync's run, at the scenario's clock: the sync's own record reads time.time as it ends."""
        with mock.patch("time.time", Clock(at)):
            return super().nightly_sync(library, queue, at)

    def replies(self, libraries, at, watcher=None):
        """What the page asks, as the real routes answer it, at the scenario's clock `at`."""
        tasks = [(runtimes.RECURRING_JOBS, recurring.Runner(lambda: libraries, clock=Clock(at)))]
        if watcher is not None:
            tasks.append((runtimes.FOLDER_WATCHER, FakeWatcher(watcher)))
        app = web.create_app("tagpup", lifecycle=Lifecycle(background=runtimes.Background(tasks)))
        app.testing = True
        client = app.test_client()
        found = {}
        with mock.patch("time.time", Clock(at)):
            for route in ROUTES:
                reply = client.get("/api/activity/" + route)
                self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
                found[route] = reply.get_json()
        return found

    def walking_sync(self, library, started, at):
        """The watcher's whole sync of `library` (a run of the `sync` job, recurring.sync_whole) walking the folders
        again, and the index of what its first walk found claimed beneath it, reading metadata: their run ids."""
        sync = job_runs.claim(library, recurring.SYNC, library.name, started)
        job_runs.progress(library, sync.run_id, {"folders": 812, "files": 20417}, "walking_again", now=at - 5)
        self.held = {"run": runs.index_tag(library.name, started + 10 * MINUTE), "folders": 3}
        with runs.running(runs.job_tag(library.name, sync.run_id)):
            index = job_runs.claim(library, job_runs.INDEX, library.name, started + 10 * MINUTE, changed=self.held)
        job_runs.progress(library, index.run_id, {"done": 120, "of": 800, "percent": 15}, "metadata", now=at - 3)
        return sync.run_id, index.run_id

    def test_the_replies_the_page_reads_are_written_for_the_page_test(self):
        text = self.written()
        self.home = own_home.for_test(self, prefix="failed_run_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        shares.forget()
        self.assertEqual(text, self.written(), "the replies differ from one run to the next")
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        before = None
        if os.path.exists(FIXTURE):
            with open(FIXTURE, encoding="utf-8") as handle:
                before = handle.read()
        if before != text:
            with open(FIXTURE, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)

    def written(self):
        """The four mornings' replies, as the fixture's text."""
        with mock.patch.object(runs, "_counter", itertools.count(1)):
            return self.mornings()

    def mornings(self):
        found = {}
        harbour = self.library("harbour")
        lighthouse = self.library("lighthouse")

        # Quiet: last night's sync was done, nothing runs.
        self.nightly_sync(harbour, at=NIGHT)
        found["quiet"] = self.replies([harbour], NIGHT + 7 * HOUR)

        # A long sync: the watcher's walk of the share, the index beneath it.
        started = NIGHT + 8 * HOUR
        at = started + 14 * MINUTE
        self.long = self.walking_sync(harbour, started, at)
        walking = {"running": True, "roots": [], "libraries": {},
                   "syncing": {"library": harbour.name, "folder": None, "started": job_runs.job_runs.stamp(started),
                               "walking_at": os.path.join(self.pictures, "harbour", "2019", "Regatta")}}
        found["long_sync"] = self.replies([harbour, lighthouse], at, watcher=walking)

        # Stuck: the CLI's index of lighthouse said nothing for 12 minutes.
        cli_held = {"run": runs.index_tag(lighthouse.name, started), "folders": 1}
        cli = job_runs.claim(lighthouse, job_runs.INDEX, lighthouse.name, started, changed=cli_held)
        job_runs.progress(lighthouse, cli.run_id, {"folders_done": 2, "folders": 9, "written": 14, "current": 40,
                                                   "conflicts": 0, "percent": 22}, "bake", now=at - 12 * MINUTE)
        found["stuck"] = self.replies([harbour, lighthouse], at, watcher=walking)

        # The morning after: the long sync and its index ended, the stuck index failed; breakwater's share dropped in
        # the night, jetty's server was killed mid-sync, and pier is no library. An index ends as the queue ends it
        # (tagpup.jobs.indexing.IndexQueue._end): its counts beside what it held from its claim.
        sync_id, index_id = self.long
        job_runs.end(harbour, index_id, at + 20 * MINUTE, dict({"photos_read": 800}, **self.held))
        job_runs.end(harbour, sync_id, at + 25 * MINUTE, {"new": 812})
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
        found["morning"] = self.replies([harbour, lighthouse, breakwater, jetty], night + 4 * HOUR)

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
        self.assertEqual(self.held["run"], index["run"])
        self.assertTrue(index["logs"], "an ended index names its log")
        (long_harbour,) = [each for each in found["long_sync"]["now"]["libraries"] if each["name"] == "harbour"]
        self.assertEqual(["sync", "index"], [run["job"] for run in long_harbour["jobs"]])
        self.assertEqual(long_harbour["jobs"][0]["run_id"], long_harbour["jobs"][1]["parent"])
        # The phase is worded by the server, once (the page holds no copy), and every phase has its words.
        self.assertEqual(["walking the folders again", "reading metadata"],
                         [run["progress"]["said"] for run in long_harbour["jobs"]])
        self.assertEqual(set(job_runs.PHASES), set(activity.PHASE_SAID))
        text = "{\n" + ",\n".join("%s: %s" % (json.dumps(key), json.dumps(found[key], sort_keys=True))
                                  for key in sorted(found)) + "\n}\n"
        text = text.replace("-%d-" % os.getpid(), "-4242-").replace("-%d.log" % os.getpid(), "-4242.log")
        for spelled in (self.home.root, self.home.root.replace("\\", "/")):
            text = text.replace(json.dumps(spelled)[1:-1], "D:\\\\TagPup")
        return text


if __name__ == "__main__":
    unittest.main()

"""Recurring jobs (docs/ARCHITECTURE.md, phase 8): what is due, from the runs recorded in
the library; a missed period run once; one run of a job for a library at a time, across
threads and processes, and a run a dead process left taken over; the web server's
thread stopped cleanly; and what the apps are told, counts only.
"""
import json
import os
import queue
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import web_client  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, file_journal, job_runs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Noon on a day without a change of clocks near it.
NOON = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
HOUR, DAY = recurring.HOUR, recurring.DAY


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


def made(home, name):
    path = home.library(name + ".db")
    library_actions.create(path)
    return Library(path)


class Counting:
    """A job's service that counts its calls, and fails when told to."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, library, run):
        self.calls.append((getattr(library, "name", None), run.now, run.forced))
        if self.fail:
            raise RuntimeError("the disk under D:/Photos/Imogen is full")
        return Result(attempted=2, changed=1, details={"bytes": 1024, "taken": ["daily"]})


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="jobs_")
        self.harbour = made(self.home, "harbour")
        self.clock = Clock()
        self.service = Counting()
        self.registry = recurring.Registry()
        self.registry.job("tidy", recurring.DAILY)(self.service)
        self.runner = recurring.Runner(lambda: [self.harbour], self.registry, clock=self.clock)

    def ran(self):
        return [o for o in self.runner.run_due() if o.ran]


class WhatIsDue(Base):
    def test_a_job_never_run_is_due_and_then_not_until_its_period_is_up(self):
        self.assertEqual(1, len(self.ran()))
        self.clock.now += 23 * HOUR
        self.assertEqual([], self.ran())
        self.clock.now += HOUR
        self.assertEqual(1, len(self.ran()))
        self.assertEqual(2, len(self.service.calls))

    def test_a_missed_period_runs_once_not_once_for_each_missed(self):
        self.ran()
        self.clock.now += 5 * DAY + 3 * HOUR
        self.assertEqual(1, len(self.ran()))
        self.assertEqual([], self.ran())
        self.clock.now += 10 * 60
        self.assertEqual([], self.ran())
        self.assertEqual(2, len(self.service.calls))
        # And it is due a period after that run, not on the old schedule.
        self.clock.now += DAY - 10 * 60
        self.assertEqual(1, len(self.ran()))

    def test_a_failed_run_is_tried_again_after_an_hour_not_a_period(self):
        self.service.fail = True
        outcome = self.ran()[0]
        self.assertIsNotNone(outcome.error)
        self.clock.now += 30 * 60
        self.assertEqual([], self.ran())
        self.clock.now += 30 * 60
        self.assertEqual(1, len(self.ran()))

    def test_asked_for_now_it_runs_whether_or_not_it_is_due(self):
        self.ran()
        self.clock.now += 60
        outcomes = self.runner.run("tidy", self.harbour)
        self.assertTrue(outcomes[0].ran)
        self.assertEqual([True], [forced for _, _, forced in self.service.calls[1:]])

    def test_a_run_is_recorded_in_the_library_with_what_it_changed_as_counts(self):
        self.ran()
        run = job_runs.runs(self.harbour.path)[0]
        self.assertEqual(("tidy", "harbour", "done", None), (run.job, run.library, run.outcome, run.owner))
        self.assertEqual({"attempted": 2, "changed": 1, "skipped": 0, "errors": 0, "bytes": 1024}, run.changed)
        self.assertEqual(job_runs.stamp(NOON), run.started)

    def test_each_library_has_its_own_runs(self):
        cove = made(self.home, "cove")
        runner = recurring.Runner(lambda: [self.harbour, cove], self.registry, clock=self.clock)
        self.assertEqual(["harbour", "cove"], [o.library for o in runner.run_due() if o.ran])
        self.assertEqual(["cove"], [run.library for run in job_runs.runs(cove.path)])

    def test_a_job_not_run_per_library_is_called_with_them_all_and_recorded_in_the_first(self):
        cove = made(self.home, "cove")
        seen = []
        registry = recurring.Registry()
        registry.job("everywhere", recurring.WEEKLY, per_library=False)(
            lambda libraries, run: seen.append([lib.name for lib in libraries]) or Result())
        runner = recurring.Runner(lambda: [self.harbour, cove], registry, clock=self.clock)
        self.assertEqual(1, len([o for o in runner.run_due() if o.ran]))
        self.assertEqual([["harbour", "cove"]], seen)
        self.assertEqual([("everywhere", None)], [(r.job, r.library) for r in job_runs.runs(cove.path)])
        self.assertEqual([], job_runs.runs(self.harbour.path))
        self.assertEqual([], [o for o in runner.run_due() if o.ran])

    def test_registering_a_job_is_one_line_and_a_name_is_registered_once(self):
        registry = recurring.Registry()
        registry.job("sync", recurring.every_hours(6))(lambda library, run: Result())
        self.assertEqual((["sync"], 6 * HOUR), (registry.names(), registry.get("sync").period.seconds))
        with self.assertRaises(ValueError):
            registry.job("sync", recurring.DAILY)(lambda library, run: Result())

    def test_the_registry_holds_the_snapshots_and_pruning_the_journal(self):
        self.assertEqual({"snapshots": "daily", "prune-journal": "weekly"},
                         {job.name: job.period.name for job in recurring.JOBS})
        self.assertTrue(all(job.per_library for job in recurring.JOBS))


class ALibraryBehind(unittest.TestCase):
    def test_is_left_alone_not_migrated(self):
        from test_migrations import at_version
        from tagpup.store import schema
        home = own_home.for_test(self, prefix="jobs_")
        library = Library(home.library("harbour.db"))
        at_version(library.path, 12)
        registry = recurring.Registry()
        service = Counting()
        registry.job("tidy", recurring.DAILY)(service)
        outcomes = recurring.Runner(lambda: [library], registry, clock=Clock()).run_due()
        self.assertEqual([(False, "behind")], [(o.ran, o.why) for o in outcomes])
        self.assertEqual([], service.calls)
        self.assertEqual(1, len(schema.pending(library.path)), "running what is due migrated it")


class OneRunAtATime(Base):
    def test_two_runners_racing_for_one_job_run_it_once(self):
        release, started = threading.Event(), threading.Event()
        calls = []

        def slow(library, run):
            calls.append(library.name)
            started.set()
            release.wait(30)
            return Result(changed=1)

        registry = recurring.Registry()
        registry.job("slow", recurring.DAILY)(slow)
        barrier = threading.Barrier(2)
        answers = queue.Queue()

        def race():
            runner = recurring.Runner(lambda: [self.harbour], registry, clock=self.clock)
            barrier.wait(10)
            answers.put(runner.run_due())

        threads = [threading.Thread(target=race) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            loser = answers.get(timeout=30)
            self.assertEqual([(False, "running")], [(o.ran, o.why) for o in loser])
            self.assertTrue(started.wait(30))
        finally:
            release.set()
            for thread in threads:
                thread.join(30)
        winner = answers.get(timeout=5)
        self.assertEqual([True], [o.ran for o in winner])
        self.assertEqual(["harbour"], calls)
        self.assertEqual(["done"], [run.outcome for run in job_runs.runs(self.harbour.path)])

    def _left_by(self, owner):
        conn = db.connect(self.harbour.path)
        try:
            conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES"
                         " ('tidy', 'harbour', ?, 'running', ?)", (job_runs.stamp(NOON - HOUR), owner))
            conn.commit()
        finally:
            conn.close()

    def test_a_run_a_process_that_has_ended_left_is_taken_over(self):
        host = socket.gethostname()
        self._left_by("%s:%d:%d" % (host, os.getpid(), processes.started(os.getpid()) - 1))
        self.assertEqual(1, len(self.ran()))
        self.assertEqual(["done", "abandoned"], [run.outcome for run in job_runs.runs(self.harbour.path)])

    def test_a_run_another_machine_holds_is_left_to_it(self):
        self._left_by("another-machine:4242:1234567")
        self.assertEqual([(False, "running")], [(o.ran, o.why) for o in self.runner.run_due()])
        self.assertEqual([], self.service.calls)

    def test_a_run_another_process_holds_is_left_to_it_until_it_ends(self):
        # A real second process claims the job and holds it; killed, its run is taken over.
        code = ("import sys, time; sys.path.insert(0, %r)\n"
                "from tagpup.store import job_runs\n"
                "claim = job_runs.claim(%r, 'tidy', 'harbour', time.time())\n"
                "print(claim.run_id, flush=True)\n"
                "sys.stdin.readline()\n") % (ROOT, self.harbour.path)
        import subprocess
        child = processes.start([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True)
        try:
            self.assertTrue(child.stdout.readline().strip().isdigit(), "the other process claimed nothing")
            self.assertEqual([(False, "running")], [(o.ran, o.why) for o in self.runner.run_due()])
            holder = job_runs.runs(self.harbour.path)[0].owner
            self.assertNotEqual(file_journal.owner(), holder)
        finally:
            processes.kill_tree(child.pid)
            child.wait(30)
            child.stdin.close()
            child.stdout.close()
        self.assertEqual(1, len(self.ran()))
        self.assertEqual(["done", "abandoned"], [run.outcome for run in job_runs.runs(self.harbour.path)])

    def test_a_run_ended_by_another_is_not_ended_again(self):
        claim = job_runs.claim(self.harbour.path, "tidy", "harbour", NOON)
        conn = db.connect(self.harbour.path)
        try:
            conn.execute("UPDATE job_runs SET outcome = 'abandoned' WHERE id = ?", (claim.run_id,))
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(job_runs.finish(self.harbour.path, claim.run_id, job_runs.DONE, NOON + 60))

    def test_only_the_last_runs_are_kept(self):
        with mock.patch.object(job_runs, "KEEP_RUNS", 3):
            for _ in range(5):
                self.ran()
                self.clock.now += DAY
        self.assertEqual(3, len(job_runs.runs(self.harbour.path)))


class TheWebServersThread(Base):
    def test_looks_for_what_is_due_and_stops_cleanly(self):
        thread = self.runner.start(every=0.05, first_after=0)
        deadline = time.time() + 30
        while not self.service.calls and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.runner.stop(timeout=30))
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(self.service.calls), "the fake clock never moved, so it was due once")

    def test_the_launcher_starts_none_under_test_and_stops_it_after_serving(self):
        import tagpup_web
        order = []
        with mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1"}), \
                mock.patch.object(tagpup_web.web, "serve", side_effect=lambda *a, **k: order.append("serve")), \
                mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)"), \
                mock.patch.object(recurring.Runner, "start", side_effect=lambda *a, **k: order.append("start")), \
                mock.patch.object(recurring.Runner, "stop", side_effect=lambda *a, **k: order.append("stop")):
            tagpup_web.main(["--tagpup-port", "1", "--tuner-port", "2"])
            self.assertEqual(["serve", "stop"], order)
            del order[:]
            with mock.patch.dict(os.environ, {runtimes.RUN_JOBS: "1"}):
                tagpup_web.main(["--tagpup-port", "1", "--tuner-port", "2"])
            self.assertEqual(["start", "serve", "stop"], order)


class WhetherAProcessRunsThem(unittest.TestCase):
    def test_not_in_a_test_run_unless_asked_and_never_when_told_not_to(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(runtimes.RUN_JOBS, None)
            os.environ.pop(runtimes.NO_JOBS, None)
            self.assertFalse(runtimes.runs_recurring_jobs())
            os.environ[runtimes.RUN_JOBS] = "1"
            self.assertTrue(runtimes.runs_recurring_jobs())
            os.environ[runtimes.NO_JOBS] = "1"
            self.assertFalse(runtimes.runs_recurring_jobs())

    def test_the_home_libraries_are_those_in_the_data_folder(self):
        home = own_home.for_test(self, prefix="jobs_")
        self.assertEqual([], runtimes.home_libraries(), "none is made for a picker's first name")
        made(home, "harbour")
        made(home, "test_cove")
        self.assertEqual(["harbour"], [lib.name for lib in runtimes.home_libraries()])
        self.assertEqual(["test_cove"], [lib.name for lib in runtimes.home_libraries(test_mode=True)])


class WhatTheAppsAreTold(unittest.TestCase):
    def test_each_jobs_last_run_and_when_it_is_due_counts_only(self):
        for kind in ("tagpup", "tuner"):
            app, home = web_client.app_for(self, kind)
            library = Library(home.library("library.db"))
            listed = app.test_client().get("/library/api/jobs").get_json()
            self.assertEqual(["snapshots", "prune-journal"], [job["name"] for job in listed["jobs"]])
            self.assertEqual([None, None], [job["last"] for job in listed["jobs"]])

            registry = recurring.Registry()
            service = Counting()
            service.fail = True
            registry.job("snapshots", recurring.DAILY)(service)
            recurring.Runner(lambda: [library], registry).run_due()
            answer = app.test_client().get("/library/api/jobs")
            self.assertEqual(200, answer.status_code)
            job = answer.get_json()["jobs"][0]
            self.assertEqual(("failed", False, {"errors": 1}), (job["last"]["outcome"], job["running"],
                                                                job["last"]["changed"]))
            self.assertNotIn("Imogen", json.dumps(answer.get_json()), "a failure's note can name a path")
            # Failed: due again an hour after it started.
            self.assertEqual(job_runs.seconds(job["last"]["started"]) + HOUR, job_runs.seconds(job["next_due"]))


if __name__ == "__main__":
    unittest.main()

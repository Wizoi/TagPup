"""What the web server runs beside its requests, registered in one place
(tagpup.runtime.BACKGROUND; docs/ARCHITECTURE.md, phase 8): the recurring jobs, letting
idle models go, and -- phase 8c -- sync's watcher. The server starts each as it starts
and stops each as it stops, and an update waits for none to be busy.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402


class Task:
    def __init__(self, log, name):
        self.log, self.name, self.working = log, name, False

    def start(self):
        self.log.append("start " + self.name)

    def stop(self, timeout=30):
        self.log.append("stop " + self.name)
        return True

    def busy(self):
        return self.working


class TheRegistry(unittest.TestCase):
    def test_holds_what_this_phase_runs_and_a_place_for_the_watcher(self):
        self.assertEqual(["recurring jobs", "release idle caches", "folder watcher"], list(runtimes.BACKGROUND))

    def test_refuses_a_name_twice(self):
        with mock.patch.dict(runtimes.BACKGROUND, clear=False):
            with self.assertRaises(ValueError):
                runtimes.background_task("recurring jobs")(lambda runtime: None)

    def test_a_task_a_process_does_not_run_is_left_out(self):
        log = []
        registry = {"watcher": lambda runtime: Task(log, "watcher"), "nothing here": lambda runtime: None}
        background = runtimes.background(Runtime(), registry)
        self.assertEqual(["watcher"], background.names())
        background.start()
        self.assertEqual([], background.stop())
        self.assertEqual(["start watcher", "stop watcher"], log)

    def test_stops_them_all_within_one_deadline(self):
        given = []

        class Slow:
            def start(self):
                pass

            def stop(self, timeout=30):
                given.append(timeout)
                time.sleep(min(timeout, 1.0))
                return timeout >= 1.0

            def busy(self):
                return False
        background = runtimes.background(Runtime(), {"one": lambda runtime: Slow(), "two": lambda runtime: Slow()})
        still = background.stop(timeout=1.0)
        # What each was given, not how long it took: a busy machine's seconds are not the deadline's (#721).
        self.assertEqual(2, len(given))
        self.assertLessEqual(given[0], 1.0)
        self.assertLess(given[1], 0.1, "each task was given the whole timeout")
        self.assertIn("two", still)

    def test_says_which_are_busy(self):
        log = []
        watcher = Task(log, "watcher")
        background = runtimes.background(Runtime(), {"watcher": lambda runtime: watcher})
        self.assertEqual([], background.busy())
        watcher.working = True
        self.assertEqual(["watcher"], background.busy())

    def test_the_recurring_jobs_run_only_where_they_are_run(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(runtimes.RUN_JOBS, None)
            self.assertNotIn("recurring jobs", runtimes.background(Runtime()).names())
            os.environ[runtimes.RUN_JOBS] = "1"
            self.assertIn("recurring jobs", runtimes.background(Runtime()).names())


class Counting:
    def __init__(self):
        self.go = threading.Event()
        self.entered = threading.Event()

    def __call__(self, library, run):
        self.entered.set()
        self.go.wait(30)
        return Result(changed=1)


class TheRecurringJobsRunner(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="background_")
        path = home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)
        self.registry = recurring.Registry()
        self.job = Counting()
        self.addCleanup(self.job.go.set)
        self.registry.job("tidy", recurring.DAILY, reason=recurring.SAFETY)(self.job)
        self.runner = recurring.Runner(lambda: [self.library], self.registry)

    def test_is_busy_while_a_job_runs(self):
        self.assertFalse(self.runner.busy())
        thread = threading.Thread(target=self.runner.run_due, daemon=True)
        thread.start()
        self.assertTrue(self.job.entered.wait(10))
        self.assertTrue(self.runner.busy())
        self.job.go.set()
        thread.join(10)
        self.assertFalse(self.runner.busy())

    def test_looks_again_when_started_after_a_stop(self):
        """A drain stops it; a drain that runs out of time starts it again."""
        self.runner.start(every=3600, first_after=3600)
        self.assertTrue(self.runner.stop(timeout=5))
        self.runner.start(every=0.05, first_after=0)
        self.assertTrue(self.job.entered.wait(10), "a runner started again never looked")
        self.job.go.set()
        self.assertTrue(self.runner.stop(timeout=10))


class Every(unittest.TestCase):
    def test_calls_until_stopped(self):
        calls = []
        every = runtimes.Every("TestEveryThread", lambda: calls.append(1), 0.01)
        every.start()
        deadline = time.time() + 10
        while len(calls) < 3 and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(every.stop(timeout=5))
        seen = len(calls)
        time.sleep(0.05)
        self.assertEqual(seen, len(calls), "called after it was stopped")
        self.assertFalse(every.busy())


if __name__ == "__main__":
    unittest.main()

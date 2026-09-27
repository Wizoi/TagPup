"""Each line of a program's log says which run it belongs to (tagpup.core.runs,
tagpup.logs): a recurring job's run, a sync, a run of the indexer -- so the Activity page's
"Logs for this run" shows that run's lines and no others. And the indexer the index queue
starts writes a log of its own, data/logs/indexer-<library>.log, not only the pipe its
parent reads (phase 8.5).
"""
import logging
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import logs  # noqa: E402
from tagpup.core import processes, runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import indexing  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import job_runs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Lines(logging.Handler):
    """The lines a program's log file would hold, as its handler formats them."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(logging.Formatter(logs.FORMAT))
        self.addFilter(logs.RunTag())
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class LinesTest(unittest.TestCase):
    def setUp(self):
        self.lines = Lines()
        self.logger = logging.getLogger("tests.log_runs")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.lines)
        self.addCleanup(self.logger.removeHandler, self.lines)
        # A run this test process was started for would be on every line.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(runs.ENV, None)


class ALineSaysItsRun(LinesTest):
    def test_a_line_outside_any_run_carries_no_tag(self):
        self.logger.info("nothing under way")
        self.assertRegex(self.lines.lines[0], r" tests\.log_runs - nothing under way$")

    def test_a_line_inside_a_run_carries_its_tag_and_nested_runs_carry_both(self):
        with runs.running(runs.job_tag("harbour", 12)):
            self.logger.info("the job")
            with runs.running(runs.sync_tag("harbour", "2026-09-26 10:15:00")):
                self.logger.warning("its sync")
        self.logger.info("after")
        self.assertIn("tests.log_runs [run job:harbour:12] - the job", self.lines.lines[0])
        self.assertIn("[run job:harbour:12,sync:harbour:20260926T101500] - its sync", self.lines.lines[1])
        self.assertNotIn("[run", self.lines.lines[2])

    def test_a_run_is_its_threads_alone(self):
        seen = []
        started, done = threading.Event(), threading.Event()

        def other():
            started.wait(5)
            self.logger.info("another thread")
            done.set()

        thread = threading.Thread(target=other)
        thread.start()
        with runs.running(runs.job_tag("harbour", 1)):
            started.set()
            done.wait(5)
        thread.join(5)
        seen = [line for line in self.lines.lines if "another thread" in line]
        self.assertEqual(1, len(seen))
        self.assertNotIn("[run", seen[0])

    def test_a_process_started_for_a_run_tags_every_line(self):
        os.environ[runs.ENV] = "index:harbour:20260926T101500-1"
        self.logger.info("the indexer")
        self.assertIn("[run index:harbour:20260926T101500-1] - the indexer", self.lines.lines[0])

    def test_a_tag_holds_nothing_but_names_and_numbers(self):
        self.assertEqual("job:all:3", runs.job_tag(None, 3))
        self.assertEqual("sync:My_Library:20260926T101500", runs.sync_tag("My Library", "2026-09-26 10:15:00"))
        self.assertTrue(runs.is_tag(runs.index_tag("harbour")))
        self.assertFalse(runs.is_tag("job:harbour:1 - injected"))


class TheRunsTagTheirLines(LinesTest):
    def setUp(self):
        super().setUp()
        self.home = own_home.for_test(self)
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)

    def test_a_recurring_jobs_run_tags_what_it_logs_with_its_run_id(self):
        registry = recurring.Registry()

        @registry.job("chatty", recurring.DAILY, reason=recurring.SAFETY)
        def _chatty(library, run):
            self.logger.info("working on it")
            return Result(attempted=1, changed=1)

        outcome = recurring.Runner(lambda: [self.library], registry=registry).run("chatty", self.library)[0]
        self.assertTrue(outcome.ran)
        self.assertIn("[run job:harbour:%d] - working on it" % outcome.run_id, self.lines.lines[0])
        self.assertEqual("done", job_runs.runs(self.library.path, "chatty")[0].outcome)

    def test_a_sync_tags_what_it_logs_with_when_it_started_as_its_record_keeps_it(self):
        held = {}

        def fake(library, folder, apply, exiftool_path, queue, roots, ignored, started):
            held["tags"], held["started"] = runs.current(), started
            return Result()

        with mock.patch.object(sync_service, "_sync", side_effect=fake):
            sync_service.sync(self.library, apply=True)
        self.assertEqual((runs.sync_tag("harbour", held["started"]),), held["tags"])

    def test_a_run_of_the_indexer_tags_its_lines_and_is_remembered(self):
        queue = indexing_jobs.IndexQueue("harbour")
        seen = {}

        def index(folder, cluster, report):
            seen["tags"] = runs.current()
            self.logger.info("indexing")
            return Result(attempted=1, changed=1)

        queue.start([self.home.data], index)
        queue.wait()
        self.assertEqual(1, len(seen["tags"]))
        self.assertTrue(seen["tags"][0].startswith("index:harbour:"), seen["tags"])
        self.assertIn("[run %s] - indexing" % seen["tags"][0], self.lines.lines[0])
        history = queue.history()
        self.assertEqual(1, len(history))
        self.assertEqual(seen["tags"][0], history[0]["run"])
        self.assertEqual("completed", history[0]["outcome"])

    def test_the_indexer_is_told_its_run_and_its_log(self):
        started = []

        class Done:
            returncode = 0

            def __init__(self):
                self.stdout = open(os.devnull, encoding="utf-8")

            def wait(self):
                return 0

        def start(args, **kwargs):
            started.append(kwargs["env"])
            return Done()

        with mock.patch.object(processes, "start", side_effect=start), \
                runs.running("index:harbour:20260926T101500-7"):
            indexing.index_folder(self.library, self.home.data, ROOT)
        self.assertEqual("index:harbour:20260926T101500-7", started[0][runs.ENV])
        self.assertEqual("indexer", started[0][runs.LOG_TO])


class TheIndexerWritesItsOwnLog(unittest.TestCase):
    """The CLI started for a run writes data/logs/indexer-<library>.log, tagged."""

    def test_the_cli_started_for_a_run_logs_to_its_file(self):
        home = own_home.for_test(self)
        env = dict(os.environ, TAGPUP_HOME=home.root, TAGPUP_DB_PATH=home.library("harbour.db"))
        env[runs.LOG_TO] = "indexer"
        env[runs.ENV] = "index:harbour:20260926T101500-2"
        code = ("import logging, tagpup_cli; logging.getLogger('tagpup_cli').warning('from the indexer')")
        done = processes.run([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout)
        path = os.path.join(home.data, "logs", "indexer-harbour.log")
        self.assertTrue(os.path.exists(path), "the indexer wrote no log of its own")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("tagpup_cli [run index:harbour:20260926T101500-2] - from the indexer", text)

    def test_the_cli_run_by_hand_writes_no_file(self):
        home = own_home.for_test(self)
        env = dict(os.environ, TAGPUP_HOME=home.root)
        env.pop(runs.LOG_TO, None)
        code = "import logging, tagpup_cli; logging.getLogger('tagpup_cli').warning('by hand')"
        done = processes.run([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout)
        self.assertFalse(os.path.exists(os.path.join(home.data, "logs")))


if __name__ == "__main__":
    unittest.main()

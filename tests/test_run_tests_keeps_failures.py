"""tools/run_tests.py keeps what a failed run said, and does not pass a file that ran
nothing (#101).

One full run in six counted 42 fewer tests and failed one file; its output was printed
and gone, so which file, and why, is not known. A file that ends before unittest's
summary with exit code 0 -- or holds no tests -- passed silently.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "tools"))

import run_tests  # noqa: E402


def finished(returncode, stdout):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout)


class AFileThatRanNothingFails(unittest.TestCase):
    def outcome(self, returncode, stdout):
        with mock.patch.object(run_tests.processes, "run", return_value=finished(returncode, stdout)):
            module, passed, ran, _seconds, output = run_tests.run_one("test_fictional")
        return passed, ran, output

    def test_a_file_that_ran_its_tests_passes(self):
        passed, ran, _output = self.outcome(0, "...\n----\nRan 3 tests in 0.1s\n\nOK\n")
        self.assertTrue(passed)
        self.assertEqual(ran, 3)

    def test_a_file_that_ended_before_the_summary_fails(self):
        passed, ran, output = self.outcome(0, "")
        self.assertFalse(passed)
        self.assertEqual(ran, 0)
        self.assertIn("no tests", output)

    def test_a_file_that_ran_no_tests_fails(self):
        passed, _ran, output = self.outcome(0, "\n----\nRan 0 tests in 0.000s\n\nOK\n")
        self.assertFalse(passed)
        self.assertIn("no tests", output)


class AFailedRunIsKept(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="tagpup_run_tests_logs_")
        self.addCleanup(shutil.rmtree, self.folder, True)

    def main(self, results):
        printed = io.StringIO()
        with mock.patch.object(run_tests, "FAILED_RUNS", self.folder), \
                mock.patch.object(run_tests, "run", return_value=results), \
                mock.patch.object(run_tests, "test_files", return_value=[r[0] for r in results]), \
                contextlib.redirect_stdout(printed):
            code = run_tests.main([])
        return code, printed.getvalue()

    def test_the_failed_files_output_is_on_disk_and_named(self):
        code, printed = self.main([
            ("test_fictional_a", True, 4, 0.1, "Ran 4 tests in 0.1s\n\nOK"),
            ("test_fictional_b", False, 2, 0.2, "Traceback: the Kestrel file broke\nRan 2 tests in 0.2s\n\nFAILED"),
        ])
        self.assertEqual(code, 1)
        kept = [os.path.join(self.folder, n) for n in os.listdir(self.folder)]
        self.assertEqual(len(kept), 1, kept)
        with open(kept[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("test_fictional_b", text)
        self.assertIn("the Kestrel file broke", text)
        self.assertIn(kept[0], printed)

    def test_a_run_that_passed_keeps_nothing(self):
        code, _printed = self.main([("test_fictional_a", True, 4, 0.1, "Ran 4 tests in 0.1s\n\nOK")])
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.folder), [])

    def test_only_the_latest_failed_runs_are_kept(self):
        for i in range(run_tests.KEEP_FAILED_RUNS + 3):
            with open(os.path.join(self.folder, "run_tests-20260101-0000%02d.log" % i), "w") as handle:
                handle.write("old")
        self.main([("test_fictional_b", False, 0, 0.2, "FAILED")])
        kept = sorted(os.listdir(self.folder))
        self.assertEqual(len(kept), run_tests.KEEP_FAILED_RUNS)
        self.assertNotIn("run_tests-20260101-000000.log", kept)


if __name__ == "__main__":
    unittest.main()

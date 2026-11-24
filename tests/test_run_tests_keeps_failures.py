"""tools/run_tests.py keeps what a failed run said, and does not pass a file that ran
nothing (#101); it keeps each file's and each test's time of every run, and runs the page tests.

One full run in six counted 42 fewer tests and failed one file; its output was printed
and gone, so which file, and why, is not known. A file that ends before unittest's
summary with exit code 0 -- or holds no tests -- passed silently.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
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


class AFileThatRanFewerTestsFails(unittest.TestCase):
    """The shape of the original flake: a run counted 1,427 tests where the last counted 1,469, and every file
    passed (#101); a file that stopped early without failing is caught by its count (#290)."""

    def check(self, results, counts, accept=False):
        return run_tests.check_counts(results, counts, accept)

    def test_a_file_that_ran_fewer_tests_than_last_time_fails(self):
        results, counts = self.check([("test_fictional_a", True, 38, 0.1, "Ran 38 tests")], {"test_fictional_a": 40})
        self.assertFalse(results[0][1])
        self.assertIn("38", results[0][4])
        self.assertIn("40", results[0][4])
        self.assertEqual(40, counts["test_fictional_a"], "the count it fell from is kept, so the next run notices too")

    def test_the_same_or_more_passes_and_is_recorded(self):
        results, counts = self.check([("test_fictional_a", True, 40, 0.1, ""), ("test_fictional_b", True, 9, 0.1, ""),
                                      ("test_fictional_c", True, 3, 0.1, "")],
                                     {"test_fictional_a": 40, "test_fictional_b": 7})
        self.assertEqual([True, True, True], [r[1] for r in results])
        self.assertEqual({"test_fictional_a": 40, "test_fictional_b": 9, "test_fictional_c": 3}, counts)

    def test_accepting_fewer_records_it(self):
        results, counts = self.check([("test_fictional_a", True, 38, 0.1, "")], {"test_fictional_a": 40}, accept=True)
        self.assertTrue(results[0][1])
        self.assertEqual(38, counts["test_fictional_a"])

    def test_a_file_that_failed_does_not_change_its_count(self):
        results, counts = self.check([("test_fictional_a", False, 12, 0.1, "FAILED")], {"test_fictional_a": 40})
        self.assertEqual("FAILED", results[0][4])
        self.assertEqual({"test_fictional_a": 40}, counts)

    def test_the_counts_are_kept_beside_the_times_and_neither_spoils_the_other(self):
        folder = tempfile.mkdtemp(prefix="tagpup_run_tests_counts_")
        self.addCleanup(shutil.rmtree, folder, True)
        with mock.patch.object(run_tests, "DURATIONS", os.path.join(folder, ".durations.json")):
            run_tests.save_durations({"test_fictional_a": 1.5, run_tests.COUNTS: {"test_fictional_a": 40}})
            durations = run_tests.load_durations()
        self.assertEqual(40, durations[run_tests.COUNTS]["test_fictional_a"])
        self.assertEqual(1.5, durations["test_fictional_a"])


def keep_times_in(case):
    """The runner's two times files in a folder of the test's own, never the checkout's tests/; returns the folder."""
    folder = tempfile.mkdtemp(prefix="tagpup_run_tests_times_")
    case.addCleanup(shutil.rmtree, folder, True)
    for name, file in (("DURATIONS", ".durations.json"), ("TEST_DURATIONS", ".test_durations.json")):
        patcher = mock.patch.object(run_tests, name, os.path.join(folder, file))
        patcher.start()
        case.addCleanup(patcher.stop)
    return folder


class RunsMain(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="tagpup_run_tests_logs_")
        self.addCleanup(shutil.rmtree, self.folder, True)
        keep_times_in(self)

    def main(self, results, again=None, argv=()):
        """`again` is what a failed file does when it is run a second time alone."""
        printed = io.StringIO()
        second = again or (lambda module: (module, False, 0, 0.1, "FAILED again"))
        with mock.patch.object(run_tests, "FAILED_RUNS", self.folder), \
                mock.patch.object(run_tests, "run", return_value=results), \
                mock.patch.object(run_tests, "run_one", side_effect=second) as reruns, \
                mock.patch.object(run_tests, "test_files", return_value=[r[0] for r in results]), \
                contextlib.redirect_stdout(printed):
            code = run_tests.main(list(argv))
        self.reruns = [call.args[0] for call in reruns.call_args_list]
        return code, printed.getvalue()


class AFailedRunIsKept(RunsMain):
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

    def test_a_failed_run_is_a_test_run_in_the_activity_page_and_pruned_by_the_runner(self):
        # #375: the Activity page labels the runner's logs; the runner prunes its own.
        from tagpup import logs
        self.assertEqual("Test run", logs.source_of("run_tests-20261008-101500"))
        self.assertEqual("Test run", logs.source_of("run_tests-20261008-101500-2"))
        for name in ("run_tests-20261008-101500.log", "run_tests-20261008-101500-2.log"):
            self.assertTrue(run_tests.FAILED_RUN.match(name), name)

    def test_only_the_latest_failed_runs_are_kept(self):
        for i in range(run_tests.KEEP_FAILED_RUNS + 3):
            with open(os.path.join(self.folder, "run_tests-20260101-0000%02d.log" % i), "w") as handle:
                handle.write("old")
        self.main([("test_fictional_b", False, 0, 0.2, "FAILED")])
        kept = sorted(os.listdir(self.folder))
        self.assertEqual(len(kept), run_tests.KEEP_FAILED_RUNS)
        self.assertNotIn("run_tests-20260101-000000.log", kept)


class AFileThatFailsUnderLoadIsRunAgainAlone(RunsMain):
    """A failed file is run once more alone: a pass is reported as flaky, never hidden, and
    two failures fail the run."""

    FAILS = ("test_fictional_b", False, 2, 0.2, "Traceback: the Kestrel file broke\nRan 2 tests in 0.2s\n\nFAILED")
    PASSES = lambda self, module: (module, True, 2, 0.1, "Ran 2 tests in 0.1s\n\nOK")  # noqa: E731

    def test_a_file_that_passes_alone_passes_the_run_and_says_so(self):
        code, printed = self.main([self.FAILS], again=self.PASSES)
        self.assertEqual(code, 0)
        self.assertEqual(self.reruns, ["test_fictional_b"])
        self.assertIn("flaky: passed alone: test_fictional_b", printed)
        self.assertIn("the Kestrel file broke", printed)
        self.assertIn("0 file(s) failed", printed)

    def test_the_first_failure_of_a_flaky_file_is_kept(self):
        self.main([self.FAILS], again=self.PASSES)
        kept = [os.path.join(self.folder, n) for n in os.listdir(self.folder)]
        self.assertEqual(len(kept), 1)
        with open(kept[0], encoding="utf-8") as handle:
            self.assertIn("the Kestrel file broke", handle.read())

    def test_a_file_that_fails_twice_fails_the_run_with_both_outputs(self):
        code, printed = self.main([self.FAILS])
        self.assertEqual(code, 1)
        self.assertIn("1 file(s) failed", printed)
        self.assertIn("the Kestrel file broke", printed)
        self.assertIn("failed again, alone", printed)
        self.assertNotIn("flaky: passed alone", printed)

    def test_a_file_that_passed_is_not_run_again(self):
        self.main([("test_fictional_a", True, 4, 0.1, "Ran 4 tests in 0.1s\n\nOK")])
        self.assertEqual(self.reruns, [])

    def test_no_retry_runs_nothing_again(self):
        code, _printed = self.main([self.FAILS], again=self.PASSES, argv=["--no-retry"])
        self.assertEqual(code, 1)
        self.assertEqual(self.reruns, [])

    def test_a_file_run_again_alone_keeps_the_time_it_took_alone(self):
        run_tests.save_durations({"test_fictional_b": 0.2})   # the pool's time, as run() kept it
        self.main([self.FAILS], again=self.PASSES)
        self.assertEqual(0.1, run_tests.load_durations()["test_fictional_b"], "the final time, not the first or both")


class TheTiersAreReportedAndChosen(unittest.TestCase):
    def test_each_tier_that_ran_has_a_line(self):
        from tests import tiers
        slow = sorted(tiers.SLOW)[0]
        scenario = sorted(tiers.SCENARIO)[0]
        lines = run_tests.tier_lines([
            ("test_fictional_fast", True, 3, 2.0, ""), (scenario, True, 5, 10.0, ""), (slow, True, 1, 30.0, "")], 2)
        self.assertEqual(3, len(lines))
        self.assertTrue(lines[0].lstrip().startswith("fast"))
        self.assertIn("scenario", lines[1])
        self.assertIn("slow", lines[2])

    def test_fast_leaves_the_other_tiers_out_and_the_default_leaves_none(self):
        from tests import tiers
        names = ["test_fictional_fast", sorted(tiers.SCENARIO)[0], sorted(tiers.SLOW)[0]]
        seen = {}
        for argv in ([], ["--fast"], ["--no-slow"], ["--all"]):
            with mock.patch.object(run_tests, "test_files", return_value=names), \
                    mock.patch.object(run_tests, "run", side_effect=lambda modules, jobs, accept_fewer=False: (
                        [(m, True, 1, 0.1, "") for m in modules])) as ran, \
                    contextlib.redirect_stdout(io.StringIO()):
                run_tests.main(argv)
            seen[tuple(argv)] = ran.call_args.args[0]
        self.assertEqual(names, seen[()])
        self.assertEqual(names, seen[("--all",)])
        self.assertEqual(names[:2], seen[("--no-slow",)])
        self.assertEqual(names[:1], seen[("--fast",)])


QUICK = """
import unittest


class Quick(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass
"""


class EveryRunKeepsItsTimes(unittest.TestCase):
    """Each file's time and each test's are kept on every run, green or not: the files the suite's speed work
    measures against (docs/plans/fast-trustworthy-test-suite.md, cut 1)."""

    def setUp(self):
        self.times = keep_times_in(self)

    def a_checkout(self, files):
        """A checkout of the test's own holding `files` under tests/, which the runner runs as it runs the real one,
        and the runner's tools/timed_unittest.py."""
        root = tempfile.mkdtemp(prefix="tagpup_run_tests_root_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "tests", "frontend"))
        os.makedirs(os.path.join(root, "tools"))
        shutil.copyfile(os.path.join(WORKSPACE_DIR, "tools", "timed_unittest.py"),
                        os.path.join(root, "tools", "timed_unittest.py"))
        for name, text in dict(files, **{"__init__.py": ""}).items():
            with open(os.path.join(root, "tests", name), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(text))
        tests = os.path.join(root, "tests")
        for name, value in (("ROOT", root), ("TESTS", tests), ("FRONTEND", os.path.join(tests, "frontend"))):
            patcher = mock.patch.object(run_tests, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_run_keeps_each_file_and_test_a_broken_import_too_and_forgets_a_deleted_file(self):
        self.a_checkout({"test_fictional_quick.py": QUICK, "test_fictional_broken.py": "import tagpup_no_such_module\n"})
        run_tests.save_durations({"test_fictional_gone": 9.0, run_tests.COUNTS: {"test_fictional_gone": 3}},
                                 {"test_fictional_gone": {"Gone.test_it": 1.0}})
        with contextlib.redirect_stdout(io.StringIO()):
            results = {r[0]: r for r in run_tests.run(["test_fictional_quick", "test_fictional_broken"], 2)}
        self.assertEqual((True, 2), results["test_fictional_quick"][1:3])
        self.assertFalse(results["test_fictional_broken"][1], "an import that fails is a failure")
        durations = run_tests.load_durations()
        tests = run_tests.load_durations(run_tests.TEST_DURATIONS)
        for kept in (durations, tests):
            self.assertIn("test_fictional_quick", kept)
            self.assertIn("test_fictional_broken", kept, "the broken file's time is kept beside its failure")
            self.assertNotIn("test_fictional_gone", kept, "a file deleted leaves nothing behind")
            self.assertIn("never edit it by hand", kept[run_tests.GENERATED])
        self.assertEqual({"test_fictional_quick": 2}, durations[run_tests.COUNTS])
        self.assertEqual(["Quick.test_one", "Quick.test_two"], sorted(tests["test_fictional_quick"]))
        self.assertIsInstance(durations["test_fictional_broken"], float)

    def test_a_run_stopped_while_writing_leaves_the_last_whole_file(self):
        run_tests.save_durations({"test_fictional_a": 1.5})

        def cut_off(_data, handle, **_options):
            handle.write('{"test_fictional_b": ')
            raise KeyboardInterrupt
        with mock.patch.object(run_tests.json, "dump", side_effect=cut_off), self.assertRaises(KeyboardInterrupt):
            run_tests.save_durations({"test_fictional_b": 2.5})
        self.assertEqual(1.5, run_tests.load_durations()["test_fictional_a"])
        run_tests.save_durations({"test_fictional_b": 2.5})   # the next run writes over what was cut off
        self.assertEqual((1.5, 2.5), tuple(run_tests.load_durations()[k] for k in ("test_fictional_a", "test_fictional_b")))

    def test_two_runs_saving_at_once_both_land(self):
        """The repo runs one suite at a time; a second run started anyway does not spoil the first's file: each save
        merges into the file on disk, one at a time."""
        program = textwrap.dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            import run_tests
            run_tests.DURATIONS = sys.argv[2]
            for i in range(10):
                name = "test_fictional_%s_%d" % (sys.argv[3], i)
                run_tests.save_durations({name: float(i), run_tests.COUNTS: {name: i}})
            """)
        tools = os.path.dirname(os.path.abspath(run_tests.__file__))
        runs = [run_tests.processes.start([sys.executable, "-c", program, tools, run_tests.DURATIONS, side],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for side in ("a", "b")]
        for each in runs:
            output = each.communicate(timeout=120)[0]
            self.assertEqual(0, each.returncode, output)
        durations = run_tests.load_durations()
        names = ["test_fictional_%s_%d" % (side, i) for side in "ab" for i in range(10)]
        self.assertEqual([], [n for n in names if n not in durations], "a save the other run wrote over")
        self.assertEqual([], [n for n in names if n not in durations[run_tests.COUNTS]])

    def test_slowest_prints_the_slowest_files_and_tests_and_runs_nothing(self):
        run_tests.save_durations({"test_fictional_a": 3.0, "test_fictional_b": 30.0, "kestrel.test.mjs": 12.0},
                                 {"test_fictional_b": {"B.test_slow": 20.0, "B.test_quick": 0.1}})
        printed = io.StringIO()
        with mock.patch.object(run_tests, "run", side_effect=AssertionError("ran")), contextlib.redirect_stdout(printed):
            self.assertEqual(0, run_tests.main(["--slowest", "2"]))
        text = printed.getvalue()
        self.assertLess(text.index("test_fictional_b"), text.index("kestrel.test.mjs"))
        self.assertIn("page", text)
        self.assertNotIn("test_fictional_a", text)
        self.assertIn("B.test_slow", text)


SPEC_SUMMARY = "\u2714 a kestrel nests (1.2ms)\n\u2139 tests %d\n\u2139 pass %d\n\u2139 fail 0\n"


class ThePageTestsAreATier(unittest.TestCase):
    """tests/frontend/*.test.mjs run under node, each file timed like a Python file; --fast leaves them out."""

    def run_page(self, returncode, stdout):
        with mock.patch.object(run_tests.processes, "run", return_value=finished(returncode, stdout)) as ran:
            _module, passed, count, _seconds, _output = run_tests.run_one("kestrel.test.mjs")
        return ran.call_args.args[0], passed, count

    def test_a_page_file_runs_under_node_and_counts_its_tests(self):
        command, passed, count = self.run_page(0, SPEC_SUMMARY % (4, 4))
        self.assertEqual(["node", "--test"], command[:2])
        self.assertTrue(command[-1].endswith(os.path.join("frontend", "kestrel.test.mjs")))
        self.assertEqual((True, 4), (passed, count))

    def test_a_page_file_that_stopped_before_its_summary_fails_and_one_that_skips_itself_passes(self):
        self.assertFalse(self.run_page(0, "\u2714 a kestrel nests\n")[1])
        self.assertFalse(self.run_page(1, SPEC_SUMMARY % (4, 3))[1])
        skipped = "\ufe63 against the server (0.4ms) # SKIP\n" + SPEC_SUMMARY % (0, 0)
        self.assertEqual((True, 0), self.run_page(0, skipped)[1:], "run by test_pages_against_the_real_server")

    def test_the_whole_run_holds_the_page_tier_and_the_loop_and_an_owners_run_leave_it_out(self):
        self.assertIn("harness.mjs", os.listdir(run_tests.FRONTEND))
        every = run_tests.test_files()
        self.assertNotIn("harness.mjs", every)
        self.assertEqual(sorted(n for n in os.listdir(run_tests.FRONTEND) if n.endswith(".test.mjs")),
                         [n for n in every if run_tests.tiers.tier_of(n) == "page"])
        seen = {}
        for argv in ([], ["--fast"], ["--no-slow"], ["--owner", "views"]):
            printed = io.StringIO()
            with mock.patch.object(run_tests, "run", side_effect=lambda modules, jobs, accept_fewer=False: (
                    [(m, True, 1, 0.1, "") for m in modules])) as ran, contextlib.redirect_stdout(printed):
                run_tests.main(argv)
            seen[tuple(argv)] = {run_tests.tiers.tier_of(m) for m in ran.call_args.args[0]}, printed.getvalue()
        self.assertIn("page", seen[()][0])
        self.assertRegex(seen[()][1], r"page +\d+ file")
        for loop in (("--fast",), ("--no-slow",), ("--owner", "views")):
            self.assertNotIn("page", seen[loop][0], loop)
        self.assertIn("node --test tests/frontend/", seen[("--owner", "views")][1])


if __name__ == "__main__":
    unittest.main()

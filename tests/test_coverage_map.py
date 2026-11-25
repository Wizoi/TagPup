"""The per-test coverage map (tools/test_map.py, tests/coverage_map.txt): which test entered which function of tagpup/.

A worker changes one function and asks which tests to run: tools/affected_tests.py names the tests the map says entered
it, not every file that imports the module. A change that runs at import reaches every test that entered the file; a
test file the map cannot see all of (it wrote nothing, or starts a Python child) is taken by its imports, as before.
An owner retiring a test asks what only that test reaches. The map is made by the tool, never by hand, and the kept one
is refused when it was edited or is more than a day of commits behind the code it describes.
"""
import contextlib
import difflib
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import affected_tests  # noqa: E402
import test_map  # noqa: E402

RESULT = "tagpup/core/result.py"
FAILURE_TEST = "AResult.test_each_failure_is_kept_and_read_out"


def diff_of(old, new):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), n=0))


class AMapMadeForOneTestFile(unittest.TestCase):
    """The real tool, its real tracer, over the real tests/test_result.py."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="tagpup_coverage_map_test_")
        out = os.path.join(cls.folder, "map.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.lines = test_map.generate(["test_result"], 1, out)
        cls.found = test_map.load(out)
        with open(os.path.join(ROOT, RESULT), encoding="utf-8") as handle:
            cls.source = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_it_names_each_test_and_the_functions_it_entered(self):
        self.assertIn(("test_result", FAILURE_TEST, "ok"), self.found.tests)
        self.assertTrue(self.found.trusted("test_result"))
        self.assertEqual({"test_result": {FAILURE_TEST}}, self.found.reaching([RESULT + "::Result.fail"]))
        self.assertFalse(self.found.edited())

    def test_a_change_inside_one_function_names_only_the_tests_that_entered_it(self):
        new = self.source.replace("self.errors.append(", "self.failed.append(  ", 1)
        self.assertNotEqual(new, self.source, "Result.fail appends to self.errors")
        keys = affected_tests.keys_for(self.found.keys_in(RESULT), RESULT, diff_of(self.source, new), self.source, new)
        self.assertEqual([RESULT + "::Result.fail"], keys)
        self.assertEqual({"test_result": {FAILURE_TEST}}, self.found.reaching(keys))

    def test_a_change_to_what_runs_at_import_reaches_every_test_that_entered_the_file(self):
        new = self.source.replace("    def fail(self, ", "    def fail(self,  ", 1)
        self.assertNotEqual(new, self.source)
        keys = affected_tests.keys_for(self.found.keys_in(RESULT), RESULT, diff_of(self.source, new), self.source, new)
        self.assertIn(RESULT + "::<module>", keys)
        self.assertEqual({"test_result": None}, self.found.reaching(keys), "its import is the file's shared setup")

    def test_a_function_deleted_reaches_the_tests_that_entered_it(self):
        start = self.source.index("    def fail(self, ")
        end = self.source.index("\n    def ", start + 1) + 1
        new = self.source[:start] + self.source[end:]
        keys = affected_tests.keys_for(self.found.keys_in(RESULT), RESULT, diff_of(self.source, new), self.source, new)
        self.assertIn(RESULT + "::<module>", keys, "its def line ran at import")

    def test_what_only_one_test_reaches_is_named_for_the_owner(self):
        self.assertIn(RESULT + "::Result.fail", self.found.unique("tests.test_result." + FAILURE_TEST))
        self.assertNotIn(RESULT + "::<module>", self.found.unique("tests.test_result." + FAILURE_TEST))

    def test_a_hand_edit_is_refused(self):
        edited = [line.replace(" ok", " failed") if line.startswith("test ") else line for line in self.lines]
        self.assertTrue(test_map.Map(edited).edited())
        self.assertEqual("the map was edited by hand", test_map.stale_reason(test_map.Map(edited), [], 0))


class AFileTheMapCannotSeeAllOf(unittest.TestCase):
    """A process killed before it wrote, or a test starting a Python child: its file is taken by its imports."""

    def found(self, status, children):
        contexts = {"<shared>": {"covered": [RESULT + "::<module>"], "status": "ok", "children": False},
                    "tests.test_result." + FAILURE_TEST: {"covered": [RESULT + "::Result.fail"], "status": "ok",
                                                          "children": children}}
        return test_map.Map(test_map.render([("test_result", status, {} if status == "unmapped" else contexts, 1.0)],
                                            "0" * 40, 0, 1.0, 1))

    def test_a_file_that_wrote_nothing_or_starts_a_child_is_not_trusted(self):
        self.assertTrue(self.found("ok", False).trusted("test_result"))
        self.assertFalse(self.found("unmapped", False).trusted("test_result"))
        self.assertFalse(self.found("ok", True).trusted("test_result"))
        self.assertFalse(self.found("failed", False).trusted("test_result"))

    def test_it_is_taken_whole_by_its_imports(self):
        _modules, _frontend, reached = affected_tests.affected([RESULT], None, self.found("unmapped", False))
        self.assertIsNone(reached["test_result"], "the whole file, as before the map")


class AStaleMap(unittest.TestCase):
    def setUp(self):
        self.found = test_map.Map(test_map.render([], "0" * 40, 1000, 1.0, 1))

    def test_code_changed_a_day_of_commits_later_is_stale(self):
        self.assertIsNotNone(test_map.stale_reason(self.found, ["tagpup/store/db.py"], 1000 + test_map.MAX_AGE + 1))

    def test_within_a_day_or_with_no_code_changed_it_holds(self):
        self.assertIsNone(test_map.stale_reason(self.found, ["tagpup/store/db.py"], 1000 + test_map.MAX_AGE))
        self.assertIsNone(test_map.stale_reason(self.found, ["tests/findings_cited.txt"], 1000 + 10 * test_map.MAX_AGE))


class TheKeptMap(unittest.TestCase):
    def test_it_is_in_step_with_the_code_it_describes(self):
        if os.environ.get(test_map.COVERAGE):
            self.skipTest("the map is being made: the one it replaces is stale by design")
        reason = test_map.check()
        self.assertIsNone(reason, "tests/coverage_map.txt: %s. Make it again: .venv/Scripts/python.exe "
                                  "tools/test_map.py (on a clean tree; commit the map it writes)." % reason)


if __name__ == "__main__":
    unittest.main()

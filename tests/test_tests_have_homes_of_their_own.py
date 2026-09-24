"""No test uses the checkout's data/ folder or config.ini.

They are the folder and the settings of the app somebody is using. Tests made their
libraries in data/ and wrote config.ini (docs/findings.md, #14): their files turned up
in the library picker, which kept a list of names to hide them, a library left by a run
on a later schema broke a run on an earlier one, and since the names were fixed the
files that used them could not run at once -- tools/run_tests.py ran them one after
another, and that lane took as long as all the other files together.

A test that needs a library, settings, logs or anything else beside them takes a home
of its own from tests/own_home.py. What counts as naming the checkout's is
tools/run_tests.py's SHARED, which also decides whether a file must run alone.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(WORKSPACE_DIR, "tests")
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "tools"))
sys.path.insert(0, TESTS)

import own_home  # noqa: E402
import run_tests  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402

#: A quote, so that the spellings below are not themselves what they describe.
Q = '"'


def source(name):
    with open(os.path.join(TESTS, name), encoding="utf-8") as handle:
        return handle.read()


class NoTestNamesTheCheckoutsDataOrSettings(unittest.TestCase):
    def test_no_file_in_tests_does(self):
        found = [name for name in sorted(os.listdir(TESTS))
                 if name.endswith(".py") and run_tests.SHARED.search(source(name))
                 and name[:-3] not in run_tests.READS_CODE_NOT_DATA]
        self.assertEqual([], found, "give these a home of their own (tests/own_home.py)")

    def test_so_the_runner_runs_none_of_them_alone(self):
        self.assertEqual([], [module for module in run_tests.test_files()
                              if run_tests.uses_the_checkout(module)])

    def test_each_exemption_is_a_file_that_still_needs_it(self):
        for module in run_tests.READS_CODE_NOT_DATA:
            self.assertTrue(run_tests.SHARED.search(source(module + ".py")),
                            "%s no longer names config.ini; drop it from READS_CODE_NOT_DATA" % module)


class WhatCountsAsNamingThem(unittest.TestCase):
    """The spellings the tests used, and the ones a home of their own uses instead."""

    def test_the_spellings_the_tests_used(self):
        for text in ("os.path.join(WORKSPACE_DIR, %sdata%s, %stest_x.db%s)" % (Q, Q, Q, Q),
                     "os.path.join(project_root, %sdata%s, %stest_x.db%s)" % (Q, Q, Q, Q),
                     "os.path.join(WORKSPACE_DIR, %sconfig.ini%s)" % (Q, Q),
                     "os.path.join(%sdata%s, %sphoto_index.db%s)" % (Q, Q, Q, Q),
                     "set_active_db_path(%sdata/regatta.db%s)" % (Q, Q),
                     "open(%sconfig.ini%s)" % (Q, Q)):
            self.assertTrue(run_tests.SHARED.search(text), text)

    def test_the_spellings_of_a_home_of_its_own(self):
        for text in ("home.library(%stest_x.db%s)" % (Q, Q),
                     "os.path.join(self.home, %sdata%s, %sharbour.db%s)" % (Q, Q, Q, Q),
                     "config.config_path(self.home)",
                     "re.compile(r%sdata%s.get%s)" % (Q, chr(92), Q),
                     "the checkout's config.ini, in a sentence"):
            self.assertFalse(run_tests.SHARED.search(text), text)


class AHomeOfItsOwn(unittest.TestCase):
    def test_it_is_tagpup_home_while_it_is_open_and_gone_after(self):
        before = os.environ.get("TAGPUP_HOME")
        home = own_home.OwnHome("tagpup_own_home_")
        try:
            self.assertEqual(os.path.normcase(tagpup_config.home()), os.path.normcase(home.root))
            self.assertEqual(os.path.normcase(tagpup_config.data_dir()), os.path.normcase(home.data))
            self.assertEqual(os.path.normcase(tagpup_config.library_path("harbour.db")),
                             os.path.normcase(home.library("harbour.db")))
        finally:
            home.close()
        self.assertEqual(before, os.environ.get("TAGPUP_HOME"))
        self.assertFalse(os.path.exists(home.root))

    def test_one_for_a_test_goes_when_the_test_does(self):
        test = unittest.TestCase()
        home = own_home.for_test(test)
        self.assertTrue(os.path.isdir(home.data))
        test.doCleanups()
        self.assertFalse(os.path.exists(home.root))


if __name__ == "__main__":
    unittest.main()

"""Each test's seconds, for tools/run_tests.py, which names this module first on unittest's command line:

    TAGPUP_TEST_TIMES=<times.json> python -m unittest tools.timed_unittest tests.test_x

The run is still `python -m unittest` (tagpup.ml.under_test knows a test run by it), its output and exit code
unittest's own, and this module holds no tests. Imported before the run starts, it gives unittest's runner a result
that times each test, setUp and tearDown included, and writes {test id: seconds} to the file TAGPUP_TEST_TIMES names
when the process ends: a test file whose import failed included, as unittest's stand-in test. A process killed
part-way writes none, and the runner keeps the file's time without them.
"""
import atexit
import json
import os
import time
import unittest

#: The variable that names the file the times go to (tools/run_tests.py sets it for each file's process).
TIMES = "TAGPUP_TEST_TIMES"
_seconds = {}


class TimedResult(unittest.TextTestResult):
    def startTest(self, test):
        # The class's id(), not the instance's: a test may keep a value of its own called `id`.
        self._timed = (type(test).id(test), time.perf_counter())
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        name, began = self._timed
        _seconds[name] = round(time.perf_counter() - began, 3)


def _write(path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_seconds, handle)


unittest.TextTestRunner.resultclass = TimedResult
atexit.register(_write, os.environ[TIMES])

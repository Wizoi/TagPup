"""The test homes' reaper (tests/own_home.py) outlives the test process on purpose, and says nothing of it.

tests/test_bulk_delete.py printed "ResourceWarning: subprocess N is still running" (docs/findings.md, #743): the
first test home started the reaper and dropped its handle, and Python warned when it collected the handle while the
child ran -- in whichever test was running then. It looked like a test that leaked a process; it was the one
process meant to outlive them. The handle is held now.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from tagpup.core import processes  # noqa: E402

#: Starts the reaper as the first test home does, collects garbage, and ends as a test process does.
PROGRAM = """
import gc, os, sys, tempfile
sys.path.insert(0, %r)
import own_home
own_home._reap_after_exit(tempfile.mkdtemp(prefix="tagpup_reaper_quiet_"))
gc.collect()
print("collected")
"""


class TheReaperIsHeld(unittest.TestCase):
    def test_collecting_garbage_while_it_runs_warns_of_nothing(self):
        done = processes.run([sys.executable, "-W", "default", "-c", PROGRAM % HERE],
                             capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
        self.assertIn("collected", done.stdout, done.stderr)
        self.assertNotIn("ResourceWarning", done.stderr)


if __name__ == "__main__":
    unittest.main()

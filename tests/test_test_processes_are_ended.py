"""A test that starts servers ends every process it started, and every one they started,
however it ends (tests/own_home.py, end_processes; docs/findings.md, #727).

tests/test_supervisor_hand_over.py failed part-way once and left four processes running
for hours: its cleanup ended the supervisor and the server its records named at that
moment, and a supervisor started to take over -- named by no record yet -- then started
a server of its own.
"""
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: Starts a process of its own from the same folder and exits, leaving it running: what a
#: supervisor handing over does.
STARTER = '''import os, sys
sys.path.insert(0, %r)
from tagpup.core import processes
here = os.path.dirname(os.path.abspath(__file__))
processes.start([sys.executable, os.path.join(here, "waits.py")], own_group=True)
''' % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(os.name == "nt", "a process's command line is read from Windows")
class EndingWhatATestStarted(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="ended_")
        self.addCleanup(own_home.end_processes, self.home.root)

    def alive(self, pid):
        return processes.started(pid) is not None

    def test_a_process_left_by_one_the_test_started_is_ended_too(self):
        with open(os.path.join(self.home.root, "starts.py"), "w", encoding="utf-8") as handle:
            handle.write(STARTER)
        with open(os.path.join(self.home.root, "waits.py"), "w", encoding="utf-8") as handle:
            handle.write("import time\ntime.sleep(600)\n")
        first = processes.start([sys.executable, os.path.join(self.home.root, "starts.py")],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: first.poll() is None and processes.kill_tree(first.pid))
        self.assertEqual(0, first.wait(timeout=60))
        # What the test knows of has ended; what it left has not, and no record names it.
        deadline = time.time() + 30
        while not own_home._named_under(self.home.root):
            self.assertLess(time.time(), deadline, "the process it starts never ran")
            time.sleep(0.2)
        self.assertTrue(own_home.end_processes(self.home.root))
        self.assertEqual([], own_home._named_under(self.home.root), "a process started from the home still runs")

    def test_a_server_named_only_by_its_record_is_ended(self):
        """A server run from the code's folder names no home on its command line."""
        script = ("import sys, time; sys.path.insert(0, %r); from tagpup import launcher; "
                  "launcher.say_where({'tagpup': 1}, None, 't'); time.sleep(600)"
                  % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        server = processes.start([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: server.poll() is None and processes.kill_tree(server.pid))
        deadline = time.time() + 60
        while launcher.record(1) is None:
            self.assertIsNone(server.poll(), "it did not start")
            self.assertLess(time.time(), deadline, "it never said where it answers")
            time.sleep(0.1)
        recorded = launcher.record(1)["pid"]
        self.assertTrue(own_home.ran_processes(self.home.root))
        own_home.end_processes(self.home.root)
        self.assertIsNotNone(server.wait(timeout=30))
        self.assertFalse(self.alive(recorded))


if __name__ == "__main__":
    unittest.main()

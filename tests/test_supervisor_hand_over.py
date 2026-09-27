"""The hand-over to a new version's supervisor, for real (tagpup.supervisor): two installed
versions, the real supervisor from each started as the Startup shortcut starts it (the
stable launcher, TagPup Background.pyw), and the real server on ports of the test's own,
in a home of the test's own.

It used to stop the server, start the new supervisor, and go: a new version whose
supervisor could not start left nothing running, unattended. Now the new one must say it
is up before the server is stopped, and take the lock after; if it does not, the old
version goes on.
"""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.request

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install_app  # noqa: E402
import own_home  # noqa: E402
from free_port import free_port  # noqa: E402
from tagpup import supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

OLD, NEW = "20260926-100000-aaaaaaa", "20260926-110000-bbbbbbb"


@unittest.skipUnless(os.name == "nt", "the always-on process is Windows'")
class HandingOver(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="hand_over_")
        self.installed = os.path.join(self.home.root, "installed")
        install_app.install(self.installed, self.home.root, sys.executable, name=OLD, apply=True, say=lambda line: None)
        self.port = free_port()
        self.addCleanup(self.end_all)

    def end_all(self):
        for record in (supervisor.running(), supervisor.server()):
            if record:
                processes.kill_tree(record["pid"])

    def start(self):
        # A server a test starts runs no recurring job and loads no model weights.
        env = dict(os.environ, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1", TAGPUP_WEB_NO_WARMUP="1")
        server_args = "--tagpup-port %d --tuner-port %d --db sandbox" % (self.port, free_port())
        first = processes.start([sys.executable, os.path.join(self.installed, supervisor.BACKGROUND_LAUNCHER),
                                 "--server-args", server_args, "--update-every", "0.5", "--hand-over-wait", "20",
                                 "--settle", "3"],
                                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: first.poll() is None and processes.kill_tree(first.pid))
        return first

    def version(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % self.port, timeout=5) as reply:
                return json.loads(reply.read().decode("utf-8"))["version"]
        except OSError:
            return None

    def wait_until(self, check, seconds=90):
        deadline = time.time() + seconds
        while True:
            found = check()
            if found:
                return found
            self.assertLess(time.time(), deadline, "timed out")
            time.sleep(0.2)

    def test_the_new_versions_supervisor_takes_over_and_its_server_answers(self):
        first = self.start()
        self.wait_until(lambda: self.version() == OLD)
        old_pid = supervisor.running()["pid"]
        install_app.install(self.installed, self.home.root, sys.executable, name=NEW, apply=True, say=lambda line: None)
        self.wait_until(lambda: self.version() == NEW)
        now = supervisor.running()
        self.assertNotEqual(old_pid, now["pid"])
        self.assertEqual(NEW, now["version"])
        self.assertIsNotNone(first.wait(timeout=30), "the old supervisor is still running")
        self.assertFalse(os.path.exists(supervisor.data_file(supervisor.HANDOVER_FILE)),
                         "the hand-over's note was left behind")

    def test_a_new_version_whose_server_cannot_start_leaves_the_old_one_answering(self):
        """The reviewer's reproduction: the new version's supervisor starts, takes the
        lock, and its server fails to import. The old supervisor had already gone; the
        new one gave up after five crashes, and the next login started the same release
        again. Now the old one waits for the new server to answer, and takes back."""
        first = self.start()
        self.wait_until(lambda: self.version() == OLD)
        old_pid = supervisor.running()["pid"]
        install_app.install(self.installed, self.home.root, sys.executable, name=NEW, apply=True, say=lambda line: None)
        with open(os.path.join(self.installed, "versions", NEW, "tagpup_web.py"), "w", encoding="utf-8") as handle:
            handle.write("raise ImportError('a broken release')\n")
        log = os.path.join(self.home.data, "logs", "supervisor.log")
        self.wait_until(lambda: os.path.exists(log) and "did not take over" in open(log, encoding="utf-8").read())
        self.wait_until(lambda: self.version() == OLD)
        self.assertIsNone(first.poll(), "the old supervisor went")
        self.assertEqual(old_pid, supervisor.running()["pid"])
        self.assertEqual(OLD, install_app.read_current(self.installed), "the next login would start the broken release")

    def test_a_new_version_whose_supervisor_cannot_start_leaves_the_old_one_answering(self):
        self.start()
        self.wait_until(lambda: self.version() == OLD)
        old_pid = supervisor.running()["pid"]
        install_app.install(self.installed, self.home.root, sys.executable, name=NEW, apply=True, say=lambda line: None)
        # The new version's supervisor cannot even be imported.
        with open(os.path.join(self.installed, "versions", NEW, "tagpup", "supervisor.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("raise ImportError('a broken release')\n")
        log = os.path.join(self.home.data, "logs", "supervisor.log")
        self.wait_until(lambda: os.path.exists(log) and "did not start" in open(log, encoding="utf-8").read())
        self.assertEqual(OLD, self.version())
        state = supervisor.running()
        self.assertEqual(old_pid, state["pid"])
        self.assertEqual(OLD, state["server_version"])
        # And it takes work: the drain before the hand-over was undone.
        with urllib.request.urlopen("http://127.0.0.1:%d/sandbox/api/tags" % self.port, timeout=10) as reply:
            self.assertEqual(200, reply.status)


if __name__ == "__main__":
    unittest.main()

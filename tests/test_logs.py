"""Log files: each program writes one, and the servers put slow and failed requests in it.

The apps logged only to a console, which scrolled away and closed with its window, so
"was that slow on the server or in the browser?" had nothing to answer it afterwards.
"""
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
import subprocess
import time
import unittest
import urllib.request
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup import logs as tagpup_logs  # noqa: E402
from tagpup.core import processes  # noqa: E402
from free_port import free_port  # noqa: E402


def file_handlers_for(path):
    return [h for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)
            and os.path.normcase(h.baseFilename) == os.path.normcase(path)]


def detach(path):
    for handler in file_handlers_for(path):
        logging.getLogger().removeHandler(handler)
        handler.close()


class InAHomeOfItsOwn(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_logs_")
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)


class EachProgramWritesItsOwnFile(InAHomeOfItsOwn):
    def test_it_is_under_the_homes_data_folder(self):
        path = tagpup_logs.to_file("tagpup")
        self.addCleanup(detach, path)
        self.assertEqual(path, os.path.join(self.home, "data", "logs", "tagpup.log"))
        logging.getLogger("tagpup.test").warning("a line for the file")
        file_handlers_for(path)[0].flush()
        with open(path, encoding="utf-8") as handle:
            written = handle.read()
        self.assertIn("tagpup.test - a line for the file", written)
        # Plain text: the console's colour codes around the level are not in the file.
        self.assertRegex(written, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \[WARNING\] ")

    def test_asking_twice_adds_one_file_handler(self):
        path = tagpup_logs.to_file("tagtuner")
        self.addCleanup(detach, path)
        self.assertEqual(tagpup_logs.to_file("tagtuner"), path)
        self.assertEqual(len(file_handlers_for(path)), 1)

    def test_it_rotates_rather_than_growing_for_ever(self):
        path = tagpup_logs.to_file("runner")
        self.addCleanup(detach, path)
        handler = file_handlers_for(path)[0]
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual((handler.maxBytes, handler.backupCount), (5 * 1024 * 1024, 5))


class AServerLogsToItsFile(unittest.TestCase):
    """The real server (tagpup_web.py), in a home of its own, writing a real log file:
    what the pages' slow and failed requests go to (tagpup.web.app, tested through the
    test client in tests/test_web_app.py)."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="tagpup_logs_server_")
        cls.tagpup_port, cls.tuner_port = free_port(), free_port()
        cls.process = processes.start(
            [sys.executable, os.path.join(WORKSPACE_DIR, "tagpup_web.py"), "--db", "test_logs",
             "--tagpup-port", str(cls.tagpup_port), "--tuner-port", str(cls.tuner_port)],
            env=dict(os.environ, TAGPUP_HOME=cls.home, TAGPUP_WEB_NO_WARMUP="1", TAGPUP_NO_MODEL_WEIGHTS="1"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/databases" % cls.tuner_port, timeout=5)
                break
            except OSError:
                if cls.process.poll() is not None:
                    raise AssertionError("the server exited before it was ready") from None
                time.sleep(0.1)
        cls.log_path = os.path.join(cls.home, "data", "logs", "tagpup_web.log")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
        shutil.rmtree(cls.home, ignore_errors=True)
        if os.path.exists(cls.home):
            print("\nnote: could not delete %s yet (held open by the test server)" % cls.home,
                  file=sys.stderr)

    def log_text(self, expected, seconds=5):
        deadline = time.time() + seconds
        while True:
            text = ""
            if os.path.exists(self.log_path):
                with open(self.log_path, encoding="utf-8") as handle:
                    text = handle.read()
            if expected in text or time.time() > deadline:
                return text
            time.sleep(0.05)

    def test_the_server_writes_its_log_in_the_home(self):
        self.assertIn("Serving", self.log_text("Serving"))

    def test_a_quick_request_is_not_logged(self):
        urllib.request.urlopen("http://127.0.0.1:%d/api/databases?probe=quick" % self.tagpup_port)
        self.assertNotIn("probe=quick", self.log_text("probe=quick", seconds=0.5))


if __name__ == "__main__":
    unittest.main()

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
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import localserver  # noqa: E402
from tagpup import logs as tagpup_logs  # noqa: E402
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


class DroppedConnections(unittest.TestCase):
    def test_a_browser_dropping_a_request_is_not_an_error(self):
        # Thumbnails scrolled past are abandoned by the browser; each one is not a
        # traceback in the log.
        try:
            raise ConnectionResetError("reset by peer")
        except ConnectionResetError:
            with self.assertNoLogs(tagpup_logs.REQUESTS, level="WARNING"):
                localserver.ThreadedHTTPServer.handle_error(object(), None, ("127.0.0.1", 5))

    def test_both_apps_time_their_requests(self):
        import tagpup_server
        import tuner_server
        self.assertTrue(issubclass(tagpup_server.TagPupHTTPRequestHandler, localserver.RequestLog))
        self.assertTrue(issubclass(tuner_server.TunerHTTPRequestHandler, localserver.RequestLog))


class AServerLogsToItsFile(unittest.TestCase):
    """A real TagTuner server, in a home of its own, writing a real log file."""

    @classmethod
    def setUpClass(cls):
        import tuner_server
        from tagpup_server import create_library

        cls.tuner_server = tuner_server
        cls.home = tempfile.mkdtemp(prefix="tagpup_logs_server_")
        cls.environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": cls.home})
        cls.environ.start()
        cls.log_path = tagpup_logs.to_file("tagtuner")
        db_path = os.path.join(cls.home, "data", "test_logs.db")
        create_library(db_path)
        cls.port = free_port()
        threading.Thread(target=tuner_server.start_server, daemon=True,
                         kwargs={"port": cls.port, "db_path": db_path,
                                 "gui_dir": os.path.join(WORKSPACE_DIR, "gui")}).start()
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/databases" % cls.port, timeout=5)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        detach(cls.log_path)
        cls.environ.stop()
        shutil.rmtree(cls.home, ignore_errors=True)
        if os.path.exists(cls.home):
            print("\nnote: could not delete %s yet (held open by the test server)" % cls.home,
                  file=sys.stderr)

    def log_text(self, expected, seconds=5):
        """The log's text once it holds `expected`; a failed request is logged after the
        client has already seen the connection close."""
        deadline = time.time() + seconds
        while True:
            for handler in file_handlers_for(self.log_path):
                handler.flush()
            with open(self.log_path, encoding="utf-8") as handle:
                text = handle.read()
            if expected in text or time.time() > deadline:
                return text
            time.sleep(0.05)

    def test_a_slow_request_is_logged_with_its_time(self):
        handler_class = self.tuner_server.TunerHTTPRequestHandler
        with mock.patch.object(handler_class, "slow_request_seconds", 0):
            urllib.request.urlopen("http://127.0.0.1:%d/api/databases?probe=slow" % self.port)
            # The handler reads its threshold after the answer is sent, so the reply can
            # arrive first. Undoing the patch then raced it; under the full suite the
            # race was lost and nothing was logged.
            text = self.log_text("probe=slow")
        self.assertRegex(text, r"\[WARNING\] .* tagpup\.requests - slow: "
                               r"GET /api/databases\?probe=slow HTTP/1\.1 took \d+\.\d\ds")

    def test_a_quick_request_is_not(self):
        urllib.request.urlopen("http://127.0.0.1:%d/api/databases?probe=quick" % self.port)
        self.assertNotIn("probe=quick", self.log_text("probe=quick", seconds=0.5))

    def test_a_failed_request_is_logged_with_its_traceback(self):
        handler_class = self.tuner_server.TunerHTTPRequestHandler
        with mock.patch.object(handler_class, "do_GET",
                               side_effect=RuntimeError("the handler broke")):
            with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
                urllib.request.urlopen("http://127.0.0.1:%d/api/databases" % self.port)
        text = self.log_text("the handler broke")
        self.assertIn("a request from 127.0.0.1 failed", text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("RuntimeError: the handler broke", text)


if __name__ == "__main__":
    unittest.main()

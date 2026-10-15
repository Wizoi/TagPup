"""Log files: each program writes one, and the servers put slow and failed requests in it.

The apps logged only to a console, which scrolled away and closed with its window, so
"was that slow on the server or in the browser?" had nothing to answer it afterwards.

No log file grows without bound (docs/ARCHITECTURE.md, "Runtime": logs). The always-on process runs for weeks, and
the Activity page reads the logs. Every log is written by a RotatingFileHandler made in one place, tagpup.logs.to_file
-- 5 MB, five kept -- and the one file written otherwise, the server's console output (a handle a launch or an install
hands the child), is rotated each time the server starts.

Lifted: test_logs_are_bounded, and from this file the check that the handler is a RotatingFileHandler of 5 MB / 5
(ToFileRotates rotates a real one at its size, and the numbers are asserted there). Mutation: to_file making a plain
FileHandler fails ToFileRotates; KEEP = 0 fails it too.
"""
import ast
import logging
import logging.handlers
import os
import re
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

from free_port import free_port  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402

from tagpup import logs as tagpup_logs  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: The one module that makes a log handler writing a file.
HANDLER_OWNER = os.path.join("tagpup", "logs.py")

#: Files opened for appending in shipped code, and why each is bounded.
APPENDED = {
    os.path.join("tagpup", "launcher.py"): "the output of a server an install started, rotated the same way as it starts",
}

FILE_HANDLERS = re.compile(r"\b(?:FileHandler|RotatingFileHandler|TimedRotatingFileHandler|WatchedFileHandler)\s*\(")


def appends(source):
    """Each open(...) whose mode appends, in `source`: [line]."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "open"):
            continue
        modes = [arg for arg in node.args[1:2]] + [kw.value for kw in node.keywords if kw.arg == "mode"]
        for mode in modes:
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "a" in mode.value:
                found.append(node.lineno)
    return found


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


class EveryLogIsBounded(unittest.TestCase):
    def test_the_output_of_a_server_an_install_starts_is_rotated_as_it_starts(self):
        with open(os.path.join(ROOT, "tagpup", "launcher.py"), encoding="utf-8") as handle:
            source = handle.read()
        start = source[source.index("def start_server("):source.index("def wait_for(")]
        self.assertLess(start.index("CONSOLE_LOG_MAX"), start.index('open(log_path, "ab")'))

    def test_only_tagpup_logs_makes_a_file_handler_and_nothing_else_appends_for_ever(self):
        handlers, appending = [], []
        for relative in python_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                text = handle.read()
            if relative != HANDLER_OWNER and FILE_HANDLERS.search(text):
                handlers.append(relative)
            if "basicConfig" in text and re.search(r"basicConfig\([^)]*filename\s*=", text, re.S):
                handlers.append(relative + " (basicConfig with a file)")
            lines = appends(text)
            if lines and relative not in APPENDED:
                appending.append("%s: line %s" % (relative, ", ".join(map(str, lines))))
        self.assertEqual([], handlers, "log to a file through tagpup.logs.to_file, which rotates")
        self.assertEqual([], appending, "a file appended to for ever grows for ever; rotate it (tagpup.logs)")
        self.assertEqual([1], appends('open(path, "a")\n'))
        self.assertEqual([1], appends('open(path, mode="ab")\n'))
        self.assertEqual([], appends('open(path, "rb")\n'))


class ToFileRotates(InAHomeOfItsOwn):
    def setUp(self):
        super().setUp()
        self.logger = logging.getLogger("tests.logs_bounded")
        self.logger.propagate = False
        self.addCleanup(setattr, self.logger, "propagate", True)

    def test_a_programs_log_rotates_at_its_size_and_keeps_a_few(self):
        with mock.patch.object(tagpup_logs, "MAX_BYTES", 2000), mock.patch.object(tagpup_logs, "KEEP", 2):
            path = tagpup_logs.to_file("bounded")
        root = logging.getLogger()
        handler = file_handlers_for(path)[0]
        self.addCleanup(lambda: (root.removeHandler(handler), handler.close()))
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)
        for i in range(200):
            self.logger.warning("line %d %s", i, "x" * 50)
        handler.flush()
        folder = os.path.dirname(path)
        names = sorted(name for name in os.listdir(folder) if name.startswith("bounded"))
        self.assertEqual(["bounded.log", "bounded.log.1", "bounded.log.2"], names)
        for name in names:
            self.assertLessEqual(os.path.getsize(os.path.join(folder, name)), 2000 + 200)

    def test_each_programs_log_including_the_indexers_is_made_by_to_file_at_5_mb_five_kept(self):
        self.assertEqual((5 * 1024 * 1024, 5), (tagpup_logs.MAX_BYTES, tagpup_logs.KEEP))
        with open(os.path.join(ROOT, "tagpup_cli.py"), encoding="utf-8") as handle:
            self.assertIn("_tagpup_logs.to_file(_tagpup_logs.run_log(", handle.read())


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
            env=dict(os.environ, TAGPUP_HOME=cls.home, TAGPUP_WEB_NO_WARMUP="1", TAGPUP_NO_MODEL_WEIGHTS="1",
                     TAGPUP_NO_JOBS="1", TAGPUP_SERVERS=os.path.join(cls.home, "servers")),
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
        processes.kill_tree(cls.process.pid)
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

"""No log file TagPup writes grows without bound (docs/ARCHITECTURE.md, "Runtime": logs).

The always-on process runs for weeks. A log it appended to for ever would fill the disk
and, read by the Activity page, take longer to read each time. Every log is written by a
RotatingFileHandler made in one place, tagpup.logs.to_file -- 5 MB, five kept -- and
the one file written otherwise, the server's console output (its stdout and stderr, a
handle the supervisor hands the child), is rotated each time the server starts: the
server logs to its own file, so what reaches the console is only what escapes logging
(a crash before logging is set up, an interpreter's fatal error).
"""
import ast
import logging
import logging.handlers
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402

from tagpup import logs  # noqa: E402

#: The one module that makes a log handler writing a file.
HANDLER_OWNER = os.path.join("tagpup", "logs.py")

#: Files opened for appending in shipped code, and why each is bounded.
APPENDED = {
    os.path.join("tagpup", "supervisor.py"): "the server's console output, rotated at CONSOLE_LOG_MAX as each server starts",
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


class EveryLogIsBounded(unittest.TestCase):
    def test_only_tagpup_logs_makes_a_file_handler(self):
        found = []
        for relative in python_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                text = handle.read()
            if relative != HANDLER_OWNER and FILE_HANDLERS.search(text):
                found.append(relative)
            if "basicConfig" in text and re.search(r"basicConfig\([^)]*filename\s*=", text, re.S):
                found.append(relative + " (basicConfig with a file)")
        self.assertEqual([], found, "log to a file through tagpup.logs.to_file, which rotates")

    def test_nothing_else_appends_to_a_file_for_ever(self):
        found = []
        for relative in python_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                lines = appends(handle.read())
            if lines and relative not in APPENDED:
                found.append("%s: line %s" % (relative, ", ".join(map(str, lines))))
        self.assertEqual([], found, "a file appended to for ever grows for ever; rotate it (tagpup.logs)")

    def test_the_guard_sees_an_append(self):
        self.assertEqual([1], appends('open(path, "a")\n'))
        self.assertEqual([1], appends('open(path, mode="ab")\n'))
        self.assertEqual([], appends('open(path, "rb")\n'))

    def test_the_consoles_output_is_rotated_as_the_server_starts(self):
        with open(os.path.join(ROOT, "tagpup", "supervisor.py"), encoding="utf-8") as handle:
            source = handle.read()
        start = source[source.index("    def start_child(self):"):source.index("    def stop_child(self):")]
        self.assertLess(start.index("CONSOLE_LOG_MAX"), start.index('open(log_path, "ab")'),
                        "the console's output is rotated before it is appended to")


class ToFileRotates(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="logs_bounded_")
        self.logger = logging.getLogger("tests.logs_bounded")
        self.logger.propagate = False
        self.addCleanup(setattr, self.logger, "propagate", True)

    def test_a_programs_log_rotates_at_its_size_and_keeps_a_few(self):
        with mock.patch.object(logs, "MAX_BYTES", 2000), mock.patch.object(logs, "KEEP", 2):
            path = logs.to_file("bounded")
        root = logging.getLogger()
        handler = [h for h in root.handlers if isinstance(h, logging.FileHandler) and h.baseFilename == path][0]
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

    def test_each_programs_log_including_the_indexers_is_made_by_to_file(self):
        self.assertGreater(logs.MAX_BYTES, 0)
        self.assertGreater(logs.KEEP, 0)
        with open(os.path.join(ROOT, "tagpup_cli.py"), encoding="utf-8") as handle:
            self.assertIn("_tagpup_logs.to_file(_tagpup_logs.run_log(", handle.read())


if __name__ == "__main__":
    unittest.main()

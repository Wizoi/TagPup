"""An ExifTool session must not hang on its own error output.

pyexiftool reads a command's stdout to the end before it reads any stderr. ExifTool
writes a line to stderr per troublesome file, and on Windows the pipe holds about
4 KB: a batch of a hundred photos that each draw a warning fills it, ExifTool blocks
writing the next line, and pyexiftool waits for stdout forever. Two maintenance
scripts sat at 0% CPU for two days like that.

The reproduction needs no photo: a batch of files that do not exist draws one
"File not found" line each, which is the same flood through the same pipe. Every
call here runs under a watchdog, so the old code fails these tests rather than
hanging the suite.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from exiftool.exceptions import ExifToolExecuteError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession, ExifToolTimeout  # noqa: E402


#: Where the machine has ExifTool; the checkout's settings are not read.
EXIFTOOL = own_home.installed_exiftool()

#: Long enough for a real answer many times over; short enough that a hang fails.
WATCHDOG = 60


def _within(seconds, et, fn):
    """Run fn() in a thread. If it is still going after `seconds`, kill ExifTool
    (so the thread and the process both end) and report the hang."""
    outcome = {}

    def target():
        try:
            outcome["value"] = fn()
        except BaseException as e:  # the test inspects whatever happened
            outcome["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        process = getattr(et, "_process", None)
        if process is not None:
            process.kill()
        t.join(10)
        outcome["hung"] = True
    return outcome


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TestAFloodOfErrorsDoesNotHang(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="exiftool_session_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        folder = os.path.join(self.tmpdir, "2031-05-02 - Harbour Relay at Fernwick Point")
        # Nothing is created: each of these draws one "File not found - <path>" line,
        # ~120 bytes, so a hundred of them is three times what the pipe holds.
        self.missing = [os.path.join(folder, "Harbour Relay - %03d - Ottilie Varga.jpg" % i)
                        for i in range(100)]
        self.et = ExifToolSession(executable=EXIFTOOL)
        self.et.run()
        self.addCleanup(self._stop)

    def _stop(self):
        if self.et.running:
            self.et.terminate()

    def test_a_batch_that_fills_the_stderr_pipe_still_answers(self):
        outcome = _within(WATCHDOG, self.et,
                          lambda: self.et.get_tags(self.missing, tags=["XMP:Subject"]))

        self.assertNotIn("hung", outcome, "ExifTool deadlocked on a full stderr pipe")
        # ExifTool exits 1 when any file is unreadable, and ExifToolHelper raises on
        # that; the point is that the answer arrived, with every error in it.
        self.assertIsInstance(outcome.get("error"), ExifToolExecuteError)
        self.assertEqual(self.et.last_stderr.count("File not found"), len(self.missing))

    def test_the_session_is_still_usable_afterwards(self):
        _within(WATCHDOG, self.et,
                lambda: self.et.get_tags(self.missing, tags=["XMP:Subject"]))
        outcome = _within(WATCHDOG, self.et, lambda: self.et.execute("-ver"))
        self.assertNotIn("hung", outcome)
        self.assertRegex(outcome.get("value", ""), r"^\d+\.\d+")


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TestAStalledCommandFailsLoudly(unittest.TestCase):
    """A command that never finishes must end in an error, not a wait.

    "-" as a file name makes ExifTool read the image from its stdin, which in this
    mode is the command pipe: it waits there for a file that never comes. That is a
    stand-in for anything else that stalls it -- a dead network share, a file that
    will not open.
    """

    def setUp(self):
        self.et = ExifToolSession(executable=EXIFTOOL, timeout=3)
        self.et.run()
        self.addCleanup(self._stop)

    def _stop(self):
        if self.et.running:
            self.et.terminate()

    def test_it_raises_after_the_timeout_and_kills_the_process(self):
        process = self.et._process
        started = time.monotonic()
        outcome = _within(WATCHDOG, self.et, lambda: self.et.execute("-j", "-"))

        self.assertNotIn("hung", outcome, "the timeout never fired")
        self.assertIsInstance(outcome.get("error"), ExifToolTimeout)
        self.assertIn("3s", str(outcome["error"]))
        self.assertLess(time.monotonic() - started, 30)
        self.assertIsNotNone(process.poll(), "the stalled ExifTool was left running")
        self.assertFalse(self.et.running)

    def test_the_next_call_starts_a_fresh_process(self):
        _within(WATCHDOG, self.et, lambda: self.et.execute("-j", "-"))
        outcome = _within(WATCHDOG, self.et, lambda: self.et.execute("-ver"))
        self.assertNotIn("hung", outcome)
        self.assertRegex(outcome.get("value", ""), r"^\d+\.\d+")


class TestTheBatchReadersUseIt(unittest.TestCase):
    """The places that hand ExifTool a hundred files at a time."""

    def test_no_batch_reader_builds_a_bare_helper(self):
        for name in ("backfill_document_ids.py",):
            with open(os.path.join(WORKSPACE_DIR, "scripts", name), encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("ExifToolHelper(", source, name)
            self.assertIn("ExifToolSession(", source, name)

        # The reader itself; its callers hand it the library's people.
        with open(os.path.join(WORKSPACE_DIR, "tagpup", "files", "metadata.py"), encoding="utf-8") as f:
            source = f.read()
        for method in ("batch_read", "_read_one_by_one"):
            body = source.split("def %s(" % method, 1)[1].split("\n    def ", 1)[0]
            self.assertNotIn("ExifToolHelper(", body, method)
            self.assertIn("ExifToolSession(", body, method)


if __name__ == "__main__":
    unittest.main()

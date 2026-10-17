"""tagpup.services.indexing.index_folder: adding a folder's photos to a library.

The CLI is stood in for. What is checked is what it is asked to do, from where and for
which library, and what the Result and the progress reports say.

Contract row 16: the indexer's stdout is the interface, and `WhatTheProgressBarSays` is its table. What follows an
index -- clustering, its guard and its failure, from every entry point -- is tests/test_an_index_is_followed_by_clustering.py.

Lifted: test_tagpup_server_api's TestIndexerNoiseFiltering and TestIndexerProgressText (22 tests, one per line shape).
Broken in a scratch copy and the table fails: _INDEXER_NOISE emptied (every chatter row), _INDEXER_TQDM's percent group
dropped (every bar row), _INDEXER_MAX_MESSAGE ignored (the long row).
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import indexing  # noqa: E402


def process(returncode=0, lines=("done\n",), each_line=None):
    """A stand-in for the CLI's process, printing `lines` and exiting with `returncode`.
    `each_line()` is called as each line is read."""
    proc = MagicMock()
    proc.returncode = returncode
    remaining = iter(list(lines) + [""])

    def readline():
        line = next(remaining)
        if line and each_line:
            each_line()
        return line

    proc.stdout.readline.side_effect = readline
    return proc


class AddingAFolder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = os.path.join(tmp.name, "Regatta")
        os.makedirs(self.folder)
        self.library = Library(os.path.join(tmp.name, "library.db"))
        self.code = os.path.join(tmp.name, "code")
        self.reports = []

    def index(self, *processes, folder=None):
        with patch("subprocess.Popen", side_effect=list(processes)) as popen:
            result = indexing.index_folder(
                self.library, folder or self.folder, self.code,
                report=lambda message=None, percent=None: self.reports.append((message, percent)))
        return result, popen.call_args_list

    def test_the_cli_indexes_the_folder_into_this_library_from_the_code_running(self):
        result, calls = self.index(process())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], [sys.executable, "tagpup_cli.py", "index", self.folder])
        self.assertEqual(calls[0].kwargs["cwd"], self.code)
        self.assertEqual(calls[0].kwargs["env"]["TAGPUP_DB_PATH"], self.library.path)
        self.assertEqual((result.ok, result.changed, result.details["percent"]), (True, 1, 100))
        self.assertIn("Folder indexed", result.details["message"])

    def test_a_typed_folder_is_handed_over_as_stored(self):
        _, calls = self.index(process(), folder=self.folder.replace(os.sep, "/"))
        self.assertEqual(calls[0].args[0][3], paths.stored(self.folder))

    def test_a_failed_index_is_an_error(self):
        result, calls = self.index(process(2))
        self.assertEqual(len(calls), 1)
        self.assertEqual((result.ok, result.changed), (False, 0))
        self.assertEqual(result.message(), "Indexing failed with exit code 2.")
        self.assertEqual(result.details["percent"], 0)

    def test_progress_is_reported_as_a_person_would_read_it(self):
        self.index(process(lines=(
            "2026-09-19 21:31:50,515 [INFO] root - Instantiating model\n",
            "Generating embeddings:  50%|#####     | 25/50 [00:12<00:12]\n",
            "Found 50 image(s) total.\n",
        )))
        self.assertEqual(self.reports, [("Generating embeddings: 50% (25/50)", 45),
                                        ("Found 50 image(s) total.", None)])


class WhatTheProgressBarSays(unittest.TestCase):
    """One line of the CLI's stdout -> the text on the bar, or None for chatter nobody watching it wants (by shape, not wording)."""

    BAR = "Generating embeddings:  %d%%|####  | %d/49 [00:30<00:40]"
    CHATTER = (
        "Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN.",
        "WARNING:huggingface_hub.utils._http:Warning: You are sending unauthenticated requests to the HF Hub.",
        "2026-09-19 22:55:30,914 [INFO] httpx - HTTP Request: HEAD https://x",
        "2026-09-19 07:39:12,235 [INFO] tagpup_cli.index - Loaded 56959 entries",
        "DEBUG:some.module:a message", "INFO:some.module:a message", "WARNING:some.module:a message",
        "ERROR:some.module:a message", "CRITICAL:some.module:a message",
        "C:" + chr(92) + "x" + chr(92) + "y.py:42: UserWarning: something happened",
        "  warnings.warn(", '  File "C:/x/y.py", line 3, in f', "", "   " + chr(10), None)
    SAID = (
        (BAR % (42, 21), "Generating embeddings: 42% (21/49)"),
        # tqdm redraws with carriage returns: the newest frame is the current one, and the bar's width is read from its percent
        ((BAR % (10, 5)) + chr(13) + (BAR % (20, 10)), "Generating embeddings: 20% (10/49)"),
        ("Scanning directory: D:/Training/Pictures", "Scanning directory: D:/Training/Pictures"),
        ("Found 68083 image(s) total.", "Found 68083 image(s) total."),
        ("Indexing 49 image(s) (24 already tagged, 25 untagged).", "Indexing 49 image(s) (24 already tagged, 25 untagged)."),
        ("Indexing " + "x" * 300, "Indexing " + "x" * 80 + chr(0x2026)),
    )

    def test_chatter_is_dropped_and_progress_is_said_as_a_person_reads_it(self):
        for line in self.CHATTER:
            self.assertIsNone(indexing.summarize_indexer_line(line), repr(line))
        for line, said in self.SAID:
            self.assertEqual(said, indexing.summarize_indexer_line(line), repr(line[:40]))


if __name__ == "__main__":
    unittest.main()

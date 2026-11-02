"""tagpup.services.indexing.index_folder: adding a folder's photos to a library.

The CLI is stood in for. What is checked is what it is asked to do, from where and for
which library, and what the Result and the progress reports say.

Contract row 16: the indexer's stdout carries typed facts (tagpup.core.progress_line), and any other line is a
person's. `AddingAFolder.test_a_child_that_says_more_than_facts_completes_its_job` is the scenario: whatever else the child prints, the job hears its facts as
values and never raises. What follows an index -- the matching of the faces it made, from every entry point -- is
tests/test_new_faces_are_matched_after_an_index.py.

Lifted: the table of line shapes the reader once scraped (WhatTheProgressBarSays), whose regexes are gone.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
from tagpup.core import progress_line  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import bake_after_index, indexing  # noqa: E402


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
        self.listed = []
        started = iter(processes)

        def start(args, **_kwargs):
            # The folders' list as the child would read it: the file is gone once the run ends.
            with open(args[args.index(indexing.FOLDERS_FILE) + 1], encoding="utf-8", newline="") as handle:
                self.listed.append(handle.read())
            return next(started)
        with patch("subprocess.Popen", side_effect=start) as popen:
            result = indexing.index_folder(
                self.library, folder or self.folder, self.code,
                report=lambda message=None, percent=None: self.reports.append((message, percent)))
        return result, popen.call_args_list

    def test_the_cli_indexes_the_folder_into_this_library_from_the_code_running(self):
        result, calls = self.index(process())
        self.assertEqual(len(calls), 1)
        listing = calls[0].args[0][4]
        self.assertEqual(calls[0].args[0], [sys.executable, "tagpup_cli.py", "index", indexing.FOLDERS_FILE, listing])
        self.assertEqual([self.folder + "\n"], self.listed)
        self.assertFalse(os.path.exists(listing), "the list of folders was left behind")
        self.assertEqual(calls[0].kwargs["cwd"], self.code)
        self.assertEqual(calls[0].kwargs["env"]["TAGPUP_DB_PATH"], self.library.path)
        self.assertTrue(progress_line.asked(calls[0].kwargs["env"]), "the child was not asked for facts")
        self.assertEqual((result.ok, result.changed, result.details["percent"]), (True, 1, 100))
        self.assertIn("Folder indexed", result.details["message"])

    def test_a_typed_folder_is_handed_over_as_stored(self):
        self.index(process(), folder=self.folder.replace(os.sep, "/"))
        self.assertEqual([paths.stored(self.folder) + "\n"], self.listed)

    def test_a_failed_index_is_an_error(self):
        result, calls = self.index(process(2))
        self.assertEqual(len(calls), 1)
        self.assertEqual((result.ok, result.changed), (False, 0))
        self.assertEqual(result.message(), "Indexing failed with exit code 2.")
        self.assertEqual(result.details["percent"], 0)

    def test_a_child_that_says_more_than_facts_completes_its_job(self):
        """Library chatter, a bar drawn by hand, a traceback, a fact cut by a child killed mid-line, a field of the
        wrong type, a path in other letters, a very long line: the facts arrive as values, the rest as a person's line
        cut for the bar, and nothing raises into the job."""
        folder = "D:/Fotos/" + chr(0xC5) + "lesund " + chr(0x2014) + " M" + chr(0xE4) + "rta"
        bake = bake_after_index.folder_told(2, 4, bake_after_index.Baked(written=3, current=1), folder, files=(5, 10))
        baked = bake_after_index.Baked(written=7, current=2, conflicts=1)
        said = lambda kind, **fields: progress_line.say(progress_line.fact(kind, **fields)) + "\n"
        lines = (
            "2026-09-19 21:31:50,515 [INFO] root - Instantiating model\n",
            "Generating embeddings:  50%|#####     | 25/50 [00:12<00:12]\r\n",
            progress_line.say(indexing.phase_fact("embeddings", 25, 50)) + "\n",
            "Traceback (most recent call last):\n", '  File "C:/x/y.py", line 3, in f\n',
            progress_line.say(indexing.phase_fact("faces"))[:21] + "\n",
            progress_line.PREFIX + '{"kind":"failed","photos":"2"}\n',
            progress_line.PREFIX + '{"kind":"failed","photos":true}\n',
            progress_line.say(indexing.phase_fact("faces")) + "\n",
            said("new_faces", sentence="No new face is waiting for a name."),
            said("failed", photos=2),
            said("bake_read", sentence="Read: 3 photo(s) read from their file."),
            progress_line.say(bake) + "\n",
            progress_line.say(baked.fact()) + "\n",
            "x" * 5000 + "\n",
        )
        told = []
        with patch.object(indexing.job_runs, "tell", side_effect=lambda phase, counts: told.append((phase, counts))):
            result, _calls = self.index(process(lines=lines))
        self.assertTrue(result.ok, result.message())
        self.assertEqual("2 photo(s) failed and were not indexed (the indexer's log says why). Folder indexed. No new "
                         "face is waiting for a name. Read: 3 photo(s) read from their file. " + baked.said(),
                         result.details["message"])
        self.assertEqual((50, 2, 7, 2, 1), tuple(result.details[name] for name in (
            "photos_read", "failed_photos", "baked_written", "baked_current", "baked_conflicts")))
        self.assertEqual([("embeddings", {"done": 25, "of": 50, "percent": 50}), ("faces", {}),
                          ("bake", {"folders_done": 1, "folders": 4, "written": 3, "current": 1, "conflicts": 0,
                                    "percent": 37, "files_done": 5, "files": 10})], told)
        self.assertIn(("Generating embeddings: 50% (25/50)", 27), self.reports)
        self.assertIn((bake["sentence"], 68), self.reports)
        self.assertTrue(bake["sentence"].endswith(": " + folder), bake["sentence"])
        self.assertIn(("x" * 89 + chr(0x2026), None), self.reports)
        self.assertFalse([message for message, _p in self.reports if progress_line.PREFIX.strip() in message],
                         "a fact reached the bar as text")


if __name__ == "__main__":
    unittest.main()

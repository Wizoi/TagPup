"""tagpup.services.indexing.index_folder: adding a folder's photos to a library.

The CLI is stood in for. What is checked is what it is asked to do, from where and for
which library, and what the Result and the progress reports say.
"""
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
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

    def index(self, *processes, cluster=False, while_clustering=None, folder=None):
        with patch("subprocess.Popen", side_effect=list(processes)) as popen:
            result = indexing.index_folder(
                self.library, folder or self.folder, self.code, cluster=cluster,
                report=lambda message=None, percent=None: self.reports.append((message, percent)),
                while_clustering=while_clustering)
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

    def test_faces_are_clustered_only_when_asked(self):
        _, calls = self.index(process(), process(), cluster=True)
        self.assertEqual([call.args[0][2] for call in calls], ["index", "cluster-faces"])

    def test_a_failed_index_is_an_error_and_nothing_is_clustered(self):
        result, calls = self.index(process(2), cluster=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual((result.ok, result.changed), (False, 0))
        self.assertEqual(result.message(), "Indexing failed with exit code 2.")
        self.assertEqual(result.details["percent"], 0)

    def test_a_failed_clustering_is_an_error_though_the_folder_was_indexed(self):
        result, _ = self.index(process(), process(1), cluster=True)
        self.assertFalse(result.ok)
        self.assertIn("resolving face identities failed (exit code 1)", result.message())
        self.assertEqual(result.details["percent"], 100)

    def test_progress_is_reported_as_a_person_would_read_it(self):
        self.index(process(lines=(
            "2026-09-19 21:31:50,515 [INFO] root - Instantiating model\n",
            "Generating embeddings:  50%|#####     | 25/50 [00:12<00:12]\n",
            "Found 50 image(s) total.\n",
        )))
        self.assertEqual(self.reports, [("Generating embeddings: 50% (25/50)", 45),
                                        ("Found 50 image(s) total.", None)])

    def test_the_guard_is_held_while_cluster_faces_runs_and_only_then(self):
        held, seen = [False], []

        @contextmanager
        def guard():
            held[0] = True
            try:
                yield
            finally:
                held[0] = False

        def look():
            seen.append(held[0])

        result, _ = self.index(process(each_line=look), process(each_line=look), cluster=True,
                               while_clustering=guard)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(seen, [False, True])
        self.assertFalse(held[0])


if __name__ == "__main__":
    unittest.main()

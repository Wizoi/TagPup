"""Assignments wait while the clustering that adding folders runs rewrites names.

Assignment requests are refused while clustering_in_progress is set, because clustering
rewrites every automatic face name. Adding folders with face matching ran the same
cluster-faces without setting it, so an assignment made then could be overwritten a
moment later. TagTuner's indexer holds it while cluster-faces runs, and only then.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tuner_server import TunerHTTPRequestHandler, set_active_db_path  # noqa: E402


class Handler(TunerHTTPRequestHandler):
    def __init__(self, db_path):  # noqa: D107 -- no socket, on purpose
        self.db_path = db_path


class QueuedClusteringIsGuarded(unittest.TestCase):
    def test_the_guard_is_up_while_clustering_runs_and_down_after(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, "lib.db")
        # The flag is read for the library a request is in; this thread stands in for it.
        set_active_db_path(db)
        self.addCleanup(set_active_db_path, None)
        seen = []

        def process():
            proc = MagicMock()
            proc.returncode = 0
            lines = iter(["working\n", ""])

            def readline():
                line = next(lines)
                if line:
                    seen.append(TunerHTTPRequestHandler.clustering_in_progress)
                return line

            proc.stdout.readline.side_effect = readline
            return proc

        with patch("subprocess.Popen", side_effect=[process(), process()]):
            result = Handler(db).folder_indexer()(tmp.name, True, None)

        self.assertTrue(result.ok, result.message())
        self.assertEqual([False, True], seen, "assignments were accepted while clustering ran")
        self.assertFalse(TunerHTTPRequestHandler.clustering_in_progress)


if __name__ == "__main__":
    unittest.main()

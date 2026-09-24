"""Assignments wait while the index queue's clustering rewrites names.

Recluster sets clustering_in_progress, and assignment requests are refused while it is
set, because clustering rewrites every automatic face name. Adding folders with face
matching ran the same cluster-faces without setting it, so an assignment made then could
be overwritten a moment later.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tuner_server import TunerHTTPRequestHandler, set_active_db_path  # noqa: E402


class QueuedClusteringIsGuarded(unittest.TestCase):
    def test_the_guard_is_up_while_clustering_runs_and_down_after(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, "lib.db")
        seen = []

        def process(returncode, record=False):
            proc = MagicMock()
            proc.returncode = returncode

            def readline():
                if record and not seen:
                    seen.append(TunerHTTPRequestHandler.clustering_in_progress)
                    return "clustering\n"
                return ""

            proc.stdout.readline.side_effect = readline
            return proc

        with patch("subprocess.Popen", side_effect=[process(0), process(0, record=True)]):
            TunerHTTPRequestHandler.run_folder_index_thread(tmp.name, db, True)

        set_active_db_path(db)
        try:
            self.assertEqual([True], seen, "assignments were accepted while clustering ran")
            self.assertFalse(TunerHTTPRequestHandler.clustering_in_progress)
        finally:
            set_active_db_path(None)


if __name__ == "__main__":
    unittest.main()

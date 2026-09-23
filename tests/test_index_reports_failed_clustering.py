"""Adding a folder with face matching says so when face matching failed.

Both apps index a folder through the CLI and then, when asked, run cluster-faces. Each
had its own copy of those steps, and neither read cluster-faces' exit code: a crash
while resolving identities was reported as "identities resolved".
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import paths  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402
from tuner_server import set_active_db_path as set_tuner_db_path  # noqa: E402


def finished(returncode):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout.readline.side_effect = ["done\n", ""]
    return proc


class IndexReportsFailedClustering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        self.db = os.path.join(self.tmp.name, "lib.db")

    def tearDown(self):
        set_active_db_path(None)
        set_tuner_db_path(None)
        self.tmp.cleanup()

    def run_thread(self, handler, set_db):
        # Index succeeds, clustering exits with an error.
        with patch("subprocess.Popen", side_effect=[finished(0), finished(1)]) as popen:
            handler.run_folder_index_thread(self.folder, self.db, True)
        set_db(self.db)
        status = handler.index_status.get(paths.key(self.folder))
        handler.index_status.pop(paths.key(self.folder), None)
        self.assertEqual(["index", "cluster-faces"], [c.args[0][2] for c in popen.call_args_list])
        return status

    def test_tagpup_says_face_matching_failed(self):
        status = self.run_thread(TagPupHTTPRequestHandler, set_active_db_path)
        self.assertEqual("failed", status["status"], status)
        self.assertIn("face identities failed", status["message"])

    def test_tagtuner_says_face_matching_failed(self):
        status = self.run_thread(TunerHTTPRequestHandler, set_tuner_db_path)
        self.assertEqual("failed", status["status"], status)
        self.assertIn("face identities failed", status["message"])


if __name__ == "__main__":
    unittest.main()

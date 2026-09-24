"""Adding a folder with face matching says so when face matching failed.

Adding a folder indexes it through the CLI and then, when asked, runs cluster-faces.
Both apps had their own copy of those steps, and neither read cluster-faces' exit code:
a crash while resolving identities was reported as "identities resolved". There is one
copy now, tagpup.services.indexing.index_folder, and the folder's status in the queue
says what it said.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs.indexing import IndexQueue  # noqa: E402
from tagpup.services import indexing  # noqa: E402


def finished(returncode):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout.readline.side_effect = ["done\n", ""]
    return proc


class IndexReportsFailedClustering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.library = Library(os.path.join(self.tmp.name, "lib.db"))

    def index(self, folder, cluster, report):
        return indexing.index_folder(self.library, folder, WORKSPACE_DIR, cluster=cluster,
                                     report=report)

    def test_the_folder_says_face_matching_failed(self):
        queue = IndexQueue()
        # Index succeeds, clustering exits with an error.
        with patch("subprocess.Popen", side_effect=[finished(0), finished(1)]) as popen, \
                patch.object(IndexQueue, "_ensure_runner"):
            queue.start([self.folder], self.index, cluster=True)
            queue.run_pending()
        self.assertEqual(["index", "cluster-faces"], [c.args[0][2] for c in popen.call_args_list])
        status = queue.status(self.folder)
        self.assertEqual("failed", status["status"], status)
        self.assertIn("face identities failed", status["message"])


if __name__ == "__main__":
    unittest.main()

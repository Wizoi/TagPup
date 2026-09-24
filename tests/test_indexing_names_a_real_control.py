"""Adding a folder names the control that clusters faces, and that control exists.

The messages told the owner to "run Recluster". No page has had a Recluster button since
TagTuner lost the route no page called (docs/findings.md, #8 and #22). Faces are
clustered by the runner's Run Identity Resolution Clustering, or by
`tagpup_cli.py cluster-faces`.
"""
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import indexing  # noqa: E402


def finished(returncode):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout.readline.side_effect = ["done\n", ""]
    return proc


class AddingAFolderNamesARealControl(unittest.TestCase):
    def messages(self):
        """What adding a folder says when faces are left to cluster, and when
        clustering them failed."""
        with tempfile.TemporaryDirectory() as folder:
            library = Library(os.path.join(folder, "library.db"))
            with patch("subprocess.Popen", side_effect=[finished(0)]):
                indexed = indexing.index_folder(library, folder, WORKSPACE_DIR)
            with patch("subprocess.Popen", side_effect=[finished(0), finished(1)]):
                failed = indexing.index_folder(library, folder, WORKSPACE_DIR, cluster=True)
        return [indexed.details["message"], failed.message()]

    def test_no_message_names_a_button_that_is_gone(self):
        for message in self.messages():
            self.assertNotIn("Recluster", message)

    def test_each_names_the_runners_clustering_button(self):
        with open(os.path.join(WORKSPACE_DIR, "runner.py"), encoding="utf-8") as f:
            buttons = set(re.findall(r'text="([^"]+)"', f.read()))
        self.assertIn(indexing.CLUSTERING_BUTTON, buttons, "the runner has no such button")
        for message in self.messages():
            self.assertIn(indexing.CLUSTERING_BUTTON, message)


if __name__ == "__main__":
    unittest.main()

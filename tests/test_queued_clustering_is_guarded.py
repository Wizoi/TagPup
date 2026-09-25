"""Assignments wait while the clustering that adding folders runs rewrites names.

Assignment requests are refused while a library is being clustered
(tagpup.web.tuner_routes.clustering), because clustering rewrites every automatic face
name. Adding folders with face matching ran the same cluster-faces without setting it,
so an assignment made then could be overwritten a moment later. TagTuner's indexer
holds it while cluster-faces runs, and only then.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core.library import Library  # noqa: E402
from tagpup.web import tuner_routes  # noqa: E402


class QueuedClusteringIsGuarded(unittest.TestCase):
    def test_the_guard_is_up_while_clustering_runs_and_down_after(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        library = Library(os.path.join(tmp.name, "lib.db"))
        self.addCleanup(tuner_routes.clustering.forget, library)
        seen = []

        def process():
            proc = MagicMock()
            proc.returncode = 0
            lines = iter(["working\n", ""])

            def readline():
                line = next(lines)
                if line:
                    seen.append(tuner_routes.clustering.of(library).is_set())
                return line

            proc.stdout.readline.side_effect = readline
            return proc

        with patch("subprocess.Popen", side_effect=[process(), process()]):
            result = tuner_routes.folder_indexer(library)(tmp.name, True, None)

        self.assertTrue(result.ok, result.message())
        self.assertEqual([False, True], seen, "assignments were accepted while clustering ran")
        self.assertFalse(tuner_routes.clustering.of(library).is_set())


if __name__ == "__main__":
    unittest.main()

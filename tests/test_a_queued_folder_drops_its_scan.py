"""A folder the index queue finishes has its cached scan dropped, whoever queued it (docs/findings.md, #341).

Only the server's own route (tagpup_routes._folder_indexer) dropped the folder's scan when indexing ended; the folders
sync, a recurring sync, "include" and the damaged-photo check queue went through tagpup.runtime.index_folder, which did
not, so the page went on showing the folder as it was scanned before its photos were read.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import indexing  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


class AFolderTheQueueFinished(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.library = Library(home.library("library.db"))
        self.folder = os.path.join(home.root, "Pictures", "Regatta")
        tagpup_routes.folders.of(self.library).put(self.folder, {"a": {"path": "a"}})

    def run_queue(self, **mocked):
        with mock.patch.object(indexing, "index_folder", **mocked):
            for make in (runtime.index_folder(self.library, subfolders=False), tagpup_routes._folder_indexer(self.library)):
                tagpup_routes.folders.of(self.library).put(self.folder, {"a": {"path": "a"}})
                try:
                    make(self.folder, False, None)
                except RuntimeError:
                    pass
                yield tagpup_routes.folders.of(self.library).get(self.folder)

    def test_has_no_cached_scan_left(self):
        self.assertEqual([None, None], list(self.run_queue(return_value=None)))

    def test_has_none_left_when_indexing_failed_either(self):
        # Rows were written even when clustering failed afterwards.
        self.assertEqual([None, None], list(self.run_queue(side_effect=RuntimeError("clustering failed"))))


if __name__ == "__main__":
    unittest.main()

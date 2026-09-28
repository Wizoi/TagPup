"""One answer to "is this folder the library's" (tagpup.store.folders), asked by all.

"Held" had two answers: the rows (sync's walk and its sorting of new files, the watcher,
Remove Folder's list) and the rows or an add (Suggest, the writes). A folder added whose
index never ran was the library's to Suggest in and nobody's to sync, watch or remove; an
ignored folder under a folder added with its subfolders was the library's to Suggest in.
Now: the folders with rows, and those added (a parent added with its subfolders covering
what is under it), but never an ignored one.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import NotHeld, Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

THEN = 1_700_000_000


def make_photos(folder, *names):
    os.makedirs(folder, exist_ok=True)
    made = []
    for name in names:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        os.utime(path, (THEN, THEN))
        made.append(path)
    return made


class AnAddedFolder(unittest.TestCase):
    """Pictures is the library's root. Regatta is indexed; Trip was added, its index never
    ran; Trip\\Scans is ignored."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.root = os.path.join(self.home.root, "Pictures")
        self.regatta = os.path.join(self.root, "Regatta")
        self.trip = os.path.join(self.root, "Trip")
        self.scans = os.path.join(self.trip, "Scans")
        held = make_photos(self.regatta, "regatta_01.jpg")[0]
        make_photos(self.trip, "IMG_0001.jpg", "IMG_0002.jpg")
        self.scanned = make_photos(self.scans, "scan_01.jpg")[0]
        db.write_with_connection(self.library.path, lambda conn: photo_rows.add_read(conn, held, {}))
        # Added before the roots are set: a folder under a new root holding no photo of the
        # library is ignored as the roots are set (settings.excluded_under), an added one not.
        self.assertEqual(1, library_actions.record_added(self.library, [self.trip]))
        settings_service.of(self.library)
        self.assertTrue(settings_service.change(self.library, {settings_service.ROOTS: self.root,
                                                               settings_service.IGNORED: self.scans}).ok)
        self.queued = []

        def index_folder(library, subfolders=True):
            def index(folder, cluster, report):
                self.queued.extend([folder] if isinstance(folder, str) else list(folder))
                return Result(attempted=1, changed=1)
            return index

        patcher = mock.patch("tagpup.runtime.index_folder", side_effect=index_folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(indexing_jobs.forget, self.library)

    def sync(self):
        result = runtimes.sync(self.library, apply=True)
        indexing_jobs.queue_for(self.library).wait()
        return result

    def test_sync_walks_it_and_queues_its_files(self):
        found = self.sync().details["counts"]
        self.assertEqual([self.trip], [f for f in self.queued if paths.same(f, self.trip)])
        self.assertEqual(0, found["review_folders"], "the added folder was offered for review")

    def test_a_new_subfolder_of_it_is_the_librarys_not_to_review(self):
        heats = make_photos(os.path.join(self.trip, "Heats"), "heat_01.jpg")[0]
        self.sync()
        self.assertTrue(any(paths.same(f, os.path.dirname(heats)) for f in self.queued), self.queued)

    def test_a_new_folder_under_the_root_alone_is_still_to_review(self):
        make_photos(os.path.join(self.root, "Quayside"), "quay_01.jpg")
        found = self.sync().details["counts"]
        self.assertEqual(1, found["review_folders"])
        self.assertFalse(any("Quayside" in f for f in self.queued))

    def test_the_watcher_watches_it(self):
        watched = runtimes.watch_folders(self.library)
        self.assertTrue(any(paths.same(w, self.root) or paths.same(w, self.trip) for w in watched), watched)
        settings_service.change(self.library, {settings_service.ROOTS: ""})
        self.assertTrue(any(paths.same(w, self.trip) for w in runtimes.watch_folders(self.library)))

    def test_remove_folder_lists_it(self):
        listed = {paths.key(entry["path"]): entry for entry in photo_actions.indexed_folders(self.library)}
        self.assertIn(paths.key(self.trip), listed)
        self.assertEqual(0, listed[paths.key(self.trip)]["own_photos"])

    def test_an_ignored_folder_under_it_is_not_the_librarys(self):
        self.assertEqual([self.scans], library_actions.not_held(self.library, [self.scanned], leave_out_ignored=False))
        with self.assertRaises(NotHeld):
            db.write_with_connection(self.library.path, lambda conn: store_photos.ensure_row(conn, self.scanned))
        self.assertEqual([], library_actions.not_held(self.library, [os.path.join(self.trip, "IMG_0001.jpg")]))


if __name__ == "__main__":
    unittest.main()

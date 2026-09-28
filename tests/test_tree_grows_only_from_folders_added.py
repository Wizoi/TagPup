"""The tag tree grows from the tags on files only in folders the library holds.

The tree gains nodes from files in one place: the indexer, which files every tag of
each photo it reads (tagpup_cli.py index, TagTaxonomy.add_tags and add_people). It
runs on a folder only when one is added -- TagPup's Add, TagTuner's Add Folder, a
folder to review included, the CLI's `index` -- or when sync finds new files in a
folder the library holds and queues them (tagpup.runtime.sync). Refreshing a row from
its file writes the row, never the tree.

On 2026-09-28 Suggest made 25 rows in kr-track for photos of a folder photo_index held;
the folder was kr-track's from then on, so sync queued its other photos, the indexer
read their Family/Work tags into kr-track's tree, and a name typed in kr-track resolved
to photo_index's path. Suggest there is refused now (test_rows_only_in_folders_added),
so the folder never becomes the library's without being added, and sync and the
watcher leave it alone: the indexer is never queued for it.
"""
import contextlib
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
from tagpup.core.result import Conflict, Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402
from tagpup.services import suggestions as saved_suggestions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402

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


class TheTreeStaysTheLibrarys(unittest.TestCase):
    """harbour holds Regatta; the share's Lighthouse is quayside's, its photos tagged
    Family/Work. Somebody opens Lighthouse in harbour and starts Suggest."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.regatta = os.path.join(self.home.root, "Pictures", "Regatta")
        self.lighthouse = os.path.join(self.home.root, "Share", "Lighthouse")
        held = make_photos(self.regatta, "regatta_01.jpg")
        self.shared = make_photos(self.lighthouse, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg")
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, held[0], {"XMP:Subject": ["Trips/Regatta"]})
            conn.commit()
        finally:
            conn.close()
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

    def suggest_there(self):
        """What a Suggest run keeps of the first two photos -- refused now, the folder not
        added; kept, and a row made for each, before."""
        for photo in self.shared[:2]:
            with contextlib.suppress(Conflict):
                saved_suggestions.keep(self.library.path, photo, {"tags": ["Family/Work"]})

    def sync(self):
        result = runtimes.sync(self.library, apply=True)
        indexing_jobs.queue_for(self.library).wait()
        return result

    def tree(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return taxonomy.tags(conn)
        finally:
            conn.close()

    def assert_left_alone(self):
        under = [folder for folder in self.queued if paths.same(folder, self.lighthouse)
                 or paths.is_under(folder, self.lighthouse)]
        self.assertEqual([], under, "the indexer was queued for a folder never added")
        watched = runtimes.watch_folders(self.library)
        self.assertFalse(any(paths.same(w, self.lighthouse) or paths.is_under(self.lighthouse, w) for w in watched),
                         "the watcher watches a folder never added")
        self.assertFalse([tag for tag in self.tree() if tag.startswith("Family")])

    def test_a_library_without_roots(self):
        self.suggest_there()
        self.sync()
        self.assert_left_alone()

    def test_a_library_whose_roots_hold_neither(self):
        settings_service.of(self.library)
        self.assertTrue(settings_service.change(self.library, {settings_service.ROOTS: self.regatta}).ok)
        self.suggest_there()
        self.sync()
        self.assert_left_alone()

    def test_once_added_its_new_files_are_the_librarys_to_index(self):
        # The other side: a folder added, where Suggest kept something, is held, and sync
        # keeps it in step.
        library_actions.record_added(self.library, [self.lighthouse])
        saved_suggestions.keep(self.library.path, self.shared[0], {"tags": ["Family/Work"]})
        self.sync()
        self.assertTrue(any(paths.same(folder, self.lighthouse) for folder in self.queued))

    def test_adding_it_gives_sync_nothing_new_to_read(self):
        # The add made a row for every photo, unstamped, which sync then read again with
        # ExifTool while the indexer read them too. It makes none now.
        library_actions.record_added(self.library, [self.lighthouse])
        found = self.sync().details["counts"]
        self.assertEqual((0, 0, 0), (found["changed"], found["never_stamped"], found["to_write"]))
        self.assert_left_alone()


if __name__ == "__main__":
    unittest.main()

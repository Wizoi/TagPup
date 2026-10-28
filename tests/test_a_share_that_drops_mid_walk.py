"""A share that stops answering part-way through a walk of a library's folders (#2104): the listing of each folder on a
share goes through tagpup.files.shares.listing and is waited for LISTING_WAIT, so a sync -- the walk the watcher's whole
sync makes (recurring.sync_whole), and the daily job's -- ends within that wait, not after Windows's. What it could not
list is not looked at: no photo under it is missing, and the library is not recorded in step (#1356)."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images, shares  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

THEN = 1_600_000_000
#: How long the dropped share holds a listing before Windows gives up on it, in the test: far beyond LISTING_WAIT.
WINDOWS_WAIT = 3.0


class AShareThatDropsMidWalk(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="share_drop_")
        shares.forget()
        self.addCleanup(shares.forget)
        self.share = os.path.join(self.home.root, "NAS", "Pictures")
        self.regatta = [self.photo("2019 Regatta", "IMG_%04d.jpg" % n) for n in range(2)]
        self.harvest = [self.photo("2020 Harvest", "IMG_%04d.jpg" % n) for n in range(2)]
        for patcher in (mock.patch.object(shares, "LISTING_WAIT", 0.2),
                        mock.patch.object(sync, "FOLDER_WAIT", 0.2),
                        mock.patch.object(shares, "on_a_network_drive", self.on_the_share)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def on_the_share(self, path):
        return os.path.normcase(str(path)).startswith(os.path.normcase(self.share))

    def photo(self, folder, name):
        folder = os.path.join(self.share, folder)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg " + name.encode())
        os.utime(path, (THEN, THEN))
        return path

    def library(self):
        """A library holding every photo on the share, each a row as the indexer records a read of it."""
        db_path = self.home.library("regatta.db")
        library_actions.create(db_path)
        conn = db.connect(db_path)
        try:
            for path in self.regatta + self.harvest:
                photo_rows.add_read(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        return Library(db_path)

    def drops_at(self, folder):
        """The share stops answering as `folder` is listed: that listing hangs for WINDOWS_WAIT, then fails as Windows
        says the network name is no longer available."""
        released, dropped = threading.Event(), os.path.normcase(os.path.join(self.share, folder))
        self.addCleanup(released.set)
        real = os.scandir

        def scandir(path="."):
            if os.path.normcase(str(path)) == dropped:
                released.wait(WINDOWS_WAIT)
                raise OSError(64, "The specified network name is no longer available")
            return real(path)
        patcher = mock.patch.object(os, "scandir", scandir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_sync_whose_share_drops_mid_walk_ends_within_the_wait_and_calls_nothing_missing(self):
        library = self.library()
        self.drops_at("2020 Harvest")
        began = time.monotonic()
        result = sync.sync(library, apply=True, roots=[self.share])
        took = time.monotonic() - began
        self.assertLess(took, WINDOWS_WAIT / 2, "the walk waited for Windows, not for the share's wait")
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual({"missing": 0, "moved": 0, "not_looked_at": 2, "folders_not_looked_at": 1},
                         {what: counts.get(what) for what in ("missing", "moved", "not_looked_at",
                                                              "folders_not_looked_at")})
        self.assertFalse(result.details["in_step"])

    def test_the_walk_names_the_folder_it_could_not_list_and_finds_the_photos_it_listed(self):
        self.drops_at("2020 Harvest")
        unlisted = []
        began = time.monotonic()
        found = images.stamps_under(self.share, unlisted)
        self.assertLess(time.monotonic() - began, WINDOWS_WAIT / 2)
        self.assertEqual(sorted(os.path.normcase(p) for p in self.regatta),
                         sorted(os.path.normcase(path) for path, _mtime, _size in found.values()))
        self.assertEqual([os.path.normcase(os.path.join(self.share, "2020 Harvest"))],
                         [os.path.normcase(folder) for folder in unlisted])
        self.assertIsNotNone(shares.away_since(shares.share_of(self.share)), "the share was not taken as away")


if __name__ == "__main__":
    unittest.main()

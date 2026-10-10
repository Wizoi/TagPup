"""Each library keeps its newest five backups, none older than 30 days; older ones are deleted as a new one is made.

Every bulk write and every migration that rewrites data copies the library first, and
nothing ever deleted a copy: backups/ once held 28 GB in 41 of them (docs/findings.md,
#7). The owner chose five per library (2026-09-24), so a busy library never pushes out
another's only backup. Count alone left 18.7 GB of one-off copies months old, so a copy
older than 30 days goes too (2026-10-10) -- never the one just made, and never the
snapshots (tagpup.store.snapshots), which are other files in other folders.
"""
import os
import shutil
import tempfile
import time
import unittest

from tagpup.store import db, schema


def days_ago(days):
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() - days * 86400))


class BackupsAreKeptToFive(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_backups_")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.library = os.path.join(self.home, "harbour.db")
        schema.ensure(self.library)
        self.folder = os.path.join(self.home, "backups")
        os.makedirs(self.folder)

    def older(self, name, stamp, companions=False):
        path = os.path.join(self.folder, "%s.before-something-%s.db" % (name, stamp))
        open(path, "wb").close()
        if companions:
            for suffix in ("-wal", "-shm"):
                open(path + suffix, "wb").close()
        return path

    def test_the_sixth_takes_the_oldest_with_it(self):
        oldest = self.older("harbour", days_ago(9), companions=True)
        kept = [self.older("harbour", days_ago(day)) for day in range(5, 1, -1)]
        newest = db.backup(self.library, "test")
        left = sorted(os.listdir(self.folder))
        self.assertEqual(sorted(os.path.basename(p) for p in kept + [newest]), left)
        self.assertFalse(os.path.exists(oldest + "-wal") or os.path.exists(oldest + "-shm"))

    def test_another_librarys_backups_are_left_alone(self):
        theirs = [self.older("lighthouse", days_ago(day)) for day in range(1, 8)]
        db.backup(self.library, "test")
        for path in theirs:
            self.assertTrue(os.path.exists(path), path)

    def test_fewer_than_five_are_all_kept(self):
        mine = [self.older("harbour", days_ago(day)) for day in range(1, 3)]
        db.backup(self.library, "test")
        self.assertEqual(3, len(os.listdir(self.folder)))
        for path in mine:
            self.assertTrue(os.path.exists(path))

    def test_a_copy_older_than_thirty_days_goes_though_fewer_than_five_are_kept(self):
        old = self.older("harbour", days_ago(31), companions=True)
        recent = self.older("harbour", days_ago(29))
        newest = db.backup(self.library, "test")
        self.assertEqual(sorted([os.path.basename(recent), os.path.basename(newest)]), sorted(os.listdir(self.folder)))
        self.assertFalse(os.path.exists(old) or os.path.exists(old + "-wal") or os.path.exists(old + "-shm"))

    def test_another_librarys_old_copies_are_left_alone(self):
        theirs = self.older("lighthouse", days_ago(400))
        db.backup(self.library, "test")
        self.assertTrue(os.path.exists(theirs))

    def test_the_snapshots_are_not_these_files(self):
        snapshot = os.path.join(self.folder, "harbour", "monthly", "harbour-20200101_000000.db")
        os.makedirs(os.path.dirname(snapshot))
        open(snapshot, "wb").close()
        db.backup(self.library, "test")
        self.assertTrue(os.path.exists(snapshot))

    def test_the_newest_is_kept_whatever_its_age(self):
        """The newest copy stays however old it is (a long gap between two bulk writes); an older one beside it goes."""
        only = self.older("harbour", days_ago(100))
        self.assertEqual([], db.prune_backups(self.folder, "harbour"))
        self.assertTrue(os.path.exists(only))
        older = self.older("harbour", days_ago(120))
        self.assertEqual([older], db.prune_backups(self.folder, "harbour"))
        self.assertTrue(os.path.exists(only))


if __name__ == "__main__":
    unittest.main()

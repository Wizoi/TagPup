"""Each library keeps its newest five backups; older ones are deleted as a new one is made.

Every bulk write and every migration that rewrites data copies the library first, and
nothing ever deleted a copy: backups/ once held 28 GB in 41 of them (docs/findings.md,
#7). The owner chose five per library (2026-09-24), so a busy library never pushes out
another's only backup.
"""
import os
import shutil
import tempfile
import unittest

from tagpup.store import db, schema


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
        oldest = self.older("harbour", "20260101_000000", companions=True)
        kept = [self.older("harbour", "2026010%d_000000" % day) for day in range(2, 6)]
        newest = db.backup(self.library, "test")
        left = sorted(os.listdir(self.folder))
        self.assertEqual(sorted(os.path.basename(p) for p in kept + [newest]), left)
        self.assertFalse(os.path.exists(oldest + "-wal") or os.path.exists(oldest + "-shm"))

    def test_another_librarys_backups_are_left_alone(self):
        theirs = [self.older("lighthouse", "2026010%d_000000" % day) for day in range(1, 8)]
        db.backup(self.library, "test")
        for path in theirs:
            self.assertTrue(os.path.exists(path), path)

    def test_fewer_than_five_are_all_kept(self):
        mine = [self.older("harbour", "2026010%d_000000" % day) for day in range(1, 3)]
        db.backup(self.library, "test")
        self.assertEqual(3, len(os.listdir(self.folder)))
        for path in mine:
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()

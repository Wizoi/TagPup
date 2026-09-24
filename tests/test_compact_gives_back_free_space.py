"""`tagpup_cli.py compact`: the space a library holds free, given back.

Rows deleted and columns dropped leave their pages free inside the file. Phase 4's
migrations left about 1 GB of photo_index's 4 GB so. A dry run by default; --apply backs
the library up first.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

from click.testing import CliRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import tagpup_cli  # noqa: E402

from tagpup.store import db, schema  # noqa: E402


class Compacting(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="compact_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db_path = os.path.join(self.dir, "library.db")
        schema.ensure(self.db_path)
        conn = db.connect(self.db_path)
        try:
            conn.executemany("INSERT INTO photos (path, raw_metadata) VALUES (?, ?)",
                             [(os.path.join(self.dir, "%d.jpg" % n), "x" * 4000) for n in range(500)])
            conn.commit()
            conn.execute("DELETE FROM photos")
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def digest(self):
        with open(self.db_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def cli(self, *args):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "compact", *args])
        self.assertEqual(0, result.exit_code, result.output)
        return result.output

    def test_deleted_rows_leave_their_space_free(self):
        size, free = db.space(self.db_path)
        self.assertGreater(free, size // 2)

    def test_a_dry_run_changes_nothing(self):
        before = self.digest()
        self.assertIn("Nothing changed", self.cli())
        self.assertEqual(before, self.digest())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "backups")))

    def test_apply_backs_up_then_gives_the_space_back(self):
        size, free = db.space(self.db_path)
        self.cli("--apply")
        self.assertEqual(1, len(os.listdir(os.path.join(self.dir, "backups"))))
        after, free_after = db.space(self.db_path)
        self.assertLess(after, size - free // 2)
        self.assertEqual(0, free_after)


if __name__ == "__main__":
    unittest.main()

"""A library's record of its syncs keeps its newest records, not every one (tagpup.store.
sync_runs). The folder watcher records a sync each time a folder settles -- 14 in a day on
one library already -- and the table grew by each for good. "Last in step" and the last
whole sync outlive the pruning: the pages show them.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, sync_runs  # noqa: E402


class TheRecordIsBounded(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="sync_runs_")
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)

    def count(self):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0], sync_runs.last(conn)
        finally:
            conn.close()

    def test_the_newest_are_kept_and_the_last_in_step_with_them(self):
        with mock.patch.object(sync_runs, "KEEP", 5, create=True):
            whole = sync_runs.record(self.path, "2026-09-20 08:00:00", whole=True, in_step=True, found={}, changed={})
            for minute in range(12):
                sync_runs.record(self.path, "2026-09-26 09:%02d:00" % minute, whole=False, in_step=False,
                                 found={"new": 1}, changed={"rows": 0})
        count, last = self.count()
        self.assertEqual(6, count, "the newest five, and the whole sync that left the library in step")
        self.assertIsNotNone(last["last_in_step"])
        self.assertEqual("2026-09-26 09:11:00", last["last_run"]["started"])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(whole, sync_runs.newest(conn, whole=True)["id"])
        finally:
            conn.close()

    def test_the_default_keeps_hundreds(self):
        self.assertGreaterEqual(sync_runs.KEEP, 100)


if __name__ == "__main__":
    unittest.main()

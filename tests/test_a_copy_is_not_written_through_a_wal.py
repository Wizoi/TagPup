"""A copy of a library -- a backup before a bulk write, a snapshot -- is a file of its own,
written without a WAL (review of phase 8a, 2).

The copy's connection was made by db.connect, which puts every connection it opens in
WAL: the backup then wrote the whole library into the -wal first and checkpointed it into
the file, 2.5 GB twice for photo_index, 12.2 s where 6.1 s would do. The destination is
put in rollback-journal mode before the copy, and left in it after, by one helper both
use (db.copy_database).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, snapshots  # noqa: E402


def journal_mode(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


class ACopy(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="copy_")
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)

    def test_a_backup_is_a_file_of_its_own(self):
        copy = db.backup(self.path, "test")
        self.assertEqual("delete", journal_mode(copy))
        self.assertEqual([os.path.basename(copy)], [name for name in os.listdir(os.path.dirname(copy))
                                                     if name.startswith(os.path.basename(copy))])

    def test_the_destination_is_out_of_wal_before_the_copy_is_written(self):
        modes = []
        real = db.sqlite3.Connection

        class Watched:
            def __init__(self, conn):
                self.conn = conn

            def backup(self, destination, *args, **kwargs):
                modes.append(destination.execute("PRAGMA journal_mode").fetchone()[0])
                return self.conn.backup(destination, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        connect = db.connect

        def watched(target, *args, **kwargs):
            conn = connect(target, *args, **kwargs)
            return Watched(conn) if isinstance(conn, real) and kwargs.get("uri") else conn

        with mock.patch.object(db, "connect", side_effect=watched):
            db.backup(self.path, "test")
            snapshots.copy_library(self.path, os.path.join(self.home.root, "copy.db"))
        self.assertEqual(["delete", "delete"], modes)

    def test_both_copies_are_made_by_one_helper(self):
        with mock.patch.object(db, "copy_database", wraps=db.copy_database) as copied:
            db.backup(self.path, "test")
            snapshots.copy_library(self.path, os.path.join(self.home.root, "copy.db"))
        self.assertEqual(2, copied.call_count)
        self.assertEqual("delete", journal_mode(os.path.join(self.home.root, "copy.db")))


if __name__ == "__main__":
    unittest.main()

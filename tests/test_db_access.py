"""Every database connection comes from scripts/db.py.

This program is a reader and a writer at once, from many threads: both servers handle
each request on its own thread, the tag suggester runs a thread pool, and indexing runs
in a background thread beside all of it. Across the codebase that was 71 places opening
a connection and 72 statements writing through one, each deciding for itself what
journal mode and timeout to use -- which is to say, none of them deciding.

The symptom was `database is locked` appearing from a different place each time it was
fixed: first recording faces during a folder index, then tag embeddings from Suggest
Tags, then the embedding cache. Fixing them one at a time is how a whole afternoon
goes.

These tests are the structural fix: a connection opened outside db.py gets none of the
settings, so the rule is that there are no such connections.
"""
import ast
import os
import re
import sys
import threading
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db

SCRIPTS = os.path.join(WORKSPACE_DIR, "scripts")


def source_files():
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith(".py") and name != "db.py":
            yield os.path.join(SCRIPTS, name)
    yield os.path.join(WORKSPACE_DIR, "tagpup_cli.py")


class TestNobodyConnectsDirectly(unittest.TestCase):
    def test_no_module_calls_sqlite3_connect(self):
        offenders = []
        for path in source_files():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for line_no, line in enumerate(source.splitlines(), 1):
                if re.search(r"\bsqlite3\.connect\s*\(", line):
                    offenders.append("%s:%d" % (os.path.basename(path), line_no))

        self.assertEqual(
            offenders, [],
            "these open a connection without the WAL mode, busy timeout and write "
            "lock that db.connect() applies: " + ", ".join(offenders)
        )

    def test_db_itself_is_allowed_to(self):
        """The check above is worthless if it matches nothing anywhere."""
        with open(os.path.join(SCRIPTS, "db.py"), encoding="utf-8") as f:
            self.assertIn("sqlite3.connect", f.read())


class TestConnectionsAreConfigured(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_a_connection_is_in_wal_mode(self):
        conn = tagpup_db.connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_a_connection_waits_rather_than_failing_at_once(self):
        conn = tagpup_db.connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("PRAGMA busy_timeout").fetchone()[0],
            tagpup_db.BUSY_TIMEOUT_MS,
        )

    def test_a_read_only_connection_is_not_asked_to_change_the_file(self):
        # Setting journal_mode needs write access; asking for it on a read-only
        # connection fails, and failing to open a reader would be worse than the
        # contention this is all about.
        tagpup_db.connect(self.path).close()   # create it
        conn = tagpup_db.connect("file:%s?mode=ro" % self.path, uri=True)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)


class TestOneWriterAtATime(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_writes_to_one_database_do_not_overlap(self):
        """The lock is the part that actually fixes it: every writer here is a thread
        of this same program, and a busy timeout does not help a lock upgrade."""
        overlapping = []
        active = []
        guard = threading.Lock()

        def writer():
            def work():
                with guard:
                    active.append(1)
                    if len(active) > 1:
                        overlapping.append(len(active))
                threading.Event().wait(0.01)
                with guard:
                    active.pop()
            tagpup_db.write(self.path, work)

        threads = [threading.Thread(target=writer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(overlapping, [], "two writers ran at once")

    def test_different_databases_do_not_wait_on_each_other(self):
        import tempfile
        fd, other = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(other) and os.remove(other))
        self.assertIsNot(tagpup_db.lock_for(self.path), tagpup_db.lock_for(other))

    def test_the_same_database_spelled_two_ways_shares_one_lock(self):
        # Read-only connections are opened as file: URIs. Two spellings mapping to two
        # locks would mean the lock protects nothing.
        as_uri = "file:%s?mode=ro" % self.path
        self.assertIs(tagpup_db.lock_for(self.path), tagpup_db.lock_for(as_uri))

    def test_a_write_path_may_call_into_another(self):
        """Reentrant, or nesting two writes deadlocks the thread against itself."""
        def outer():
            return tagpup_db.write(self.path, lambda: "inner ran")
        self.assertEqual(tagpup_db.write(self.path, outer), "inner ran")


class TestBusyIsRetriedAndOtherFailuresAreNot(unittest.TestCase):
    def test_a_locked_database_is_retried(self):
        import sqlite3
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "written"

        self.assertEqual(
            tagpup_db.retry_when_busy(flaky, first_delay=0.001), "written"
        )
        self.assertEqual(len(attempts), 3)

    def test_a_real_error_is_raised_at_once(self):
        import sqlite3
        attempts = []

        def broken():
            attempts.append(1)
            raise sqlite3.OperationalError("no such table: nope")

        with self.assertRaises(sqlite3.OperationalError):
            tagpup_db.retry_when_busy(broken, first_delay=0.001)
        self.assertEqual(len(attempts), 1, "a broken statement was retried")

    def test_it_gives_up_eventually_rather_than_hanging(self):
        import sqlite3

        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            tagpup_db.retry_when_busy(always_locked, attempts=3, first_delay=0.001)


if __name__ == "__main__":
    unittest.main()

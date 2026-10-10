"""The newer-library guard in a process that has run for hours (docs/findings.md, the cached 'current').

`schema.ensure` remembers a library it found current by the stat of its main file. In WAL mode a newer app's commit goes
to the -wal file and the main file does not change while any connection holds a read mark, so a server, MCP or watcher
that had cached the library was never refused after a newer app migrated it. The cache now also asks the version again,
read only, once RECHECK_SECONDS have passed.
"""
import os
import sys
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def newer_app_commits(home, path, version):
    """What a newer TagPup does: another PROCESS commits a higher schema version to the library."""
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from tagpup.store import db
        conn = db.connect(%r)
        conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, 'a later step', '2026-10-09')",
                     (%d,))
        conn.commit()
        conn.close()
        """) % (ROOT, path, version)
    child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=home.root),
                            stdout=-1, stderr=-1, text=True)
    out, err = child.communicate(timeout=120)
    assert child.returncode == 0, err


class ALongLivedProcessAndANewerApp(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)
        schema._current.clear()
        self.clock = Clock()
        patch = mock.patch.object(schema, "_clock", self.clock)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(schema._current.clear)
        self.assertEqual([], schema.ensure(self.path), "found current, and remembered")
        # This process (a server's request thread, say) holds a read mark, as a long-lived one does: the newer app's
        # commit then stays in the -wal file and the main file is not written.
        self.reader = db.connect(db.readonly_uri(self.path), uri=True)
        self.reader.execute("BEGIN")
        self.reader.execute("SELECT COUNT(*) FROM schema_version").fetchall()
        self.addCleanup(self.reader.close)
        self.stat_before = schema._identity(self.path)
        newer_app_commits(self.home, self.path, schema.LATEST + 1)

    def test_the_premise_the_main_file_does_not_change(self):
        self.assertEqual(self.stat_before, schema._identity(self.path))
        self.assertEqual(schema.LATEST + 1, schema.version_of(self.path))

    def test_within_the_bound_it_is_still_served_and_after_it_refused(self):
        self.clock.now += schema.RECHECK_SECONDS / 2
        self.assertEqual([], schema.ensure(self.path), "inside the bound: no query, no refusal")
        self.clock.now += schema.RECHECK_SECONDS
        with self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.path)
        self.assertEqual((schema.LATEST + 1, schema.LATEST), (refused.exception.found, refused.exception.known))
        with self.assertRaises(schema.NewerLibrary):
            schema.ensure(self.path)   # and the next request too: nothing remembers it as current

    def test_a_write_through_the_journal_is_refused_after_the_bound_and_writes_nothing(self):
        self.clock.now += schema.RECHECK_SECONDS + 1
        with self.assertRaises(schema.NewerLibrary):
            journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        self.assertEqual([], db_rows(self.path, "SELECT 1 FROM tag_taxonomy WHERE tag = 'Fresh'"))

    def test_the_recovery_tools_still_read_it(self):
        self.clock.now += schema.RECHECK_SECONDS + 1
        with schema.reading_newer():
            self.assertEqual([], schema.ensure(self.path))
        with self.assertRaises(schema.NewerLibrary):
            schema.ensure(self.path)

    def test_the_server_answers_409_under_the_library_after_the_bound(self):
        app, home = web_client.app_for(self, "tagpup")
        path = home.library("library.db")
        schema._current.clear()
        client = app.test_client()
        self.assertEqual(200, client.get("/library/api/databases").status_code)
        reader = db.connect(db.readonly_uri(path), uri=True)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM schema_version").fetchall()
        self.addCleanup(reader.close)
        newer_app_commits(home, path, schema.LATEST + 1)
        self.assertEqual(200, client.get("/library/api/databases").status_code, "inside the bound")
        self.clock.now += schema.RECHECK_SECONDS + 1
        answer = client.get("/library/api/databases")
        self.assertEqual(409, answer.status_code)
        self.assertEqual(schema.newer_problem(path), answer.get_json()["error"])


class NothingIsRefusedWhileTheVersionIsUnchanged(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)
        schema._current.clear()
        self.clock = Clock()
        patch = mock.patch.object(schema, "_clock", self.clock)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(schema._current.clear)

    def test_hours_pass_the_version_is_asked_once_per_bound_and_nothing_is_refused(self):
        schema.ensure(self.path)
        with mock.patch.object(schema, "_still_current", wraps=schema._still_current) as asked, \
                mock.patch.object(schema, "_ensure", side_effect=AssertionError("no full open")):
            for _ in range(5):
                self.clock.now += schema.RECHECK_SECONDS / 4
                self.assertEqual([], schema.ensure(self.path))
            self.clock.now += 3 * 3600
            self.assertEqual([], schema.ensure(self.path))
            self.assertEqual([], schema.ensure(self.path))
        self.assertEqual(2, asked.call_count, "once when the first bound ran out, once after the hours")

    def test_a_library_that_cannot_be_read_just_now_is_looked_at_in_full_not_decided(self):
        schema.ensure(self.path)
        self.clock.now += schema.RECHECK_SECONDS + 1
        with mock.patch.object(schema, "_still_current", return_value=False), \
                mock.patch.object(schema, "_ensure", return_value=[]) as full:
            schema.ensure(self.path)
        self.assertEqual(1, full.call_count)


def db_rows(path, sql):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()

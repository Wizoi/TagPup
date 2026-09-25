"""Background work started by a request stays with the library the request named.

The per-database registries (the folder cache among them) resolved through a
thread-local set during request handling. Worker threads spawned by a request did not
inherit that thread-local, and the class-level fallback pointed at the *startup*
database. Any worker that read or wrote an isolated registry had to re-bind the active
database, or it silently operated on the wrong one (docs/findings.md, #44). A
background job is handed its Library now (tagpup.web.state), and these check the
handing over.

These drive the app through a NON-DEFAULT library prefix, which is the only
configuration where the old thread-local and the class-level fallback disagreed.
test_multiple_databases.py exercises the startup library only, where the two happened
to agree and these defects were invisible. No server, no port, no sleep: the queue's
worker is run on this thread, and the suggestion run too.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import web_client  # noqa: E402
from face_rows import add_face, add_people  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db as tagpup_store_db  # noqa: E402
from tagpup.store import schema  # noqa: E402


def mock_indexer():
    """A subprocess stand-in for the CLI indexer, which reports and succeeds."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout.readline.side_effect = [
        "Scanning directory...\n",
        "Indexing photos:  50%\n",
        "Indexing photos: 100%\n",
        "",
    ]
    return proc


class TestWorkerThreadDatabaseBinding(unittest.TestCase):
    """The startup library and the library addressed in the URL are deliberately
    different."""

    STARTUP_NAME = "test_ctx_startup.db"   # in a home of the test's own
    OTHER_URL_NAME = "ctx_other"
    OTHER_NAME = "test_ctx_other.db"

    def setUp(self):
        self.app, home = web_client.app_for(self, "tagpup", startup=self.STARTUP_NAME)
        self.client = self.app.test_client()
        self.STARTUP_DB = home.library(self.STARTUP_NAME)
        self.OTHER_DB = home.library(self.OTHER_NAME)
        library_actions.create(self.OTHER_DB)
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_ctx_", dir=home.root)
        for library in (Library(self.STARTUP_DB), Library(self.OTHER_DB)):
            self.addCleanup(indexing_jobs.forget, library)
            self.addCleanup(suggestion_jobs.forget, library)

    def startup_name(self):
        name = os.path.splitext(os.path.basename(self.STARTUP_DB))[0]
        return name[5:] if name.startswith("test_") else name

    def post(self, path, body):
        reply = self.client.post(path, json=body)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def get(self, path, query=None):
        reply = self.client.get(path, query_string=query)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def test_startup_and_addressed_databases_actually_differ(self):
        """Guard the premise: if these ever coincide, the tests below prove nothing.

        Proven by round-tripping distinct rows through each database, since the URL
        prefix -- not config.ini -- is what decides which one a request reads.
        """
        self.assertNotEqual(self.startup_name(), self.OTHER_URL_NAME)

        # /api/people is sourced from resolved face names, so seed the faces table.
        for db, person in ((self.STARTUP_DB, "StartupOnly"), (self.OTHER_DB, "OtherOnly")):
            conn = tagpup_store_db.connect(db)
            conn.execute("DELETE FROM photos")
            conn.execute("DELETE FROM faces")
            conn.execute(
                "INSERT INTO photos (path, mtime, size, tags) VALUES (?, ?, ?, ?)",
                (f"C:/{person}.jpg", 1.0, 1, json.dumps([])),
            )
            add_people(conn, f"C:/{person}.jpg", [person])
            add_face(conn, f"C:/{person}.jpg", box="[]", embedding=b"", name=person)
            conn.commit()
            conn.close()

        startup_people = self.get(f"/{self.startup_name()}/api/people")
        other_people = self.get(f"/{self.OTHER_URL_NAME}/api/people")
        self.assertIn("StartupOnly", startup_people)
        self.assertIn("OtherOnly", other_people)
        self.assertNotIn("OtherOnly", startup_people)
        self.assertNotIn("StartupOnly", other_people)

    def index_the_folder(self, mock_popen):
        """Queue the folder under the other library and run its worker here."""
        queue = indexing_jobs.queue_for(Library(self.OTHER_DB))
        with patch.object(indexing_jobs.IndexQueue, "_ensure_runner"):
            res = self.post(f"/{self.OTHER_URL_NAME}/api/folder/index-start", {"folder_path": self.tmpdir})
        self.assertTrue(res["success"])
        queue.run_pending()
        return queue

    @patch("subprocess.Popen")
    def test_index_worker_reports_completion_under_non_default_database(self, mock_popen):
        """The index worker must find its own status entry and drive it to completion.

        Before the fix the worker looked the entry up in the startup database's registry,
        raised KeyError before its try block, and died without ever spawning the indexer,
        leaving the UI polling 'running' forever.
        """
        mock_popen.return_value = mock_indexer()
        self.index_the_folder(mock_popen)
        data = self.get(f"/{self.OTHER_URL_NAME}/api/folder/index-status", {"path": self.tmpdir})
        self.assertEqual(data.get("status"), "completed", f"worker never completed: {data}")
        self.assertEqual(data.get("percent"), 100)
        # The indexer subprocess must actually have been launched.
        self.assertTrue(mock_popen.called, "index subprocess was never spawned")

    @patch("subprocess.Popen")
    def test_index_worker_status_does_not_leak_into_startup_database(self, mock_popen):
        """Work started under one database must not appear under another."""
        mock_popen.return_value = mock_indexer()
        self.index_the_folder(mock_popen)
        # The startup database must report the untouched default for this folder.
        other = self.get(f"/{self.startup_name()}/api/folder/index-status", {"path": self.tmpdir})
        self.assertEqual(other.get("message"), "Ready")

    def test_suggest_worker_surfaces_failure_under_non_default_database(self):
        """A failing suggest worker must record 'error' in the caller's own registry.

        Before the fix the error branch was guarded by a membership check against the
        wrong registry, so the failure was never recorded and the UI polled 'preparing'
        indefinitely. An empty folder reaches that branch without loading CLIP.
        """
        def run_here(runs, folder, work):
            runs.run(folder, work)
            return "running"

        with patch.object(suggestion_jobs.SuggestionRuns, "start", run_here):
            res = self.post(f"/{self.OTHER_URL_NAME}/api/folder/suggest-start", {"folder_path": self.tmpdir})
        self.assertTrue(res["success"])
        data = self.get(f"/{self.OTHER_URL_NAME}/api/folder/suggest-status", {"path": self.tmpdir})
        self.assertEqual(data.get("status"), "error",
                         f"suggest worker never reported terminal status (stuck at {data.get('status')!r})")
        self.assertEqual(self.get(f"/{self.startup_name()}/api/folder/suggest-status",
                                  {"path": self.tmpdir}).get("status"), "idle")

    def test_index_worker_survives_missing_status_entry(self):
        """The worker must not die if a folder's status is gone when it gets to it.

        The folder is queued through the route under the non-default library, and so
        must land in that library's queue."""
        queue = indexing_jobs.queue_for(Library(self.OTHER_DB))
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = mock_indexer()
            with patch.object(indexing_jobs.IndexQueue, "_ensure_runner"):
                self.post(f"/{self.OTHER_URL_NAME}/api/folder/index-start", {"folder_path": self.tmpdir})
            self.assertEqual([job["folder"] for job in queue.pending()], [paths.stored(self.tmpdir)])
            queue._statuses.clear()
            queue.run_pending()
        self.assertEqual(queue.status(self.tmpdir).get("status"), "completed")


class TestSuggestionsCachePerDatabase(unittest.TestCase):
    """The suggestions cache file was scoped per database, and migration 7 must read
    each library's own (tagpup.store.schema; the suggestions are rows of the library now).

    The worker serialized whichever registry was active, so a shared filename let a
    secondary database overwrite the primary library's cache wholesale; a migration
    reading another library's file would take in that library's suggestions.
    """

    def file_of(self, name):
        import shutil

        folder = tempfile.mkdtemp(prefix="sugg_file_")
        self.addCleanup(shutil.rmtree, folder, True)
        conn = tagpup_store_db.connect(os.path.join(folder, name))
        try:
            return os.path.basename(schema._suggestions_file(conn))
        finally:
            conn.close()

    def test_default_database_keeps_unsuffixed_filename(self):
        self.assertEqual(self.file_of("photo_index.db"), "gui_suggestions_cache.json")

    def test_secondary_database_gets_its_own_file(self):
        self.assertEqual(self.file_of("kr-track.db"), "gui_suggestions_cache_kr-track.json")

    def test_distinct_databases_never_share_a_cache_file(self):
        self.assertNotEqual(self.file_of("photo_index.db"), self.file_of("kr-track.db"))

    # TagTuner's copy of this naming, which a test here kept in step, is gone: it had
    # no callers, and the migration is now the only place the file is named
    # (tests/test_suggestions_pipeline.py, OneFileOneOwner).


if __name__ == "__main__":
    unittest.main()

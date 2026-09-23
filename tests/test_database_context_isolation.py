"""Regression tests for database context propagation into background worker threads.

The per-database registries (index_status, suggest_status, folder_cache) resolve through
a thread-local set during request handling. Worker threads spawned by a request do not
inherit that thread-local, and the class-level fallback points at the *startup* database.
Any worker that reads or writes an isolated registry must therefore re-bind the active
database, or it silently operates on the wrong one.

These tests drive the servers through a NON-DEFAULT database prefix, which is the only
configuration where the thread-local and the class-level fallback disagree. Existing
coverage in test_multiple_databases.py exercises the startup database only, where the
two happen to agree and these defects are invisible.
"""
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch, MagicMock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup_server import (
    start_server as start_tagpup_server,
    TagPupHTTPRequestHandler,
    set_active_db_path,
)
import paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from free_port import free_port  # noqa: E402


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _poll_until(port, path, terminal, timeout=30.0, interval=0.1):
    """Poll a status endpoint until it reports one of `terminal`. Returns the last payload."""
    deadline = time.time() + timeout
    data = {}
    while time.time() < deadline:
        data = _get(port, path)
        if data.get("status") in terminal:
            return data
        time.sleep(interval)
    return data


class TestWorkerThreadDatabaseBinding(unittest.TestCase):
    """The startup DB and the DB addressed in the URL are deliberately different."""

    TEST_PORT = free_port()
    # Server boots on this database...
    STARTUP_DB = os.path.join(WORKSPACE_DIR, "data", "test_ctx_startup.db")
    # ...but every request below is addressed to this one via the URL prefix.
    OTHER_DB_URL_NAME = "ctx_other"
    OTHER_DB = os.path.join(WORKSPACE_DIR, "data", "test_ctx_other.db")

    @classmethod
    def setUpClass(cls):
        # Its own port: subclasses inherit the attribute, and a port
        # already held by the last class's server is refused.
        cls.TEST_PORT = free_port()
        from index import PhotoIndex

        for db in (cls.STARTUP_DB, cls.OTHER_DB):
            pi = PhotoIndex(db_path=db)
            pi.load()
            pi.close()

        cls.server_thread = threading.Thread(
            target=start_tagpup_server,
            kwargs={
                "port": cls.TEST_PORT,
                "db_path": cls.STARTUP_DB,
                "gui_dir": os.path.join(WORKSPACE_DIR, "gui_tagpup"),
            },
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(1.0)  # wait for bind

    @classmethod
    def tearDownClass(cls):
        set_active_db_path(None)
        for db in (cls.STARTUP_DB, cls.OTHER_DB):
            for path in (db, db.replace(".db", "_taxonomy.json")):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_ctx_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)

    def test_startup_and_addressed_databases_actually_differ(self):
        """Guard the premise: if these ever coincide, the tests below prove nothing.

        Proven by round-tripping distinct rows through each database, since the URL
        prefix -- not config.ini -- is what decides which one a request reads.
        """
        import sqlite3

        startup_name = os.path.splitext(os.path.basename(self.STARTUP_DB))[0]
        if startup_name.startswith("test_"):
            startup_name = startup_name[5:]
        self.assertNotEqual(startup_name, self.OTHER_DB_URL_NAME)

        # /api/people is sourced from resolved face names, so seed the faces table.
        for db, person in ((self.STARTUP_DB, "StartupOnly"), (self.OTHER_DB, "OtherOnly")):
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM photos")
            conn.execute("DELETE FROM faces")
            conn.execute(
                "INSERT INTO photos (path, mtime, size, people, tags) VALUES (?, ?, ?, ?, ?)",
                (f"C:/{person}.jpg", 1.0, 1, json.dumps([person]), json.dumps([])),
            )
            conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, ?, ?, ?)",
                (f"C:/{person}.jpg", "[]", b"", person),
            )
            conn.commit()
            conn.close()

        startup_people = _get(self.TEST_PORT, f"/{startup_name}/api/people")
        other_people = _get(self.TEST_PORT, f"/{self.OTHER_DB_URL_NAME}/api/people")
        self.assertIn("StartupOnly", startup_people)
        self.assertIn("OtherOnly", other_people)
        self.assertNotIn("OtherOnly", startup_people)
        self.assertNotIn("StartupOnly", other_people)

    @patch("subprocess.Popen")
    def test_index_worker_reports_completion_under_non_default_database(self, mock_popen):
        """The index worker must find its own status entry and drive it to completion.

        Before the fix the worker looked the entry up in the startup database's registry,
        raised KeyError before its try block, and died without ever spawning the indexer,
        leaving the UI polling 'running' forever.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.side_effect = [
            "Scanning directory...\n",
            "Indexing photos:  50%\n",
            "Indexing photos: 100%\n",
            "",
        ]
        mock_popen.return_value = mock_proc

        res = _post(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/index-start",
            {"folder_path": self.tmpdir},
        )
        self.assertTrue(res["success"])

        q = urllib.parse.quote(self.tmpdir)
        data = _poll_until(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/index-status?path={q}",
            terminal=("completed", "failed"),
        )
        self.assertEqual(data.get("status"), "completed", f"worker never completed: {data}")
        self.assertEqual(data.get("percent"), 100)
        # The indexer subprocess must actually have been launched.
        self.assertTrue(mock_popen.called, "index subprocess was never spawned")

    @patch("subprocess.Popen")
    def test_index_worker_status_does_not_leak_into_startup_database(self, mock_popen):
        """Work started under one database must not appear under another."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.side_effect = ["done\n", ""]
        mock_popen.return_value = mock_proc

        _post(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/index-start",
            {"folder_path": self.tmpdir},
        )
        q = urllib.parse.quote(self.tmpdir)
        _poll_until(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/index-status?path={q}",
            terminal=("completed", "failed"),
        )

        # The startup database must report the untouched default for this folder.
        startup_name = os.path.splitext(os.path.basename(self.STARTUP_DB))[0]
        if startup_name.startswith("test_"):
            startup_name = startup_name[5:]
        other = _get(self.TEST_PORT, f"/{startup_name}/api/folder/index-status?path={q}")
        self.assertEqual(other.get("message"), "Ready")

    def test_suggest_worker_surfaces_failure_under_non_default_database(self):
        """A failing suggest worker must record 'error' in the caller's own registry.

        Before the fix the error branch was guarded by a membership check against the
        wrong registry, so the failure was never recorded and the UI polled 'preparing'
        indefinitely. An empty folder reaches that branch without loading CLIP.
        """
        res = _post(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/suggest-start",
            {"folder_path": self.tmpdir},
        )
        self.assertTrue(res["success"])

        q = urllib.parse.quote(self.tmpdir)
        data = _poll_until(
            self.TEST_PORT,
            f"/{self.OTHER_DB_URL_NAME}/api/folder/suggest-status?path={q}",
            terminal=("error", "completed"),
        )
        self.assertEqual(
            data.get("status"),
            "error",
            f"suggest worker never reported terminal status (stuck at {data.get('status')!r})",
        )

    def test_index_worker_survives_missing_status_entry(self):
        """The worker must not die if its status entry is absent when it starts."""
        folder = self.tmpdir
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout.readline.side_effect = ["done\n", ""]
            mock_popen.return_value = mock_proc

            set_active_db_path(self.OTHER_DB)
            TagPupHTTPRequestHandler.index_status.pop(paths.key(folder), None)
            set_active_db_path(None)

            t = threading.Thread(
                target=TagPupHTTPRequestHandler.run_folder_index_thread,
                args=(folder, self.OTHER_DB),
                daemon=True,
            )
            t.start()
            t.join(timeout=20)
            self.assertFalse(t.is_alive(), "worker thread hung")

        set_active_db_path(self.OTHER_DB)
        status = TagPupHTTPRequestHandler.index_status.get(paths.key(folder))
        self.assertIsNotNone(status, "worker died without recording any status")
        self.assertEqual(status.get("status"), "completed")


class TestSuggestionsCachePerDatabase(unittest.TestCase):
    """The suggestions cache file must be scoped per database.

    The worker serializes whichever registry is active, so a shared filename lets a
    secondary database overwrite the primary library's cache wholesale.
    """

    def test_default_database_keeps_unsuffixed_filename(self):
        path = TagPupHTTPRequestHandler._suggestions_cache_path(os.path.join("data", "photo_index.db"))
        self.assertEqual(os.path.basename(path), "gui_suggestions_cache.json")

    def test_secondary_database_gets_its_own_file(self):
        path = TagPupHTTPRequestHandler._suggestions_cache_path(os.path.join("data", "kr-track.db"))
        self.assertEqual(os.path.basename(path), "gui_suggestions_cache_kr-track.json")

    def test_distinct_databases_never_share_a_cache_file(self):
        a = TagPupHTTPRequestHandler._suggestions_cache_path(os.path.join("data", "photo_index.db"))
        b = TagPupHTTPRequestHandler._suggestions_cache_path(os.path.join("data", "kr-track.db"))
        self.assertNotEqual(a, b)

    # TagTuner's copy of this naming, which a test here kept in step, is gone: it had
    # no callers, and tagpup_server is now the only place the file is named
    # (tests/test_suggestions_pipeline.py, OneFileOneOwner).


if __name__ == "__main__":
    unittest.main()

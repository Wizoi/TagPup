"""Selecting and creating libraries, and reading each through its URL, in a TAGPUP_HOME
of the test's own.

These wrote the checkout's config.ini -- the one the app somebody is using reads --
and put it back afterwards, so a run stopped in between left that app pointing at a
test library. They also made their libraries in the checkout's data folder.

The code folder is one of the test's own as well, holding settings of its own, so
that "nothing was written beside the code" is checked without reading the checkout.
The apps are asked through Flask's test client: no server, no port, no sleep.
"""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import web_client  # noqa: E402
from face_rows import add_face, add_people  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402


def file_bytes(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


class TestMultipleDatabases(unittest.TestCase):
    """The picker, as TagTuner's app serves it; the routes are the same blueprint in
    both apps (tagpup.web.libraries)."""

    def setUp(self):
        self.app, home = web_client.app_for(self, "tuner", startup="test_multiple_db_startup.db")
        self.client = self.app.test_client()
        self.data_dir = home.data
        self.TEST_DB_PATH = home.library("test_multiple_db_startup.db")
        code_folder = tempfile.mkdtemp(prefix="tagpup_code_folder_")
        self.addCleanup(own_home.remove, code_folder)
        tagpup_config.write_file({"paths": {"default_db": "the_code_folders.db"}}, folder=code_folder)
        self.code_config = tagpup_config.config_path(code_folder)
        self.code_config_bytes = file_bytes(self.code_config)
        code_root = mock.patch.object(tagpup_config, "CODE_ROOT", code_folder)
        code_root.start()
        self.addCleanup(code_root.stop)

    def get(self, path):
        reply = self.client.get(path)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def post(self, path, body):
        reply = self.client.post(path, json=body)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def test_database_api_endpoints(self):
        # 1. GET /api/databases - the startup library is offered: it was hidden from
        # the list by name while the tests kept theirs in the checkout (#14). Nothing
        # is "selected": which library was opened last is the browser's to remember
        # (#100), and the server writes no setting.
        data = self.get("/api/databases")
        self.assertEqual(["databases"], list(data))
        self.assertIn("multiple_db_startup", data["databases"])

        # 2. POST /api/databases/create - create a new database (without .db suffix in request)
        create_res = self.post("/api/databases/create", {"db_name": "created_db_1"})
        self.assertTrue(create_res["success"])
        # Server stores it as test_created_db_1.db, but returns created_db_1
        self.assertEqual(create_res["db_name"], "created_db_1")

        # Verify it was created on disk in data folder with test_ prefix because the server runs in test mode
        expected_fs_path = os.path.join(self.data_dir, "test_created_db_1.db")
        self.assertTrue(os.path.exists(expected_fs_path), f"File {expected_fs_path} should be created on disk")

        # Verify schema exists by connecting to it
        conn = tagpup_db.connect(tagpup_db.readonly_uri(expected_fs_path), uri=True)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        self.assertIn("photos", tables)
        self.assertIn("faces", tables)
        self.assertIn("tag_taxonomy", tables)

        # 3. GET /api/databases again - new DB should now be listed without .db extension
        self.assertIn("created_db_1", self.get("/api/databases")["databases"])

        # 4. There is no route to remember a choice by, and no setting was written:
        # neither this home's nor the one beside the code.
        self.assertEqual(404, self.client.post("/api/databases/select", json={"db_name": "x"}).status_code)
        self.assertFalse(tagpup_config.read_file().has_option("paths", "default_db"))
        self.assertEqual(file_bytes(self.code_config), self.code_config_bytes,
                         "creating a library changed the config.ini beside the code")

    def test_a_created_library_is_not_left_open(self):
        """Creating a library opened it for its schema and never closed it.

        The file stayed open until a garbage-collection pass: on Windows it could not be
        moved or deleted until then, and this test's own home could not be removed.
        Collection is off here, so the old behaviour fails every time rather than only
        when no pass happened to run.
        """
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            self.post("/api/databases/create", {"db_name": "created_db_1"})
            created = os.path.join(self.data_dir, "test_created_db_1.db")
            self.assertTrue(os.path.exists(created))
            try:
                os.remove(created)
            except PermissionError:
                self.fail("the new library is still held open by the server")
        finally:
            if was_enabled:
                gc.enable()

    def test_prefix_routing_and_isolation(self):
        # Create two database files
        for name in ["created_db_1", "created_db_2"]:
            self.post("/api/databases/create", {"db_name": name})

        # Insert different dummy people records in each database directly
        db1_path = os.path.join(self.data_dir, "test_created_db_1.db")
        db2_path = os.path.join(self.data_dir, "test_created_db_2.db")

        conn1 = tagpup_db.connect(db1_path)
        conn1.execute("INSERT INTO photos (path, mtime, size) VALUES (?, ?, ?)",
                      ("C:/photo1.jpg", 1.0, 100))
        add_people(conn1, "C:/photo1.jpg", ["Alice"])
        add_face(conn1, "C:/photo1.jpg", box="[]", embedding=b"", name="Alice")
        conn1.commit()
        conn1.close()

        conn2 = tagpup_db.connect(db2_path)
        conn2.execute("INSERT INTO photos (path, mtime, size) VALUES (?, ?, ?)",
                      ("C:/photo2.jpg", 2.0, 200))
        add_people(conn2, "C:/photo2.jpg", ["Bob"])
        add_face(conn2, "C:/photo2.jpg", box="[]", embedding=b"", name="Bob")
        conn2.commit()
        conn2.close()

        # Query people using database-specific prefix routing subfolders (without .db extension):
        self.assertEqual(self.get("/created_db_1/api/people"), ["Alice"])
        self.assertEqual(self.get("/created_db_2/api/people"), ["Bob"])

        # Caches isolation check: each library's server state is its own, filed by
        # the library rather than by a thread-local set by hand (tagpup.web.state).
        from tagpup.web.state import PerLibrary

        held = PerLibrary(lambda library: {})
        held.of(Library(db1_path))["shared_key"] = "value_1"
        held.of(Library(db2_path))["shared_key"] = "value_2"
        self.assertEqual(held.of(Library(db1_path))["shared_key"], "value_1")
        self.assertEqual(held.of(Library(db2_path))["shared_key"], "value_2")


class TestFolderIndexingAPI(unittest.TestCase):
    def setUp(self):
        self.app, home = web_client.app_for(self, "tagpup", startup="test_index_api.db")
        self.client = self.app.test_client()
        self.library = Library(home.library("test_index_api.db"))
        self.addCleanup(indexing_jobs.forget, self.library)

    @patch("subprocess.Popen")
    def test_folder_indexing_flow(self, mock_popen):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.side_effect = [
            "Scanning directory...\n",
            "Indexing photos:  50%\n",
            "Indexing photos: 100%\n",
            ""
        ]
        mock_popen.return_value = mock_proc

        # 1. Trigger indexing via POST /api/folder/index-start; the queue's worker runs
        # on this thread rather than one of its own, so nothing has to be waited for.
        with patch.object(indexing_jobs.IndexQueue, "_ensure_runner"):
            reply = self.client.post("/api/folder/index-start", json={"folder_path": WORKSPACE_DIR})
        res = reply.get_json()
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "running")
        self.assertEqual(self.client.get("/api/folder/index-status",
                                         query_string={"path": WORKSPACE_DIR}).get_json()["status"], "queued")
        indexing_jobs.queue_for(self.library).run_pending()

        # 2. Its progress via GET /api/folder/index-status
        status_data = self.client.get("/api/folder/index-status", query_string={"path": WORKSPACE_DIR}).get_json()
        self.assertEqual(status_data.get("status"), "completed")
        self.assertEqual(status_data["percent"], 100)


if __name__ == "__main__":
    unittest.main()

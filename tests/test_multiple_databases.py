import os
import sys
import json
import sqlite3
import urllib.request
import urllib.error
import threading
import time
import unittest
import configparser
from unittest.mock import patch, MagicMock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tuner_server import start_server, TunerHTTPRequestHandler

class TestMultipleDatabases(unittest.TestCase):
    TEST_PORT = 9911
    TEST_DB_PATH = os.path.join(WORKSPACE_DIR, "data", "test_multiple_db_startup.db")
    server_thread = None
    original_default_db = None

    @classmethod
    def setUpClass(cls):
        # Save original default_db from config.ini if exists
        config_path = os.path.join(WORKSPACE_DIR, "config.ini")
        cls.config = configparser.ConfigParser(interpolation=None)
        if os.path.exists(config_path):
            cls.config.read(config_path, encoding='utf-8')
            if cls.config.has_section("paths") and cls.config.has_option("paths", "default_db"):
                cls.original_default_db = cls.config.get("paths", "default_db")

        # Ensure startup db file is created before server starts to avoid migration warning
        from index import PhotoIndex
        pi = PhotoIndex(db_path=cls.TEST_DB_PATH)
        pi.load()
        pi.close()

        # Start the tuner server once
        cls.server_thread = threading.Thread(
            target=start_server,
            kwargs={"port": cls.TEST_PORT, "db_path": cls.TEST_DB_PATH, "gui_dir": os.path.join(WORKSPACE_DIR, "gui")},
            daemon=True
        )
        cls.server_thread.start()
        time.sleep(1.0) # wait for bind

    @classmethod
    def tearDownClass(cls):
        # Restore original default_db
        config_path = os.path.join(WORKSPACE_DIR, "config.ini")
        if cls.original_default_db is not None and os.path.exists(config_path):
            cls.config.read(config_path, encoding='utf-8')
            if not cls.config.has_section("paths"):
                cls.config.add_section("paths")
            cls.config.set("paths", "default_db", cls.original_default_db)
            with open(config_path, "w", encoding="utf-8") as f:
                cls.config.write(f)

    def setUp(self):
        # Ensure startup db file is clean
        if os.path.exists(self.TEST_DB_PATH):
            try:
                os.remove(self.TEST_DB_PATH)
            except Exception:
                pass
        
        # Initialize the database on disk
        from index import PhotoIndex
        pi = PhotoIndex(db_path=self.TEST_DB_PATH)
        pi.load()
        pi.close()
        
        # Ensure test database files we create in tests are also cleaned up
        for name in ["test_created_db_1.db", "test_created_db_2.db"]:
            p = os.path.join(WORKSPACE_DIR, "data", name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
            # Also taxonomy json
            t = p.replace(".db", "_taxonomy.json")
            if os.path.exists(t):
                try:
                    os.remove(t)
                except Exception:
                    pass

    def tearDown(self):
        from tuner_server import set_active_db_path
        set_active_db_path(None)
        self.setUp()

    def test_database_api_endpoints(self):
        # 1. GET /api/databases - verify startup DB selected, but excluded from databases dropdown list
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/databases"
        response = urllib.request.urlopen(url)
        data = json.loads(response.read().decode('utf-8'))
        
        self.assertIn("databases", data)
        self.assertIn("selected", data)
        self.assertNotIn("multiple_db_startup", data["databases"])
        self.assertTrue(data["selected"])

        # 2. POST /api/databases/create - create a new database (without .db suffix in request)
        new_db_clean_name = "created_db_1"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/databases/create",
            data=json.dumps({"db_name": new_db_clean_name}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        create_res = json.loads(response.read().decode('utf-8'))
        self.assertTrue(create_res["success"])
        # Server stores it as test_created_db_1.db, but returns created_db_1
        self.assertEqual(create_res["db_name"], "created_db_1")

        # Verify it was created on disk in data folder with test_ prefix because the server runs in test mode
        expected_fs_path = os.path.join(WORKSPACE_DIR, "data", "test_created_db_1.db")
        self.assertTrue(os.path.exists(expected_fs_path), f"File {expected_fs_path} should be created on disk")
        
        # Verify schema exists by connecting to it
        conn = sqlite3.connect(expected_fs_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        self.assertIn("photos", tables)
        self.assertIn("faces", tables)
        self.assertIn("tag_taxonomy", tables)

        # 3. GET /api/databases again - new DB should now be listed without .db extension
        response = urllib.request.urlopen(url)
        data = json.loads(response.read().decode('utf-8'))
        self.assertIn("created_db_1", data["databases"])
        # And it should be selected because create sets it as default
        self.assertEqual(data["selected"], "created_db_1")

        # 4. POST /api/databases/select - select database back to startup (without .db suffix in request)
        req_select = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/databases/select",
            data=json.dumps({"db_name": "multiple_db_startup"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req_select)
        select_res = json.loads(response.read().decode('utf-8'))
        self.assertTrue(select_res["success"])

        # Verify selected changed
        response = urllib.request.urlopen(url)
        data = json.loads(response.read().decode('utf-8'))
        self.assertEqual(data["selected"], "multiple_db_startup")

    def test_prefix_routing_and_isolation(self):
        # Create two database files
        for name in ["created_db_1", "created_db_2"]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.TEST_PORT}/api/databases/create",
                data=json.dumps({"db_name": name}).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req)

        # Insert different dummy people records in each database directly via SQLite
        db1_path = os.path.join(WORKSPACE_DIR, "data", "test_created_db_1.db")
        db2_path = os.path.join(WORKSPACE_DIR, "data", "test_created_db_2.db")

        conn1 = sqlite3.connect(db1_path)
        conn1.execute("INSERT INTO photos (path, mtime, size, people) VALUES (?, ?, ?, ?)",
                      ("C:/photo1.jpg", 1.0, 100, json.dumps(["Alice"])))
        conn1.execute("INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, ?, ?, ?)",
                      ("C:/photo1.jpg", "[]", b"", "Alice"))
        conn1.commit()
        conn1.close()

        conn2 = sqlite3.connect(db2_path)
        conn2.execute("INSERT INTO photos (path, mtime, size, people) VALUES (?, ?, ?, ?)",
                      ("C:/photo2.jpg", 2.0, 200, json.dumps(["Bob"])))
        conn2.execute("INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, ?, ?, ?)",
                      ("C:/photo2.jpg", "[]", b"", "Bob"))
        conn2.commit()
        conn2.close()

        # Query people using database-specific prefix routing subfolders (without .db extension):
        # DB 1
        url_db1 = f"http://127.0.0.1:{self.TEST_PORT}/created_db_1/api/people"
        response_db1 = urllib.request.urlopen(url_db1)
        people_db1 = json.loads(response_db1.read().decode('utf-8'))
        self.assertEqual(people_db1, ["Alice"])

        # DB 2
        url_db2 = f"http://127.0.0.1:{self.TEST_PORT}/created_db_2/api/people"
        response_db2 = urllib.request.urlopen(url_db2)
        people_db2 = json.loads(response_db2.read().decode('utf-8'))
        self.assertEqual(people_db2, ["Bob"])

        # Caches Isolation check - folders cache
        from tuner_server import set_active_db_path
        
        set_active_db_path(db1_path)
        TunerHTTPRequestHandler.folder_cache["shared_key"] = "value_1"

        set_active_db_path(db2_path)
        TunerHTTPRequestHandler.folder_cache["shared_key"] = "value_2"

        # Verify they are isolated
        set_active_db_path(db1_path)
        self.assertEqual(TunerHTTPRequestHandler.folder_cache["shared_key"], "value_1")

        set_active_db_path(db2_path)
        self.assertEqual(TunerHTTPRequestHandler.folder_cache["shared_key"], "value_2")


class TestFolderIndexingAPI(unittest.TestCase):
    TEST_PORT = 9922
    TEST_DB_PATH = os.path.join(WORKSPACE_DIR, "data", "test_index_api.db")
    server_thread = None

    @classmethod
    def setUpClass(cls):
        # Create DB file
        from index import PhotoIndex
        pi = PhotoIndex(db_path=cls.TEST_DB_PATH)
        pi.load()
        pi.close()

        cls.server_thread = threading.Thread(
            target=start_server, # we can use tuner_server or import tagpup_server
            kwargs={"port": cls.TEST_PORT, "db_path": cls.TEST_DB_PATH, "gui_dir": os.path.join(WORKSPACE_DIR, "gui_tagpup")},
            daemon=True
        )
        # Actually import tagpup_server's start_server to test the right server class
        from tagpup_server import start_server as start_tagpup_server
        cls.server_thread = threading.Thread(
            target=start_tagpup_server,
            kwargs={"port": cls.TEST_PORT, "db_path": cls.TEST_DB_PATH, "gui_dir": os.path.join(WORKSPACE_DIR, "gui_tagpup")},
            daemon=True
        )
        cls.server_thread.start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TEST_DB_PATH):
            try:
                os.remove(cls.TEST_DB_PATH)
            except Exception:
                pass
        # Also clean up index status cache if created
        cache_json = cls.TEST_DB_PATH.replace(".db", "_taxonomy.json")
        if os.path.exists(cache_json):
            try:
                os.remove(cache_json)
            except Exception:
                pass

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

        # 1. Trigger indexing via POST /api/folder/index-start
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/folder/index-start",
            data=json.dumps({"folder_path": WORKSPACE_DIR}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        res = json.loads(response.read().decode('utf-8'))
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "running")

        # 2. Poll progress via GET /api/folder/index-status
        status = "running"
        for _ in range(30):
            time.sleep(0.1)
            url = f"http://127.0.0.1:{self.TEST_PORT}/api/folder/index-status?path={urllib.parse.quote(WORKSPACE_DIR)}"
            response = urllib.request.urlopen(url)
            status_data = json.loads(response.read().decode('utf-8'))
            status = status_data.get("status")
            if status in ("completed", "failed"):
                self.assertEqual(status, "completed")
                self.assertEqual(status_data["percent"], 100)
                break
        self.assertEqual(status, "completed")

if __name__ == "__main__":
    unittest.main()

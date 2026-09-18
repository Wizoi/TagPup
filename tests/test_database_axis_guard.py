"""Standing guards that keep the multi-database axis honest.

Two complementary nets against the class of defect where code resolves "the current
database" implicitly and silently picks the wrong one:

  1. A structural guard (AST) asserting every background worker that is handed a
     `db_path` re-binds it, since a new thread does not inherit the request's
     thread-local and falls back to the *startup* database.
  2. Behavioural checks that reads through a URL database prefix never observe another
     database's rows, run against both servers.

The structural guard is deliberately mechanical: it fires on a newly added worker
before anyone has to reproduce a hung progress bar to discover the problem.
"""
import os
import re
import sys
import ast
import json
import time
import sqlite3
import threading
import unittest
import urllib.request

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")
SERVER_MODULES = (
    os.path.join(SCRIPTS_DIR, "tagpup_server.py"),
    os.path.join(SCRIPTS_DIR, "tuner_server.py"),
)


def _target_name(node):
    """Resolve the `target=` argument of a threading.Thread(...) call to a bare name."""
    for kw in node.keywords:
        if kw.arg != "target":
            continue
        val = kw.value
        if isinstance(val, ast.Name):
            return val.id
        if isinstance(val, ast.Attribute):
            return val.attr
    return None


def _thread_target_names(tree):
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_thread = (isinstance(func, ast.Attribute) and func.attr == "Thread") or (
            isinstance(func, ast.Name) and func.id == "Thread"
        )
        if is_thread:
            name = _target_name(node)
            if name:
                names.add(name)
    return names


def _function_defs(tree):
    defs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    return defs


def _calls_set_active_db_path(fn_node):
    """True if the function's OWN body binds the database.

    Nested helpers are skipped deliberately: a binding inside an inner function runs
    only when that helper is called, which does not protect the outer worker's own
    registry access. Counting it would mask exactly the defect this guard exists for.
    """
    def walk_own_body(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from walk_own_body(child)

    for node in walk_own_body(fn_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "set_active_db_path":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "set_active_db_path":
                return True
    return False


def _takes_db_path(fn_node):
    args = [a.arg for a in fn_node.args.args] + [a.arg for a in fn_node.args.kwonlyargs]
    return "db_path" in args


class TestWorkersBindTheirDatabase(unittest.TestCase):
    """Any worker thread handed a db_path must re-bind it before touching shared state."""

    def test_every_db_aware_thread_target_binds_the_database(self):
        offenders = []
        checked = 0

        for module_path in SERVER_MODULES:
            with open(module_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=module_path)

            targets = _thread_target_names(tree)
            defs = _function_defs(tree)

            for name in sorted(targets):
                for fn in defs.get(name, []):
                    if not _takes_db_path(fn):
                        # Workers with no db_path legitimately run on the startup DB.
                        continue
                    checked += 1
                    if not _calls_set_active_db_path(fn):
                        offenders.append(
                            f"{os.path.basename(module_path)}:{fn.lineno} {name}() "
                            f"receives db_path but never calls set_active_db_path()"
                        )

        self.assertGreater(checked, 0, "guard found no db-aware workers -- has the pattern moved?")
        self.assertEqual(
            offenders,
            [],
            "background workers would resolve the wrong database:\n  "
            + "\n  ".join(offenders),
        )

    def test_guard_detects_a_missing_binding(self):
        """The guard must actually fail on offending code, not pass vacuously."""
        source = (
            "import threading\n"
            "def worker(folder, db_path):\n"
            "    registry[folder] = 1\n"
            "threading.Thread(target=worker, args=(f, db)).start()\n"
        )
        tree = ast.parse(source)
        targets = _thread_target_names(tree)
        self.assertIn("worker", targets)
        fn = _function_defs(tree)["worker"][0]
        self.assertTrue(_takes_db_path(fn))
        self.assertFalse(_calls_set_active_db_path(fn))

    def test_guard_accepts_a_correct_binding(self):
        source = (
            "import threading\n"
            "def worker(folder, db_path):\n"
            "    set_active_db_path(db_path)\n"
            "    registry[folder] = 1\n"
            "threading.Thread(target=worker, args=(f, db)).start()\n"
        )
        tree = ast.parse(source)
        fn = _function_defs(tree)["worker"][0]
        self.assertTrue(_calls_set_active_db_path(fn))


class TestSuggestionsCacheNamingAgreesAcrossServers(unittest.TestCase):
    """Both servers read each other's cache files; their naming must not drift."""

    def test_both_servers_scope_the_cache_per_database(self):
        for module_path in SERVER_MODULES:
            with open(module_path, encoding="utf-8") as f:
                src = f.read()
            self.assertIn(
                "gui_suggestions_cache_",
                src,
                f"{os.path.basename(module_path)} writes an unscoped suggestions cache, "
                "which lets one database overwrite another's suggestions",
            )


class CrossDatabaseReadIsolationMixin:
    """Reads through a URL prefix must never surface another database's rows."""

    SERVER_START = None   # set by subclass
    GUI_DIR = None
    TEST_PORT = None
    STARTUP_DB = None
    OTHER_DB = None
    OTHER_URL_NAME = None
    READ_ENDPOINTS = ()

    @classmethod
    def _boot(cls):
        from index import PhotoIndex

        for db in (cls.STARTUP_DB, cls.OTHER_DB):
            pi = PhotoIndex(db_path=db)
            pi.load()
            pi.close()

        cls.server_thread = threading.Thread(
            target=cls.SERVER_START,
            kwargs={
                "port": cls.TEST_PORT,
                "db_path": cls.STARTUP_DB,
                "gui_dir": os.path.join(WORKSPACE_DIR, cls.GUI_DIR),
            },
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(1.0)

    @classmethod
    def _cleanup(cls):
        for db in (cls.STARTUP_DB, cls.OTHER_DB):
            for path in (db, db.replace(".db", "_taxonomy.json")):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    def _seed(self, db, marker):
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM tag_taxonomy")
        conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"C:/{marker}.jpg",
                1.0,
                1,
                json.dumps([marker]),
                json.dumps([marker]),
                json.dumps([]),
                json.dumps({}),
            ),
        )
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, ?, ?, ?)",
            (f"C:/{marker}.jpg", "[]", b"", marker),
        )
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name) VALUES (?, NULL, ?)",
            (marker, marker),
        )
        conn.commit()
        conn.close()

    def _startup_url_name(self):
        name = os.path.splitext(os.path.basename(self.STARTUP_DB))[0]
        return name[5:] if name.startswith("test_") else name

    def _body(self, url):
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode("utf-8")

    def test_prefixed_reads_never_leak_across_databases(self):
        self._seed(self.STARTUP_DB, "StartupMarker")
        self._seed(self.OTHER_DB, "OtherMarker")

        startup_name = self._startup_url_name()
        base = f"http://127.0.0.1:{self.TEST_PORT}"

        for endpoint in self.READ_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                startup_body = self._body(f"{base}/{startup_name}{endpoint}")
                other_body = self._body(f"{base}/{self.OTHER_URL_NAME}{endpoint}")

                self.assertNotIn(
                    "OtherMarker",
                    startup_body,
                    f"{endpoint} under /{startup_name}/ leaked the other database's rows",
                )
                self.assertNotIn(
                    "StartupMarker",
                    other_body,
                    f"{endpoint} under /{self.OTHER_URL_NAME}/ leaked the startup database's rows",
                )


class TestTagPupCrossDatabaseReads(CrossDatabaseReadIsolationMixin, unittest.TestCase):
    TEST_PORT = 9966
    GUI_DIR = "gui_tagpup"
    STARTUP_DB = os.path.join(WORKSPACE_DIR, "data", "test_axis_tagpup_startup.db")
    OTHER_DB = os.path.join(WORKSPACE_DIR, "data", "test_axis_tagpup_other.db")
    OTHER_URL_NAME = "axis_tagpup_other"
    READ_ENDPOINTS = ("/api/people", "/api/tags", "/api/taxonomy/tree")

    @classmethod
    def setUpClass(cls):
        from tagpup_server import start_server

        cls.SERVER_START = staticmethod(start_server).__func__
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        from tagpup_server import set_active_db_path

        set_active_db_path(None)
        cls._cleanup()


class TestTunerCrossDatabaseReads(CrossDatabaseReadIsolationMixin, unittest.TestCase):
    TEST_PORT = 9977
    GUI_DIR = "gui"
    STARTUP_DB = os.path.join(WORKSPACE_DIR, "data", "test_axis_tuner_startup.db")
    OTHER_DB = os.path.join(WORKSPACE_DIR, "data", "test_axis_tuner_other.db")
    OTHER_URL_NAME = "axis_tuner_other"
    READ_ENDPOINTS = ("/api/people", "/api/people-with-counts")

    @classmethod
    def setUpClass(cls):
        from tuner_server import start_server

        cls.SERVER_START = staticmethod(start_server).__func__
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        from tuner_server import set_active_db_path

        set_active_db_path(None)
        cls._cleanup()


if __name__ == "__main__":
    unittest.main()

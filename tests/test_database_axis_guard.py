"""Standing guards that keep the multi-database axis honest.

Two complementary nets against the class of defect where code resolves "the current
database" implicitly and silently picks the wrong one:

  1. A structural guard (AST) asserting that the web layer and the jobs resolve no
     library implicitly: no thread-local, no "active database" set by hand. The old
     servers kept one, and every background worker had to re-bind it, since a new
     thread does not inherit the request's thread-local and fell back to the
     *startup* database. A Library is handed to whatever needs one now
     (tagpup.web.state; docs/ARCHITECTURE.md, "Context is explicit").
  2. Behavioural checks that reads through a URL database prefix never observe another
     database's rows, run against both apps.

The structural guard is deliberately mechanical: it fires on a newly added worker
before anyone has to reproduce a hung progress bar to discover the problem.
"""
import ast
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from face_rows import add_face, add_people  # noqa: E402

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402

#: Where a request's library is turned into background work: the web layer and the jobs.
GUARDED = ("web", "jobs")


def _sources():
    for folder in GUARDED:
        root = os.path.join(WORKSPACE_DIR, "tagpup", folder)
        for name in sorted(os.listdir(root)):
            if name.endswith(".py"):
                yield os.path.join("tagpup", folder, name), os.path.join(root, name)


def _implicit_library(tree):
    """Where `tree` resolves a library implicitly: a thread-local, or the old servers'
    set_active_db_path / get_active_db_path."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in ("set_active_db_path", "get_active_db_path"):
                found.append("%d: %s()" % (node.lineno, name))
            elif name == "local" and getattr(func, "value", None) is not None \
                    and getattr(func.value, "id", None) == "threading":
                found.append("%d: threading.local()" % node.lineno)
    return found


class TestNoLibraryIsResolvedImplicitly(unittest.TestCase):
    def test_the_web_layer_and_the_jobs_name_their_library(self):
        offenders = []
        checked = 0
        for relative, path in _sources():
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            checked += 1
            offenders += ["%s:%s" % (relative, where) for where in _implicit_library(tree)]
        self.assertGreater(checked, 5, "the guard found nothing to check")
        self.assertEqual(offenders, [], "a library is resolved implicitly here:\n  " + "\n  ".join(offenders))

    def test_work_handed_to_a_job_is_handed_its_library(self):
        """What TagPup hands a suggestion run touches its per-library folder cache and
        embedder; the run's thread gets the library from the closure, never from a
        request (tagpup.jobs.suggestions.work_for)."""
        import inspect

        from tagpup.jobs import suggestions as suggestion_jobs
        from tagpup.web import tagpup_routes

        self.assertEqual(["library", "photos"], list(inspect.signature(suggestion_jobs.work_for).parameters))
        self.assertEqual(["library", "folder"], list(inspect.signature(tagpup_routes._folder_photos).parameters))
        self.assertEqual(["library"], list(inspect.signature(tagpup_routes._folder_indexer).parameters))

    def test_guard_detects_an_implicit_library(self):
        """The guard must actually fail on offending code, not pass vacuously."""
        source = (
            "import threading\n"
            "_thread_local = threading.local()\n"
            "def worker(folder, db_path):\n"
            "    set_active_db_path(db_path)\n"
            "    registry[folder] = 1\n"
        )
        found = _implicit_library(ast.parse(source))
        self.assertEqual(["2: threading.local()", "4: set_active_db_path()"], found)

    def test_guard_accepts_explicit_context(self):
        source = (
            "def index(library, folder):\n"
            "    folders.of(library).pop(folder)\n"
        )
        self.assertEqual([], _implicit_library(ast.parse(source)))


class TestSuggestionsCacheIsScopedPerDatabase(unittest.TestCase):
    """Migration 7 is the only reader of the old cache file (tests/test_suggestions_pipeline.py
    checks it is the only owner); it must still read one file per database."""

    def test_the_migration_scopes_the_cache_per_database(self):
        import shutil
        import tempfile

        from tagpup.store import db, schema

        folder = tempfile.mkdtemp(prefix="sugg_file_")
        self.addCleanup(shutil.rmtree, folder, True)
        files = set()
        for name in ("photo_index.db", "kr-track.db", "a.db"):
            conn = db.connect(os.path.join(folder, name))
            try:
                files.add(schema._suggestions_file(conn))
            finally:
                conn.close()
        self.assertEqual(len(files), 3, "one database's suggestions file is another's")


class CrossDatabaseReadIsolationMixin:
    """Reads through a URL prefix must never surface another database's rows."""

    KIND = None   # set by subclass
    STARTUP_NAME = None   # file names, in a home of the test's own
    OTHER_NAME = None
    OTHER_URL_NAME = None
    READ_ENDPOINTS = ()

    def setUp(self):
        self.app, home = web_client.app_for(self, self.KIND, startup=self.STARTUP_NAME)
        self.client = self.app.test_client()
        self.STARTUP_DB = home.library(self.STARTUP_NAME)
        self.OTHER_DB = home.library(self.OTHER_NAME)
        library_actions.create(self.OTHER_DB)

    def _seed(self, db, marker):
        conn = tagpup_db.connect(db)
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM tag_taxonomy")
        conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"C:/{marker}.jpg",
                1.0,
                1,
                json.dumps([marker]),
                json.dumps([]),
                json.dumps({}),
            ),
        )
        add_people(conn, f"C:/{marker}.jpg", [marker])
        add_face(conn, f"C:/{marker}.jpg", box="[]", embedding=b"", name=marker)
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
        reply = self.client.get(url)
        self.assertEqual(200, reply.status_code, url)
        return reply.get_data(as_text=True)

    def test_prefixed_reads_never_leak_across_databases(self):
        self._seed(self.STARTUP_DB, "StartupMarker")
        self._seed(self.OTHER_DB, "OtherMarker")

        startup_name = self._startup_url_name()

        for endpoint in self.READ_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                startup_body = self._body(f"/{startup_name}{endpoint}")
                other_body = self._body(f"/{self.OTHER_URL_NAME}{endpoint}")

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
    KIND = "tagpup"
    STARTUP_NAME = "test_axis_tagpup_startup.db"
    OTHER_NAME = "test_axis_tagpup_other.db"
    OTHER_URL_NAME = "axis_tagpup_other"
    READ_ENDPOINTS = ("/api/people", "/api/tags", "/api/taxonomy/tree")


class TestTunerCrossDatabaseReads(CrossDatabaseReadIsolationMixin, unittest.TestCase):
    KIND = "tuner"
    STARTUP_NAME = "test_axis_tuner_startup.db"
    OTHER_NAME = "test_axis_tuner_other.db"
    OTHER_URL_NAME = "axis_tuner_other"
    READ_ENDPOINTS = ("/api/people", "/api/people-with-counts")


if __name__ == "__main__":
    unittest.main()

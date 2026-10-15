"""tagpup.web: what every app does alike, through Flask's test client.

The library a URL names, the picker both pages share, the page's own files, the
refusal of a request from anywhere but this machine, one process answering on two
ports, and the requests log. Each was a copy in each of the two old servers.

Lifted: test_a_mistyped_library_is_not_created and test_multiple_databases (the library a URL names, made by Create,
read through its prefix; the folder-index flow stayed with test_database_context_isolation). Mutation: dropping
web/libraries.py's `os.path.exists` check in resolve() fails the 404 tests below.
"""
import gc
import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from werkzeug.test import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from face_rows import add_face, add_people  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.logs import REQUESTS  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store.taxonomy import TagTaxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import state  # noqa: E402


def which_library(app):
    """A route of the test's own, so a test can ask any app which library a request was served from."""
    @app.get("/api/which-library")
    def which():
        return {"path": state.require().path}
    return app.test_client()


class TheLibraryAUrlNames(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()

    def test_the_first_part_of_the_url_names_the_library(self):
        reply = self.client.get("/library/api/databases")
        self.assertEqual(200, reply.status_code)
        self.assertIn("library", reply.get_json()["databases"])

    def test_a_name_that_is_no_library_is_404_in_both_apps_and_makes_none(self):
        """A typo in the address bar once made an empty library, which then sat in the library list."""
        name = "no_such_library_" + uuid.uuid4().hex[:8]
        for kind in ("tagpup", "tuner"):
            app, home = web_client.app_for(self, kind)
            reply = app.test_client().get("/%s/api/tags" % name)
            self.assertEqual(404, reply.status_code, kind)
            self.assertIn(b"There is no library called " + name.encode(), reply.data)
            self.assertFalse(os.path.exists(home.library(name + ".db")), "a library was created by a typo")

    def test_asking_who_the_people_roots_are_creates_nothing(self):
        created = self.home.library("no_such_library_" + uuid.uuid4().hex[:8] + ".db")
        self.assertIn("people", TagTaxonomy(db_path=created).people_roots())
        self.assertFalse(os.path.exists(created))

    def test_the_library_served_is_the_one_named_and_the_startup_file_wherever_it_lives(self):
        client = which_library(self.app)
        for name in ("library", "regatta"):
            open(self.home.library(name + ".db"), "ab").close()
            reply = client.get("/%s/api/which-library" % name)
            self.assertEqual(Library(self.home.library(name + ".db")), Library(reply.get_json()["path"]), name)
        # A server started on a library outside data/ serves that file, not an empty namesake made in data/.
        elsewhere = os.path.join(self.home.root, "elsewhere", "harbour.db")
        os.makedirs(os.path.dirname(elsewhere))
        open(elsewhere, "wb").close()
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=Library(elsewhere))
            app.testing = True
            client = which_library(app)
            for url in ("/api/which-library", "/harbour/api/which-library"):
                reply = client.get(url)
                self.assertEqual(Library(elsewhere), Library(reply.get_json()["path"]), "%s %s" % (kind, url))
        self.assertFalse(os.path.exists(self.home.library("harbour.db")), "an empty namesake was made in data/")

    def test_a_created_library_has_its_schema_and_is_routed_by_its_prefix_apart_from_the_others(self):
        # A server started on a test library serves and makes test libraries (test_x.db for x).
        app, home = web_client.app_for(self, "tuner", startup="test_multiple_db_startup.db")
        client = app.test_client()
        code_folder = tempfile.mkdtemp(prefix="tagpup_code_folder_")
        self.addCleanup(own_home.remove, code_folder)
        with mock.patch.object(tagpup_config, "CODE_ROOT", code_folder):
            self.assertIn("multiple_db_startup", client.get("/api/databases").get_json()["databases"])
            for name in ("created_db_1", "created_db_2"):
                reply = client.post("/api/databases/create", json={"db_name": name})
                self.assertEqual((200, name), (reply.status_code, reply.get_json()["db_name"]), reply.data)
                self.assertIn(name, client.get("/api/databases").get_json()["databases"])
            self.assertEqual(404, client.post("/api/databases/select", json={"db_name": "x"}).status_code)
            self.assertEqual([], os.listdir(code_folder), "creating a library wrote beside the code")
            self.assertEqual(["data"], os.listdir(home.root), "creating a library wrote a file in the home")
        paths = {name: os.path.join(home.data, "test_%s.db" % name) for name in ("created_db_1", "created_db_2")}
        conn = tagpup_db.connect(tagpup_db.readonly_uri(paths["created_db_1"]), uri=True)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertLessEqual({"photos", "faces", "tag_taxonomy"}, tables)
        # One person in each; each prefix answers with its own library's.
        for number, (name, path) in enumerate(paths.items(), 1):
            person = ("Alice", "Bob")[number - 1]
            conn = tagpup_db.connect(path)
            conn.execute("INSERT INTO photos (path, mtime, size) VALUES (?, ?, ?)", ("C:/photo.jpg", 1.0, 100))
            add_people(conn, "C:/photo.jpg", [person])
            add_face(conn, "C:/photo.jpg", box="[]", embedding=b"", name=person)
            conn.commit()
            conn.close()
        self.assertEqual(["Alice"], client.get("/created_db_1/api/people").get_json())
        self.assertEqual(["Bob"], client.get("/created_db_2/api/people").get_json())

    def test_a_created_library_is_not_left_open(self):
        """Creating a library opened it for its schema and never closed it: on Windows it could not be moved or
        deleted until a garbage-collection pass. Collection is off here, so the old behaviour fails every time."""
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            self.assertEqual(200, self.client.post("/library/api/databases/create", json={"db_name": "second"}).status_code)
            try:
                os.remove(self.home.library("second.db"))
            except PermissionError:
                self.fail("the new library is still held open by the server")
        finally:
            if was_enabled:
                gc.enable()

    def test_a_request_naming_none_goes_to_the_startup_library(self):
        self.assertEqual(200, self.client.get("/api/databases").status_code)

    def test_a_bare_page_request_is_sent_to_the_startup_librarys_page(self):
        reply = self.client.get("/?mode=faces")
        self.assertEqual(302, reply.status_code)
        self.assertEqual("/library/?mode=faces", reply.headers["Location"])

    def test_a_library_is_made_by_create_and_never_by_a_typo(self):
        reply = self.client.post("/library/api/databases/create", json={"db_name": "second"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertTrue(os.path.exists(self.home.library("second.db")))
        self.assertEqual(200, self.client.get("/second/api/databases").status_code)

    def test_a_pages_name_is_refused_as_a_library(self):
        reply = self.client.post("/library/api/databases/create", json={"db_name": "api"})
        self.assertEqual(400, reply.status_code)
        # The shape both pages read: TagTuner's reads data.success on every reply.
        self.assertEqual(False, reply.get_json()["success"])
        self.assertIn("error", reply.get_json())

    def test_a_json_reply_is_never_kept_by_the_browser(self):
        reply = self.client.get("/library/api/databases")
        self.assertIn("no-store", reply.headers["Cache-Control"])


class ThePage(unittest.TestCase):
    def test_each_app_serves_its_own_page_uncached(self):
        for kind, folder in (("tagpup", os.path.join("web", "tagpup")), ("tuner", os.path.join("web", "tuner"))):
            app, _home = web_client.app_for(self, kind)
            reply = app.test_client().get("/library/")
            self.assertEqual(200, reply.status_code, kind)
            with open(os.path.join(tagpup_config.CODE_ROOT, folder, "index.html"), "rb") as handle:
                self.assertEqual(handle.read(), reply.data, kind)
            self.assertIn("no-store", reply.headers["Cache-Control"])
            self.assertEqual(200, app.test_client().get("/library/main.js").status_code)
            for name in ("api", "library", "paths", "vocabulary"):
                self.assertEqual(200, app.test_client().get("/library/common/%s.js" % name).status_code, name)
            self.assertEqual(200, app.test_client().get("/library/style.css").status_code)


class ThePagesModules(unittest.TestCase):
    """A page is ES modules: its own beside index.html, and the shared ones in
    web/common/, which every page imports as common/<name>.js relative to itself."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="tagpup_code_")
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.page = os.path.join(self.root, "web", "tagpup")
        os.makedirs(self.page)
        os.makedirs(os.path.join(self.root, "web", "common"))
        for folder, name, text in ((self.page, "index.html", "<html></html>"),
                                   (self.page, "main.js", "import './common/api.js';"),
                                   (os.path.join(self.root, "web", "common"), "api.js", "export const api = {};"),
                                   (self.root, "secret.js", "not a page's")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        self.app, _home = web_client.app_for(self, "tagpup", pages=self.page)
        self.client = self.app.test_client()

    def test_a_pages_module_is_served_as_a_script_uncached(self):
        for url, text in (("/library/main.js", b"import './common/api.js';"),
                          ("/library/common/api.js", b"export const api = {};")):
            reply = self.client.get(url)
            self.assertEqual(200, reply.status_code, url)
            self.assertEqual(text, reply.data, url)
            self.assertEqual("application/javascript", reply.mimetype, url)
            self.assertIn("no-store", reply.headers["Cache-Control"], url)

    def test_a_module_that_is_not_there_is_not_found(self):
        self.assertEqual(404, self.client.get("/library/missing.js").status_code)
        self.assertEqual(404, self.client.get("/library/common/main.js").status_code)

    def test_nothing_outside_the_folders_is_reached(self):
        for url in ("/library/..%2Fsecret.js", "/library/common/..%2F..%2Fsecret.js",
                    "/library/main.backup.js", "/library/common/%2E%2E.js"):
            self.assertEqual(404, self.client.get(url).status_code, url)

    def test_a_page_opened_without_a_library_still_finds_the_shared_modules(self):
        # At / the page imports /common/api.js: `common` names no library (tagpup.core.library.ROUTES).
        app, _home = web_client.app_for(self, "tagpup", startup=None, pages=self.page)
        reply = app.test_client().get("/common/api.js")
        self.assertEqual(200, reply.status_code)
        self.assertEqual(b"export const api = {};", reply.data)


class OnlyThisMachine(unittest.TestCase):
    def setUp(self):
        self.app, _home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()

    def test_another_host_is_refused(self):
        reply = self.client.get("/library/api/databases", headers={"Host": "photos.example.com"})
        self.assertEqual(403, reply.status_code)

    def test_a_page_from_elsewhere_is_refused(self):
        reply = self.client.get("/library/api/databases", headers={"Origin": "http://evil.example"})
        self.assertEqual(403, reply.status_code)

    def test_this_machine_is_answered_under_each_of_its_names(self):
        for host in ("localhost:8080", "127.0.0.1", "[::1]:8080"):
            reply = self.client.get("/library/api/databases", headers={"Host": host, "Origin": "http://" + host})
            self.assertEqual(200, reply.status_code, host)


class OneProcessTwoPorts(unittest.TestCase):
    def test_each_port_reaches_its_own_app(self):
        tagpup, _home = web_client.app_for(self, "tagpup")
        tuner, _home2 = web_client.app_for(self, "tuner")
        both = Client(web.by_port({8090: tagpup, 8080: tuner}))
        for port, folder in ((8090, os.path.join("web", "tagpup")), (8080, os.path.join("web", "tuner"))):
            reply = both.get("/library/", environ_overrides={"SERVER_PORT": str(port)})
            self.assertEqual(200, reply.status_code, port)
            with open(os.path.join(tagpup_config.CODE_ROOT, folder, "index.html"), "rb") as handle:
                self.assertEqual(handle.read(), reply.data, port)
        self.assertEqual(404, both.get("/library/", environ_overrides={"SERVER_PORT": "9999"}).status_code)


class TheRequestsLog(unittest.TestCase):
    def setUp(self):
        self.app, _home = web_client.app_for(self, "tagpup")

    def test_a_slow_request_is_logged_with_its_time(self):
        original = web.SLOW_REQUEST_SECONDS
        web.SLOW_REQUEST_SECONDS = 0.0
        self.addCleanup(setattr, web, "SLOW_REQUEST_SECONDS", original)
        with self.assertLogs(REQUESTS, level="WARNING") as logged:
            self.app.test_client().get("/library/api/databases")
        self.assertRegex(logged.output[0], r"slow: GET /library/api/databases took \d")

    def test_a_request_that_fails_is_logged_with_its_traceback(self):
        @self.app.get("/api/boom")
        def boom():
            raise RuntimeError("the disk fell over")

        self.app.testing = False   # else the test client raises instead of answering 500
        with self.assertLogs(REQUESTS, level="ERROR") as logged:
            reply = self.app.test_client().get("/library/api/boom")
        self.assertEqual(500, reply.status_code)
        self.assertIn("the disk fell over", "\n".join(logged.output))


if __name__ == "__main__":
    unittest.main()

"""tagpup.web: what every app does alike, through Flask's test client.

The library a URL names, the picker both pages share, the page's own files, the
refusal of a request from anywhere but this machine, one process answering on two
ports, and the requests log. Each was a copy in each of the two old servers.
"""
import os
import sys
import unittest

from werkzeug.test import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.logs import REQUESTS  # noqa: E402
from tagpup.web import app as web  # noqa: E402


class TheLibraryAUrlNames(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()

    def test_the_first_part_of_the_url_names_the_library(self):
        reply = self.client.get("/library/api/databases")
        self.assertEqual(200, reply.status_code)
        self.assertIn("library", reply.get_json()["databases"])

    def test_a_name_that_is_no_library_is_404(self):
        reply = self.client.get("/nope/api/databases")
        self.assertEqual(404, reply.status_code)
        self.assertIn(b"There is no library called nope", reply.data)

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
        self.assertEqual(404, self.client.get("/third/api/databases").status_code)
        self.assertFalse(os.path.exists(self.home.library("third.db")))

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
        for kind, folder in (("tagpup", "gui_tagpup"), ("tuner", "gui")):
            app, _home = web_client.app_for(self, kind)
            reply = app.test_client().get("/library/")
            self.assertEqual(200, reply.status_code, kind)
            with open(os.path.join(tagpup_config.CODE_ROOT, folder, "index.html"), "rb") as handle:
                self.assertEqual(handle.read(), reply.data, kind)
            self.assertIn("no-store", reply.headers["Cache-Control"])
            self.assertEqual(200, app.test_client().get("/library/app.js").status_code)
            self.assertEqual(200, app.test_client().get("/library/style.css").status_code)


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
        for port, folder in ((8090, "gui_tagpup"), (8080, "gui")):
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

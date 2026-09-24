"""A library named in the URL that does not exist is refused, not created.

Both apps take the library from the first part of the path -- /kr-track/api/tags --
and pointed the request at data/<name>.db whether or not it existed. The first
handler to open it created it: a typo in the address bar made an empty library,
which then sat in the library list. Making one is what Create is for.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import tagpup_server  # noqa: E402
import tuner_server  # noqa: E402
from taxonomy import TagTaxonomy  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402


def fake(handler_cls, url):
    class Fake(handler_cls):
        def __init__(self):  # noqa: D107 -- no socket, on purpose
            self.path = url
            self.headers = {"Host": "localhost"}
            self.wfile = io.BytesIO()
            self.sent = []

        def send_response(self, code, message=None):
            self.sent.append(code)

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

        def send_error(self, code, message=None, explain=None):
            self.sent.append(code)

        def send_json_error(self, code, message):
            self.sent.append(code)

    return Fake()


class AMistypedLibraryIsNotCreated(unittest.TestCase):
    def setUp(self):
        self.name = "no_such_library_" + uuid.uuid4().hex[:8]
        self.home = own_home.for_test(self, "tagpup_mistyped_")
        self.created = self.home.library(self.name + ".db")

    def tearDown(self):
        tagpup_server.set_active_db_path(None)
        tuner_server.set_active_db_path(None)

    def check(self, handler_cls):
        handler = fake(handler_cls, "/%s/api/tags" % self.name)
        routed = handler.resolve_db_from_url()
        self.assertFalse(routed, "the request went ahead against a library that does not exist")
        self.assertEqual([404], handler.sent)
        self.assertFalse(os.path.exists(self.created), "a library was created by a typo")

    def test_tagpup_refuses_it(self):
        self.check(tagpup_server.TagPupHTTPRequestHandler)

    def test_tagtuner_refuses_it(self):
        self.check(tuner_server.TunerHTTPRequestHandler)

    def test_asking_who_the_people_roots_are_creates_nothing(self):
        roots = TagTaxonomy(db_path=self.created).people_roots()
        self.assertIn("people", roots)
        self.assertFalse(os.path.exists(self.created))

    def test_a_library_that_exists_is_routed_to(self):
        # It relied on the checkout's data/ holding the startup library, and skipped
        # when it did not. Here the startup library and another both exist in the home.
        started_on = mock.patch.object(tagpup_server.TagPupHTTPRequestHandler, "db_path",
                                       self.home.library("harbour.db"))
        started_on.start()
        self.addCleanup(started_on.stop)
        for name in ("harbour", "regatta"):
            open(self.home.library(name + ".db"), "wb").close()
            handler = fake(tagpup_server.TagPupHTTPRequestHandler, "/%s/api/tags" % name)
            self.assertTrue(handler.resolve_db_from_url(), name)
            self.assertEqual("/api/tags", handler.path)
            self.assertEqual(Library(handler.db_path), Library(self.home.library(name + ".db")))


class TheStartupLibraryIsTheFileTheServerStartedOn(unittest.TestCase):
    """A request with no library in its URL, or naming the startup library, went to
    data/<its name>.db rather than to the file the server was started on.

    They are the same file when the library is in data/, as the launchers arrange. A
    server started on a library anywhere else served -- and created -- an empty namesake
    in data/ instead. Found when tests moved their libraries out of the checkout.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_routing_")
        self.addCleanup(shutil.rmtree, self.home, True)
        environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        environ.start()
        self.addCleanup(environ.stop)
        self.library = os.path.join(self.home, "elsewhere", "harbour.db")
        os.makedirs(os.path.dirname(self.library))
        open(self.library, "wb").close()
        for module, handler_cls in ((tagpup_server, tagpup_server.TagPupHTTPRequestHandler),
                                    (tuner_server, tuner_server.TunerHTTPRequestHandler)):
            started_on = mock.patch.object(handler_cls, "db_path", self.library)
            started_on.start()
            self.addCleanup(started_on.stop)
            self.addCleanup(module.set_active_db_path, None)

    def check(self, url):
        for handler_cls in (tagpup_server.TagPupHTTPRequestHandler,
                            tuner_server.TunerHTTPRequestHandler):
            handler = fake(handler_cls, url)
            self.assertTrue(handler.resolve_db_from_url(), handler_cls.__name__)
            self.assertEqual(Library(handler.db_path), Library(self.library),
                             "%s served %s" % (handler_cls.__name__, handler.db_path))
        self.assertFalse(os.path.exists(os.path.join(self.home, "data", "harbour.db")),
                         "an empty namesake was made in data/")

    def test_with_no_library_in_the_url(self):
        self.check("/api/tags")

    def test_naming_the_startup_library(self):
        self.check("/harbour/api/tags")


if __name__ == "__main__":
    unittest.main()

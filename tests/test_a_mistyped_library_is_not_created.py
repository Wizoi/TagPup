"""A library named in the URL that does not exist is refused, not created.

Both apps take the library from the first part of the path -- /kr-track/api/tags --
and pointed the request at data/<name>.db whether or not it existed. The first
handler to open it created it: a typo in the address bar made an empty library,
which then sat in the library list. Making one is what Create is for.
"""
import io
import os
import sys
import unittest
import uuid

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import tagpup_server  # noqa: E402
import tuner_server  # noqa: E402
from taxonomy import TagTaxonomy  # noqa: E402


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
        self.data_dir = os.path.join(WORKSPACE_DIR, "data")
        self.created = os.path.join(self.data_dir, self.name + ".db")

    def tearDown(self):
        tagpup_server.set_active_db_path(None)
        tuner_server.set_active_db_path(None)
        if os.path.exists(self.created):
            os.remove(self.created)

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
        # The startup library always exists by the time requests arrive.
        startup = os.path.basename(tagpup_server.TagPupHTTPRequestHandler.db_path)
        existing = os.path.join(self.data_dir, startup)
        if not os.path.exists(existing):
            self.skipTest("no startup library in data/")
        handler = fake(tagpup_server.TagPupHTTPRequestHandler,
                       "/%s/api/tags" % os.path.splitext(startup)[0])
        self.assertTrue(handler.resolve_db_from_url())
        self.assertEqual("/api/tags", handler.path)


if __name__ == "__main__":
    unittest.main()

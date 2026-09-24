"""A request goes ahead when its library's migration cannot run just now.

Each request that names a library brings it to the current schema first
(tagpup.store.schema.ensure). When that cannot happen -- another process holding the
write lock past the busy timeout, or a migration that fails -- the request used to fail
with it, page and static files included, and the socket closed with no answer
(docs/findings.md, #57). The library is served as it is; the next request tries again.
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import tagpup_server  # noqa: E402
import tuner_server  # noqa: E402
from tagpup.store import schema  # noqa: E402


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

    return Fake()


class ALibraryThatCannotBeMigratedIsStillServed(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_unmigrated_")
        self.addCleanup(shutil.rmtree, self.home, True)
        environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        environ.start()
        self.addCleanup(environ.stop)
        self.library = os.path.join(self.home, "harbour.db")
        open(self.library, "wb").close()
        locked = mock.patch.object(schema, "ensure", side_effect=sqlite3.OperationalError("database is locked"))
        locked.start()
        self.addCleanup(locked.stop)

    def check(self, module, handler_cls):
        started_on = mock.patch.object(handler_cls, "db_path", self.library)
        started_on.start()
        self.addCleanup(started_on.stop)
        self.addCleanup(module.set_active_db_path, None)
        handler = fake(handler_cls, "/harbour/api/tags")
        self.assertTrue(handler.resolve_db_from_url())
        self.assertEqual(("/api/tags", []), (handler.path, handler.sent))

    def test_by_tagpup(self):
        self.check(tagpup_server, tagpup_server.TagPupHTTPRequestHandler)

    def test_by_tagtuner(self):
        self.check(tuner_server, tuner_server.TunerHTTPRequestHandler)


if __name__ == "__main__":
    unittest.main()

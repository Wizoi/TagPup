"""A request goes ahead when its library's migration cannot run just now.

Each request that names a library brings it to the current schema first
(tagpup.store.schema.ensure). When that cannot happen -- another process holding the
write lock past the busy timeout, or a migration that fails -- the request used to fail
with it, page and static files included, and the socket closed with no answer
(docs/findings.md, #57). The library is served as it is; the next request tries again.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup.store import schema  # noqa: E402


class ALibraryThatCannotBeMigratedIsStillServed(unittest.TestCase):
    def check(self, kind):
        app, home = web_client.app_for(self, kind)
        open(home.library("harbour.db"), "wb").close()
        client = app.test_client()
        with mock.patch.object(schema, "ensure", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(200, client.get("/harbour/").status_code, kind)
            self.assertEqual(200, client.get("/harbour/api/databases").status_code, kind)

    def test_by_tagpup(self):
        self.check("tagpup")

    def test_by_tagtuner(self):
        self.check("tuner")

    def test_a_route_reading_the_library_still_answers(self):
        app, home = web_client.app_for(self, "tagpup")
        client = app.test_client()
        with mock.patch.object(schema, "ensure", side_effect=sqlite3.OperationalError("database is locked")):
            reply = client.get("/library/api/tags")
        self.assertEqual(200, reply.status_code)
        self.assertIn("People", reply.get_json())


if __name__ == "__main__":
    unittest.main()

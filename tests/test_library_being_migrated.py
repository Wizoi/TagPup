"""A request for a library that is being migrated is answered 503 at once, not parked on its write lock
(docs/findings.md, Integration-and-apps review 2026-10-09, 3.1).

Every URL under a library's name resolves the library, and `schema.ensure` waited on the migration's write lock
holding one of the server's sixteen threads. With a page open and sixteen requests parked there, /api/server --
which the supervisor and a launcher probe, and end a server for missing three times -- had no thread to answer on,
and a server ended mid-migration rolls the migration back and starts it again. Now the request is told "updating"
(503, Retry-After, X-TagPup-Updating: what a drain answers, and the pages wait out) and holds no thread; /api/server
and other libraries answer as before.
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_client  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import schema  # noqa: E402

REQUESTS = 24   # more than the server's sixteen threads


class ALibraryBeingMigrated(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.held = [self.home.library(name) for name in ("harbour.db", "regatta.db")]
        for path in self.held:
            at_version(path, schema.LATEST - 1)
        schema._current.clear()
        self.addCleanup(schema._current.clear)
        self.entered, self.release = threading.Event(), threading.Event()
        last = schema.MIGRATIONS[-1]
        real = last.apply

        def hold(conn):
            self.entered.set()
            self.release.wait(60)
            real(conn)

        patcher = mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:-1] + (last._replace(apply=hold),))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.thread = library_actions.bring_up_to_date_in_background([Library(path) for path in self.held])
        self.addCleanup(self.thread.join, 60)
        self.addCleanup(self.release.set)   # runs first: the held migration is let go, then the thread is joined
        self.assertTrue(self.entered.wait(30), "the first library's migration is under way")

    def ask(self, url, results, index):
        results[index] = self.app.test_client().get(url)

    def ask_all(self, url):
        results = [None] * REQUESTS
        threads = [threading.Thread(target=self.ask, args=(url, results, i), daemon=True) for i in range(REQUESTS)]
        for each in threads:
            each.start()
        for each in threads:
            each.join(10)
        return results, [each for each in threads if each.is_alive()]

    def test_requests_for_it_are_turned_away_at_once_and_hold_no_thread(self):
        results, parked = self.ask_all("/harbour/api/tags")
        self.assertEqual([], parked, "no request waits for the migration")
        self.assertEqual({503}, {r.status_code for r in results})
        first = results[0]
        self.assertEqual("2", first.headers["Retry-After"])
        self.assertEqual("1", first.headers["X-TagPup-Updating"])
        body = json.loads(first.data)
        self.assertTrue(body["updating"] and not body["success"])

    def test_its_page_says_so_and_asks_again_by_itself(self):
        reply = self.app.test_client().get("/harbour/")
        self.assertEqual(503, reply.status_code)
        self.assertIn(b'http-equiv="refresh"', reply.data)
        self.assertIn(b"brought up to date", reply.data)

    def test_the_servers_own_status_and_the_other_libraries_answer_meanwhile(self):
        _, parked = self.ask_all("/harbour/api/tags")
        self.assertEqual([], parked)
        self.assertEqual(200, self.app.test_client().get("/api/server").status_code, "the hand-over's probe")
        self.assertEqual(200, self.app.test_client().get("/library/api/server").status_code, "a library already current")

    def test_a_library_the_startup_thread_has_not_reached_yet_is_not_migrated_by_a_request(self):
        results, parked = self.ask_all("/regatta/api/tags")
        self.assertEqual([], parked)
        self.assertEqual({503}, {r.status_code for r in results})

    def test_when_the_migration_ends_the_library_is_served(self):
        self.release.set()
        self.thread.join(60)
        self.assertFalse(library_actions.updating(self.held[0]))
        self.assertNotEqual(503, self.app.test_client().get("/harbour/api/tags").status_code)
        self.assertNotEqual(503, self.app.test_client().get("/regatta/api/tags").status_code)


if __name__ == "__main__":
    unittest.main()

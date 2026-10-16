"""The server brings its libraries up to date as it starts, not in the first request (docs/findings.md, #661).

Nothing at start called schema.ensure: the first request naming a library after an update ran its
migration -- 21 is 17.5 s cold on photo_index -- and a write from the other app waited on the busy
timeout meanwhile. Now tagpup_web starts a thread, before it serves, that brings each library it
serves up to date (the startup library, or every library in the data folder). The server answers at
once (the supervisor's hand-over asks /api/server, which names no library); a page's request for a
library being migrated is answered 503 at once (ALibraryBeingMigrated below).

A request for a library that is being migrated is not parked on its write lock (docs/findings.md, Integration-and-apps
review 2026-10-09, 3.1). Every URL under a library's name resolves the library, and `schema.ensure` waited on the
migration's write lock holding one of the server's sixteen threads: with a page open and sixteen requests parked there,
/api/server -- which a launcher probes, and ends a server for missing three times -- had no thread to answer on, and a
server ended mid-migration rolls the migration back and starts it again. Now the request is told "updating" (503,
Retry-After, X-TagPup-Updating: what a drain answers, and the pages wait out) and holds no thread; /api/server and other
libraries answer as before.

Lifted: test_library_being_migrated. Mutation: web/libraries.py's `raise Updating(...)` replaced by `return library`
(serve it) fails test_requests_for_it_are_turned_away_at_once_and_hold_no_thread and two more; `pass` parks the requests.
"""
import json
import logging
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import web_client  # noqa: E402
import migration_names  # noqa: E402

import tagpup_web  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.web import lifecycle as web_lifecycle  # noqa: E402


def version(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return schema.version(conn)
    finally:
        conn.close()


class TheServerMigratesAtStart(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.behind = [self.home.library(name) for name in ("harbour.db", "regatta.db")]
        for path in self.behind:
            schema.ensure(path)
        self.enterContext(migration_names.ahead())   # the code knows a step the libraries have not had
        self.addCleanup(schema.forget)

    def test_every_library_it_serves_is_brought_up_to_date_on_a_thread(self):
        thread = library_actions.bring_up_to_date_in_background([Library(path) for path in self.behind])
        thread.join(60)
        self.assertFalse(thread.is_alive())
        self.assertEqual([schema.LATEST] * 2, [version(path) for path in self.behind])

    def test_a_library_that_is_not_there_is_not_made(self):
        missing = os.path.join(self.home.data, "photo_index.db")
        library_actions.bring_up_to_date_in_background([Library(missing)]).join(60)
        self.assertFalse(os.path.exists(missing))

    def test_one_that_cannot_be_is_logged_and_the_rest_are_done(self):
        real = schema.ensure

        def locked_first(path):
            if path == self.behind[0]:
                raise RuntimeError("database is locked")
            return real(path)

        with mock.patch.object(schema, "ensure", side_effect=locked_first), \
                self.assertLogs("tagpup.services.libraries", level=logging.WARNING) as said:
            library_actions.bring_up_to_date_in_background([Library(path) for path in self.behind]).join(60)
        self.assertEqual([schema.LATEST - 1, schema.LATEST], [version(path) for path in self.behind])
        self.assertTrue(any("database is locked" in line for line in said.output), said.output)

    def test_main_starts_it_with_the_libraries_it_serves_before_serving(self):
        order = []
        with mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1"}), \
                mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)"), \
                mock.patch.object(library_actions, "bring_up_to_date_in_background",
                                  side_effect=lambda libraries: order.append(("bring", [lib.path for lib in libraries]))), \
                mock.patch.object(tagpup_web.web, "serve", side_effect=lambda *a, **k: order.append(("serve",))):
            self.assertEqual(0, tagpup_web.main(["--tagpup-port", "1", "--tuner-port", "2"]))
        self.assertEqual("bring", order[0][0])
        self.assertEqual(sorted(os.path.normcase(p) for p in self.behind), sorted(os.path.normcase(p) for p in order[0][1]))
        self.assertEqual(("serve",), order[1])

    def test_a_drain_while_it_runs_waits_for_it(self):
        """docs/findings.md, #664: the thread is work beside the requests. /api/server says so, and a
        drain -- the supervisor's, before a new version -- is refused while it runs, rather than ending
        the server part-way through a migration that the next start would pay for again."""
        release, entered = threading.Event(), threading.Event()
        real = schema.ensure

        def slow(path):
            entered.set()
            release.wait(30)
            return real(path)

        lifecycle = web_lifecycle.Lifecycle()
        with mock.patch.object(schema, "ensure", side_effect=slow):
            thread = library_actions.bring_up_to_date_in_background([Library(path) for path in self.behind])
            try:
                self.assertTrue(entered.wait(30))
                self.assertIn("bringing 2 library(ies) up to date", lifecycle.status()["busy"])
                answer = lifecycle.drain(seconds=0.2, lease=60)
                self.assertFalse(answer["drained"])
                self.assertIn("bringing 2 library(ies) up to date", answer["waiting_for"])
            finally:
                release.set()
                thread.join(60)
        self.assertEqual([], [w for w in lifecycle.busy() if "up to date" in w])


REQUESTS = 24   # more than the server's sixteen threads


class ALibraryBeingMigrated(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.held = [self.home.library(name) for name in ("harbour.db", "regatta.db")]
        current = self.home.library("library.db")
        for path in self.held + [current]:
            schema.ensure(path)
        self.enterContext(migration_names.ahead())   # the code knows a step the libraries have not had
        schema.ensure(current)   # "a library already current" is one this step has been run on
        self.addCleanup(schema.forget)
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

    def ask_all(self, url):
        results = [None] * REQUESTS

        def ask(index):
            results[index] = self.app.test_client().get(url)
        threads = [threading.Thread(target=ask, args=(i,), daemon=True) for i in range(REQUESTS)]
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

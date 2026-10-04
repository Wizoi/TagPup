"""The server brings its libraries up to date as it starts, not in the first request (docs/findings.md, #661).

Nothing at start called schema.ensure: the first request naming a library after an update ran its
migration -- 21 is 17.5 s cold on photo_index -- and a write from the other app waited on the busy
timeout meanwhile. Now tagpup_web starts a thread, before it serves, that brings each library it
serves up to date (the startup library, or every library in the data folder). The server answers at
once (the supervisor's hand-over asks /api/server, which names no library); a page's request for a
library being migrated waits for it on the library's write lock, then finds it current.
"""
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
from test_migrations import at_version  # noqa: E402

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
            at_version(path, schema.LATEST - 1)
        schema._current.clear()
        self.addCleanup(schema._current.clear)

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
        import threading
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
                answer = lifecycle.drain(seconds=0.2)
                self.assertFalse(answer["drained"])
                self.assertIn("bringing 2 library(ies) up to date", answer["waiting_for"])
            finally:
                release.set()
                thread.join(60)
        self.assertEqual([], [w for w in lifecycle.busy() if "up to date" in w])


if __name__ == "__main__":
    unittest.main()

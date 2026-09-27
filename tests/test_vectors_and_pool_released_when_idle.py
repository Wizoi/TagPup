"""The vector index and New Person's pool are let go after the same idle period as the
models, and made again on their next use; never while a run holds them (owner,
2026-09-26: "can the in-memory cache be minimized when not in use?"). On photo_index the
vectors are about 270 MB and the pool about 185 MB, held for as long as the process ran.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.idle import IdleCaches  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import tuner_routes  # noqa: E402

IDLE = 30 * 60


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


class TheRegistry(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.idle = IdleCaches(IDLE, clock=self.clock)
        self.released = []
        self.busy = False
        self.idle.register("grids", lambda: self.released.append("grids") or 1, in_use=lambda: self.busy)

    def test_lets_go_of_what_has_not_been_used_for_the_period(self):
        self.idle.used("grids")
        self.clock.now += IDLE - 1
        self.assertEqual([], self.idle.release_idle())
        self.clock.now += 2
        self.assertEqual(["grids"], self.idle.release_idle())
        self.assertEqual(["grids"], self.released)

    def test_never_what_is_in_use_nor_what_it_let_go_already(self):
        self.idle.used("grids")
        self.clock.now += IDLE * 2
        self.busy = True
        self.assertEqual([], self.idle.release_idle())
        self.busy = False
        self.assertEqual(["grids"], self.idle.release_idle())
        self.clock.now += IDLE * 2
        self.assertEqual([], self.idle.release_idle(), "released again with nothing made since")

    def test_without_a_period_it_keeps_everything(self):
        idle = IdleCaches(None, clock=self.clock)
        idle.register("grids", lambda: self.released.append("grids"))
        idle.used("grids")
        self.clock.now += IDLE * 100
        self.assertEqual([], idle.release_idle())


class FakeModel:
    def __init__(self, settings):
        self.settings = dict(settings)

    def unload(self):
        pass


class TheVectorIndex(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="idle_vectors_")
        path = home.library("harbour.db")
        library_actions.create(path)
        self.harbour = Library(path)
        self.clock = Clock()
        self.runtime = Runtime(build_clip=FakeModel, build_faces=FakeModel, idle_after=IDLE, clock=self.clock)
        self.addCleanup(self.runtime.forget, self.harbour)

    def test_is_let_go_after_the_idle_period_and_made_again_on_its_next_use(self):
        first = self.runtime.photo_index(self.harbour)
        self.clock.now += IDLE + 1
        self.assertIn("photo indexes", self.runtime.release_idle())
        self.assertIsNone(first.conn, "the index let go still holds its library open")
        again = self.runtime.photo_index(self.harbour)
        self.assertIsNot(first, again)
        self.assertIsNotNone(again.conn)

    def test_is_kept_while_a_run_holds_it(self):
        with mock.patch("tagpup.services.suggester.model_for_run", lambda *args: object()):
            run = self.runtime.begin(self.harbour)
        held = self.runtime.photo_index(self.harbour)
        self.clock.now += IDLE * 3
        self.assertEqual([], self.runtime.release_idle(), "let go under a running Suggest")
        self.assertIsNotNone(held.conn)
        run.end()
        self.clock.now += IDLE + 1
        self.assertEqual(sorted(["models", "photo indexes"]), sorted(self.runtime.release_idle()))
        self.assertIsNone(held.conn)


class NewPersonsPool(unittest.TestCase):
    def test_is_let_go_after_the_idle_period_and_read_again_on_its_next_use(self):
        clock = Clock()
        runtime = Runtime(build_clip=FakeModel, build_faces=FakeModel, idle_after=IDLE, clock=clock)
        app, home = web_client.app_for(self, "tuner", runtime=runtime)
        library = Library(home.library("library.db"))
        self.addCleanup(tuner_routes.identify_cache.forget, library)
        client = app.test_client()

        def unnamed_like(library, face_id, unnamed=None):
            unnamed()
            return {"matches": []}
        with mock.patch.object(tuner_routes.identify_service, "unnamed_like", side_effect=unnamed_like):
            self.assertEqual(200, client.get("/library/api/face-matches-unmatched?id=1").status_code)
            cache = tuner_routes.identify_cache.of(library)
            self.assertIn("unnamed_faces", cache.keys())
            clock.now += IDLE - 1
            self.assertNotIn("New Person pool", runtime.release_idle())
            clock.now += 2
            self.assertIn("New Person pool", runtime.release_idle())
            self.assertNotIn("unnamed_faces", cache.keys(), "the pool is still held")
            self.assertEqual(200, client.get("/library/api/face-matches-unmatched?id=1").status_code)
            # The library's Identify Faces caches may have gone with it: read what it holds now.
            self.assertIn("unnamed_faces", tuner_routes.identify_cache.of(library).keys(),
                          "the pool was not read again")


if __name__ == "__main__":
    unittest.main()

"""The pages' caches are in the one idle registry too (tagpup.core.idle, Runtime.idle):
TagPup's folder scans and TagTuner's Identify Faces caches -- the queue, the grids, the
named faces' matrix -- are let go after the idle period and made again on their next use;
never while something holds them (a Suggest run over a scanned folder, a grid being
built). Owner, 2026-09-26: "can the in-memory cache be minimized when not in use?"
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.web import tagpup_routes, tuner_routes  # noqa: E402

IDLE = 30 * 60


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


def a_jpeg(path):
    from PIL import Image
    Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")


class Base(unittest.TestCase):
    kind = "tagpup"

    def setUp(self):
        self.clock = Clock()
        self.runtime = Runtime(idle_after=IDLE, clock=self.clock)
        self.app, self.home = web_client.app_for(self, self.kind, runtime=self.runtime)
        self.library = Library(self.home.library("library.db"))
        self.client = self.app.test_client()


class FolderScans(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.folder = os.path.join(self.home.root, "Regatta")
        os.makedirs(self.folder)
        a_jpeg(os.path.join(self.folder, "Regatta - 1.jpg"))

    def scan(self):
        answer = self.client.get("/library/api/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, answer.status_code, answer.get_data(as_text=True))

    def test_are_let_go_after_the_idle_period_and_scanned_again_on_their_next_use(self):
        self.scan()
        self.assertIsNotNone(tagpup_routes.folders.held(self.library.key).get(self.folder))
        self.clock.now += IDLE - 1
        self.assertNotIn("folder scans", self.runtime.release_idle())
        self.clock.now += 2
        self.assertIn("folder scans", self.runtime.release_idle())
        self.assertIsNone(tagpup_routes.folders.held(self.library.key), "the scans are still held")
        self.scan()
        self.assertIsNotNone(tagpup_routes.folders.held(self.library.key).get(self.folder))

    def test_are_kept_while_a_suggest_run_is_under_way(self):
        self.scan()
        self.clock.now += IDLE * 2
        with mock.patch.object(suggestion_jobs, "running", return_value=1):
            self.assertNotIn("folder scans", self.runtime.release_idle())
        self.assertIsNotNone(tagpup_routes.folders.held(self.library.key))
        self.assertIn("folder scans", self.runtime.release_idle())


class IdentifyCaches(Base):
    kind = "tuner"

    def setUp(self):
        super().setUp()
        self.addCleanup(tuner_routes.identify_cache.forget, self.library)
        self.addCleanup(tuner_routes.identify_progress.forget, self.library)

    def queue(self):
        answer = self.client.get("/library/api/unmatched-faces/people")
        self.assertEqual(200, answer.status_code, answer.get_data(as_text=True))

    def test_are_let_go_after_the_idle_period_and_read_again_on_their_next_use(self):
        self.queue()
        self.assertTrue(tuner_routes.identify_cache.held(self.library.key).keys())
        self.clock.now += IDLE + 1
        self.assertIn("identify caches", self.runtime.release_idle())
        self.assertIsNone(tuner_routes.identify_cache.held(self.library.key))
        self.queue()
        self.assertTrue(tuner_routes.identify_cache.held(self.library.key).keys())

    def test_are_kept_while_a_grid_is_being_built(self):
        self.queue()
        self.clock.now += IDLE * 2
        progress = tuner_routes.identify_progress.of(self.library)
        progress.report("Rowan Thackeray", "reading", 0.5, "Reading faces...")
        self.assertNotIn("identify caches", self.runtime.release_idle())
        self.assertIsNotNone(tuner_routes.identify_cache.held(self.library.key))
        progress.done("Rowan Thackeray")
        self.assertIn("identify caches", self.runtime.release_idle())


if __name__ == "__main__":
    unittest.main()

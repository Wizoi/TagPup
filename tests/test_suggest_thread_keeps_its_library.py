"""A Suggest run started by a request runs on its own thread, on the library the request
named (#126).

The tests that proved a background run stays with the URL's library (#44) run it on the
test thread: test_database_context_isolation patches SuggestionRuns.start with a
synchronous run, so the thread a real run is on -- where a thread-local or a request
global would be missing -- was never exercised. Here the page's route starts the run
unpatched, on its thread, with two libraries and the one not started with addressed;
the model is a fake (tagpup.ml refuses real weights in a test), and the run is joined,
not slept on.
"""
import os
import sys
import threading
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

THREAD_NAME = "FolderSuggestionsThread"   # tagpup.jobs.suggestions.SuggestionRuns.start


class FakeModels:
    """A runtime (tagpup.runtime.Runtime) that records which library each run began on,
    and on which thread, and suggests one tag for every photo."""

    def __init__(self):
        self.began = []
        self.suggested_on = set()

    def begin(self, library):
        self.began.append((library, threading.current_thread().name))
        return FakeModel(self)


class FakeModel:
    model_key = "fake-model"

    def __init__(self, models):
        self.models = models

    def suggest(self, path, metadata):
        self.models.suggested_on.add(threading.current_thread().name)
        return {"path": path, "suggested_tags": [{"tag": "Activity/Kayak", "score": 0.9}]}

    def offered(self, suggestion):
        return [t["tag"] for t in suggestion["suggested_tags"]], [], None

    def consensus(self, suggestions):
        return suggestions


class SuggestRunsOnItsThreadForItsLibrary(unittest.TestCase):
    STARTUP = "harbour_startup.db"
    OTHER = "harbour_other.db"

    def setUp(self):
        self.models = FakeModels()
        self.app, self.home = web_client.app_for(self, "tagpup", startup=self.STARTUP, runtime=self.models)
        self.client = self.app.test_client()
        self.other = Library(self.home.library(self.OTHER))
        library_actions.create(self.other.path)
        self.startup = Library(self.home.library(self.STARTUP))
        for library in (self.startup, self.other):
            self.addCleanup(suggestion_jobs.forget, library)
            self.addCleanup(tagpup_routes.folders.forget, library)

        # Photos the other library has read, so the run's scan takes them from its rows
        # (tagpup.services.photos.scan_folder) and needs no ExifTool.
        self.folder = os.path.join(self.home.root, "Photos", "Harbour Walk")
        os.makedirs(self.folder)
        self.photos = [os.path.join(self.folder, name) for name in ("jetty.jpg", "quay.jpg")]
        conn = db.connect(self.other.path)
        try:
            for photo in self.photos:
                Image.new("RGB", (8, 8)).save(photo, "JPEG")
                photo_rows.add_read(conn, photo, {"XMP:Subject": []})
            conn.commit()
        finally:
            conn.close()

    def url(self, library, route):
        return "/%s/api/%s" % (os.path.splitext(os.path.basename(library.path))[0], route)

    def start_and_wait(self):
        reply = self.client.post(self.url(self.other, "folder/suggest-start"), json={"folder_path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        runs = [t for t in threading.enumerate() if t.name == THREAD_NAME]
        self.assertTrue(runs, "the run was not started on a thread of its own")
        for run in runs:
            run.join(timeout=60)
            self.assertFalse(run.is_alive(), "the run did not finish")

    def status(self, library):
        reply = self.client.get(self.url(library, "folder/suggest-status"), query_string={"path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def test_the_run_begins_on_its_thread_with_the_library_named(self):
        self.start_and_wait()
        self.assertEqual([(self.other, THREAD_NAME)], self.models.began)
        self.assertTrue(self.models.suggested_on)
        self.assertNotIn(threading.current_thread().name, self.models.suggested_on)

    def test_its_suggestions_are_kept_in_that_library_and_not_the_other(self):
        self.start_and_wait()
        found = self.status(self.other)
        self.assertEqual("completed", found["status"], found)
        self.assertEqual(sorted(paths.key(p) for p in self.photos),
                         sorted(paths.key(p) for p in found["suggestions"]))
        for entry in found["suggestions"].values():
            self.assertEqual(["Activity/Kayak"], entry["tags"])
        self.assertEqual("idle", self.status(self.startup)["status"])


if __name__ == "__main__":
    unittest.main()

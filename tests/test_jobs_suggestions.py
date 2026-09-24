"""tagpup.jobs.suggestions: starting a folder's run, and what a run leaves behind.

The model is stood in for. The file, failures and consensus are in
test_suggestions_pipeline.py, which runs TagPup's own work.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
from tagpup.jobs.suggestions import SuggestionRuns  # noqa: E402


class Model:
    def suggest(self, photo, meta):
        return {"path": photo, "suggested_tags": [{"tag": "Activity/Rowing", "score": 0.8}]}

    def offered(self, suggestion):
        return [dict(t) for t in suggestion["suggested_tags"]], [], "Rowing"

    def consensus(self, suggestions):
        return suggestions


class Work:
    def __init__(self, photos, gate=None):
        self._photos, self.gate = photos, gate

    def photos(self):
        if self.gate:
            self.gate.wait(10)
        return self._photos

    def begin(self):
        return Model()


class RunsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="suggestion_runs_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.runs = SuggestionRuns(os.path.join(self.dir, "library.db"))
        self.folder = os.path.join(self.dir, "Regatta")
        self.photos = {paths.key(os.path.join(self.folder, n)): {"path": os.path.join(self.folder, n)}
                       for n in ("a.jpg", "b.jpg")}

    def wait_for(self, status, timeout=10):
        deadline = time.time() + timeout
        while self.runs.status(self.folder).get("status") != status:
            if time.time() > deadline:
                self.fail("still %s" % self.runs.status(self.folder))
            time.sleep(0.02)


class StartingARun(RunsCase):
    def test_a_run_goes_to_completed_with_a_suggestion_per_photo(self):
        self.assertEqual(self.runs.start(self.folder, Work(self.photos)), "running")
        self.wait_for("completed")
        status = self.runs.status(self.folder)
        self.assertEqual((status["completed"], status["total"]), (2, 2))
        self.assertEqual(sorted(status["suggestions"]),
                         sorted(paths.stored(m["path"]) for m in self.photos.values()))

    def test_a_folder_already_under_way_is_left_alone(self):
        gate = threading.Event()
        self.runs.start(self.folder, Work(self.photos, gate))
        try:
            self.assertEqual(self.runs.start(self.folder, Work({})), "preparing")
        finally:
            gate.set()
        self.wait_for("completed")

    def test_a_folder_without_photos_says_so(self):
        self.runs.start(self.folder, Work({}))
        self.wait_for("error")
        self.assertEqual(self.runs.status(self.folder)["message"], "No images found in this folder.")

    def test_a_folder_nobody_asked_about_is_idle(self):
        self.assertEqual(self.runs.status(self.folder), {"status": "idle"})


class RenamedPhotos(RunsCase):
    def test_their_suggestions_follow_them_and_are_saved(self):
        self.runs.run(self.folder, Work(self.photos))
        old = paths.stored(os.path.join(self.folder, "a.jpg"))
        new = paths.stored(os.path.join(self.folder, "Regatta - 1.jpg"))
        self.assertEqual(self.runs.move_photos({old: new}), 1)
        saved = self.runs.suggestions(self.folder)
        self.assertIn(new, saved)
        self.assertNotIn(old, saved)
        self.assertEqual(saved[new]["raw_suggestions"]["path"], new)
        self.assertTrue(os.path.exists(self.runs.file))


if __name__ == "__main__":
    unittest.main()

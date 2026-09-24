"""Saved suggestions leave a run already in progress alone.

The server restored its saved suggestions in a background thread, after the models
began loading, while it was already answering requests. A folder chosen straight
after launch could therefore have a run going before the restore landed. The restore
used dict.update(), replacing that live entry with the saved copy -- whose status it
had just rewritten to "idle" -- so the page, polling, saw "idle" and stopped asking.
The server went on and finished; the page never showed the result.

Saved suggestions are rows of the library now (tagpup.store.suggestions), read on each
poll: a folder with saved rows and a run in memory reports the run.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.jobs.suggestions import SuggestionRuns  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import suggestions as saved  # noqa: E402


def save(library_path, photo):
    conn = db.connect(library_path)
    try:
        saved.put(conn, photo, {"tags": [], "people": [], "title": None,
                                "raw_suggestions": {"suggested_tags": []}, "raw_before_consensus": True})
        conn.commit()
    finally:
        conn.close()


class SavedSuggestionsDoNotCancelARun(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.running = os.path.join(self.lib.photos, "2025-11 Classic")
        self.finished = os.path.join(self.lib.photos, "2025-10 Invitational")
        # An earlier session saved a photo of each folder.
        save(self.lib.library.path, os.path.join(self.running, "start.jpg"))
        save(self.lib.library.path, os.path.join(self.finished, "finish.jpg"))
        self.runs = SuggestionRuns(self.lib.library.path)

    def test_a_run_started_before_the_poll_keeps_going(self):
        live = {"status": "preparing", "completed": 0, "total": 3}
        self.runs.statuses[paths.key(self.running)] = live
        self.assertEqual(self.runs.status(self.running)["status"], "preparing")
        self.assertIs(self.runs.statuses[paths.key(self.running)], live)
        self.assertEqual(live["status"], "preparing")

    def test_folders_nothing_touched_are_still_offered(self):
        self.runs.statuses[paths.key(self.running)] = {"status": "preparing", "completed": 0, "total": 3}
        status = self.runs.status(self.finished)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(list(status["suggestions"]), [os.path.join(self.finished, "finish.jpg")])


if __name__ == "__main__":
    unittest.main()

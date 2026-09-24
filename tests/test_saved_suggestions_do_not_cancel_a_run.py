"""Restoring saved suggestions at startup leaves a run already in progress alone.

The server restores its saved suggestions in a background thread, after the models
begin loading, while it is already answering requests. A folder chosen straight
after launch can therefore have a run going before the restore lands. The restore
used dict.update(), replacing that live entry with the saved copy -- whose status it
had just rewritten to "idle" -- so the page, polling, saw "idle" and stopped asking.
The server went on and finished; the page never showed the result.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
from tagpup.jobs.suggestions import SuggestionRuns, cache_file  # noqa: E402


class SavedSuggestionsDoNotCancelARun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="saved_sugg_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "library.db")
        self.running = paths.key(os.path.join(self.dir, "Meets", "2025-11 Classic"))
        self.finished = paths.key(os.path.join(self.dir, "Meets", "2025-10 Invitational"))
        saved = {
            # Saved mid-run by an earlier session: the restore rewrites it to "idle".
            self.running: {"status": "running", "completed": 1, "total": 3, "suggestions": {}},
            self.finished: {"status": "completed", "completed": 2, "total": 2, "suggestions": {}},
        }
        with open(cache_file(self.db), "w", encoding="utf-8") as handle:
            json.dump(saved, handle)
        self.runs = SuggestionRuns(self.db)

    def test_a_run_started_before_the_restore_keeps_going(self):
        live = {"status": "preparing", "completed": 0, "total": 3, "suggestions": {}}
        self.runs.statuses[self.running] = live
        self.runs.ensure_loaded()
        self.assertIs(self.runs.statuses[self.running], live)
        self.assertEqual(self.runs.statuses[self.running]["status"], "preparing")

    def test_folders_nothing_touched_are_still_restored(self):
        self.runs.ensure_loaded()
        self.assertEqual(self.runs.statuses[self.finished]["status"], "completed")
        self.assertEqual(self.runs.statuses[self.running]["status"], "idle")


if __name__ == "__main__":
    unittest.main()

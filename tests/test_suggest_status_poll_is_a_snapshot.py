"""The page's progress poll during Suggest reads a copy, not the dict being written.

The poll handed the live status dict to the JSON encoder while four worker threads
added suggestions to it, which fails with "dictionary changed size during iteration"
and turns a poll into an error. It now copies under the lock the workers write under
(tagpup.jobs.suggestions.SuggestionRuns.status).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
from tagpup.jobs.suggestions import SuggestionRuns  # noqa: E402

FOLDER = r"D:\Pictures\Regatta"


class RecordingLock:
    def __init__(self):
        self.held = False

    def __enter__(self):
        self.held = True

    def __exit__(self, *exc):
        self.held = False


class SuggestStatusPollIsASnapshot(unittest.TestCase):
    def test_the_reply_is_a_copy_taken_under_the_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            runs = SuggestionRuns(os.path.join(folder, "regatta.db"))
            lock = runs.lock = RecordingLock()
            live = {"status": "running", "suggestions": {"a.jpg": {"tags": []}}}
            runs.statuses[paths.key(FOLDER)] = live
            copied = []
            real_get = dict.get

            class Watched(dict):
                def get(self, key, default=None):
                    copied.append(lock.held)
                    return real_get(self, key, default)

            runs.statuses = Watched(runs.statuses)
            reply = runs.status(FOLDER)

        self.assertEqual([True], copied, "the run was read outside the lock")
        self.assertEqual(live, reply)
        self.assertIsNot(live, reply, "the live dict went to the encoder")
        self.assertIsNot(live["suggestions"], reply["suggestions"])


if __name__ == "__main__":
    unittest.main()

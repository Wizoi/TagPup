"""The page's progress poll during Suggest reads a copy, not the dict being written.

The poll handed the live status dict to the JSON encoder while four worker threads
added suggestions to it, which fails with "dictionary changed size during iteration"
and turns a poll into an error. It now copies under the lock the workers write under.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import paths  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path  # noqa: E402

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
        lock = RecordingLock()
        live = {"status": "running", "suggestions": {"a.jpg": {"tags": []}}}
        sent = {}

        handler = TagPupHTTPRequestHandler.__new__(TagPupHTTPRequestHandler)
        handler.db_path = os.path.join("data", "regatta.db")
        handler.ensure_suggestions_loaded = lambda db_path: None

        def send_json(data):
            sent["data"] = data
            sent["locked_when_copied"] = lock.held

        handler.send_json = send_json
        set_active_db_path(handler.db_path)
        saved_lock = TagPupHTTPRequestHandler.model_lock
        TagPupHTTPRequestHandler.model_lock = lock
        TagPupHTTPRequestHandler.suggest_status[paths.key(FOLDER)] = live
        try:
            handler.handle_get_folder_suggest_status({"path": [FOLDER]})
        finally:
            TagPupHTTPRequestHandler.suggest_status.pop(paths.key(FOLDER), None)
            TagPupHTTPRequestHandler.model_lock = saved_lock
            set_active_db_path(None)

        self.assertEqual(live, sent["data"])
        self.assertIsNot(live, sent["data"], "the live dict went to the encoder")
        self.assertIsNot(live["suggestions"], sent["data"]["suggestions"])


if __name__ == "__main__":
    unittest.main()

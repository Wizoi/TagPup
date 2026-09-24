"""Every library's saved suggestions come back, not only the startup library's.

The saved file was read once, at startup, for the library the server was started on.
Switching to another library offered none of its saved runs, and the first save there
-- which writes whatever is in memory -- replaced its file with only this session's
folders. A library that had been worked through folder by folder lost all of it.

Saved suggestions are now rows of each library (tagpup.store.suggestions); these keep
the promise: each library offers its own, a run there adds to them rather than
replacing them, and a live run is not reported as the saved one.
"""
import os
import unittest

from tests.handler_harness import Library

from tagpup_server import paths, set_active_db_path

SAVED_FOLDER = r"D:\Library\2019\Harbour"
NEW_FOLDER = r"D:\Library\2020\Meadow"
BOAT = os.path.join(SAVED_FOLDER, "boat.jpg")
FIELD = os.path.join(NEW_FOLDER, "field.jpg")


def offered(tag):
    return {"tags": [{"tag": tag, "score": 0.8}], "people": [], "title": None,
            "raw_suggestions": {"suggested_tags": [{"tag": tag, "score": 0.8}]},
            "raw_before_consensus": True}


class Model:
    def suggest(self, photo, meta):
        return {"path": photo, "suggested_tags": [{"tag": "Meadow", "score": 0.8}]}

    def offered(self, suggestion):
        return [dict(t) for t in suggestion["suggested_tags"]], [], None

    def consensus(self, suggestions):
        return suggestions


class Work:
    def __init__(self, photo):
        self.photo = photo

    def photos(self):
        return {paths.key(self.photo): {"path": self.photo}}

    def begin(self):
        return Model()


class TestASecondLibrary(unittest.TestCase):
    def setUp(self):
        self.startup = Library(self, "photo_index")
        self.other = Library(self, "second-library")
        self.other.save_suggestions({BOAT: offered("Harbour")})

    def test_its_saved_run_is_offered(self):
        handler = self.other.handler(None)
        status, reply = handler.call("handle_get_folder_suggest_status", None,
                                     {"path": [SAVED_FOLDER]})
        self.assertEqual(status, 200)
        self.assertEqual(reply.get("status"), "completed")
        self.assertIn(BOAT, reply.get("suggestions", {}))

    def test_a_run_there_keeps_the_folders_saved_before(self):
        # A run in this session, on another folder, saves as it goes.
        set_active_db_path(self.other.db_path)
        runs = self.other.suggestion_runs()
        runs.run(NEW_FOLDER, Work(FIELD))
        self.assertEqual(runs.status(NEW_FOLDER)["status"], "completed")

        self.assertEqual(set(runs.suggestions(NEW_FOLDER)), {FIELD})
        self.assertEqual(runs.suggestions(SAVED_FOLDER), {BOAT: offered("Harbour")})

    def test_a_run_in_progress_is_not_replaced_by_the_saved_copy(self):
        # A folder something is working on reports that run, not the saved one's
        # "completed".
        set_active_db_path(self.other.db_path)
        live = {"status": "running", "completed": 0, "total": 5}
        self.other.suggestion_runs().statuses[paths.key(SAVED_FOLDER)] = live

        handler = self.other.handler(None)
        status, reply = handler.call("handle_get_folder_suggest_status", None,
                                     {"path": [SAVED_FOLDER]})
        self.assertEqual(status, 200)
        self.assertEqual(reply.get("status"), "running")
        self.assertEqual(reply.get("total"), 5)
        self.assertIn(BOAT, reply.get("suggestions", {}))
        set_active_db_path(self.other.db_path)
        self.assertIs(self.other.suggestion_runs().statuses[paths.key(SAVED_FOLDER)], live)
        self.assertEqual(live, {"status": "running", "completed": 0, "total": 5})

    def test_the_startup_library_does_not_see_the_other_librarys_folders(self):
        handler = self.startup.handler(None)
        status, reply = handler.call("handle_get_folder_suggest_status", None,
                                     {"path": [SAVED_FOLDER]})
        self.assertEqual(status, 200)
        self.assertEqual(reply.get("status"), "idle")


if __name__ == "__main__":
    unittest.main()

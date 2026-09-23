"""Every library's saved suggestions come back, not only the startup library's.

The saved file was read once, at startup, for the library the server was started on.
Switching to another library offered none of its saved runs, and the first save there
-- which writes whatever is in memory -- replaced its file with only this session's
folders. A library that had been worked through folder by folder lost all of it.
"""
import json
import os
import unittest

from tests.handler_harness import Library

from tagpup_server import TagPupHTTPRequestHandler, paths, set_active_db_path

SAVED_FOLDER = r"D:\Library\2019\Harbour"
NEW_FOLDER = r"D:\Library\2020\Meadow"


def saved_run(photo):
    return {"status": "completed", "completed": 1, "total": 1,
            "suggestions": {photo: {"tags": [{"tag": "Harbour", "score": 0.8}], "people": []}}}


class TestASecondLibrary(unittest.TestCase):
    def setUp(self):
        self.startup = Library(self, "photo_index")
        self.other = Library(self, "second-library")
        # The server was started on the first library and has read its file.
        TagPupHTTPRequestHandler.load_suggestions_cache(self.startup.db_path)

        self.cache_file = TagPupHTTPRequestHandler._suggestions_cache_path(self.other.db_path)
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump({SAVED_FOLDER: saved_run(os.path.join(SAVED_FOLDER, "boat.jpg"))}, handle)

    def saved_folders(self):
        with open(self.cache_file, encoding="utf-8") as handle:
            return {paths.key(folder) for folder in json.load(handle)}

    def test_its_saved_run_is_offered(self):
        handler = self.other.handler(None)
        status, reply = handler.call("handle_get_folder_suggest_status", None,
                                     {"path": [SAVED_FOLDER]})
        self.assertEqual(status, 200)
        self.assertEqual(reply.get("status"), "completed")
        self.assertIn(os.path.join(SAVED_FOLDER, "boat.jpg"), reply.get("suggestions", {}))

    def test_a_save_there_keeps_the_folders_saved_before(self):
        # A run in this session, on another folder, saves as it goes.
        set_active_db_path(self.other.db_path)
        TagPupHTTPRequestHandler.suggest_status[paths.key(NEW_FOLDER)] = saved_run(
            os.path.join(NEW_FOLDER, "field.jpg"))
        self.assertTrue(TagPupHTTPRequestHandler.save_suggestions_cache(self.other.db_path))

        self.assertEqual(self.saved_folders(), {paths.key(SAVED_FOLDER), paths.key(NEW_FOLDER)})
        set_active_db_path(self.other.db_path)
        self.assertIn(paths.key(SAVED_FOLDER), TagPupHTTPRequestHandler.suggest_status)

    def test_a_run_in_progress_is_not_replaced_by_the_saved_copy(self):
        # The rule the startup load already kept: a folder something is working on
        # is never overwritten by what was saved about it.
        set_active_db_path(self.other.db_path)
        live = {"status": "running", "completed": 0, "total": 5, "suggestions": {}}
        TagPupHTTPRequestHandler.suggest_status[paths.key(SAVED_FOLDER)] = live
        TagPupHTTPRequestHandler.save_suggestions_cache(self.other.db_path)

        set_active_db_path(self.other.db_path)
        self.assertIs(TagPupHTTPRequestHandler.suggest_status[paths.key(SAVED_FOLDER)], live)

    def test_the_startup_library_does_not_see_the_other_librarys_folders(self):
        handler = self.startup.handler(None)
        status, reply = handler.call("handle_get_folder_suggest_status", None,
                                     {"path": [SAVED_FOLDER]})
        self.assertEqual(status, 200)
        self.assertEqual(reply.get("status"), "idle")


if __name__ == "__main__":
    unittest.main()

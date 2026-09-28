"""No photo is written through a library that does not hold its folder.

Refusing Suggest there kept the rows out (test_rows_only_in_folders_added), but a
tag write, a rotation, a rename, a time shift or a delete still reached the file: with
kr-track selected on a folder photo_index holds, kr-track's tags were written into
photo_index's photos. The page's Just look holds them back; the server now refuses
each, 409, "<folder> is not in <library>. Add it to <library> first.", and writes
nothing -- one check, tagpup.services.libraries.refuse_writes, in every write service.
The CLI's `write` refuses the same way.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402
from tagpup_cli import cli  # noqa: E402


def make_photo(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 8), color="blue").save(path, "JPEG")
    return path


def stamp(path):
    stat = os.stat(path)
    return (stat.st_mtime_ns, stat.st_size)


class TheWriteRoutes(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.regatta = os.path.join(self.home.root, "Pictures", "Regatta")
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, make_photo(os.path.join(self.regatta, "regatta_01.jpg")), {})
            conn.commit()
        finally:
            conn.close()
        self.lighthouse = os.path.join(self.home.root, "Share", "Lighthouse")
        self.photo = make_photo(os.path.join(self.lighthouse, "IMG_0001.jpg"))
        self.before = stamp(self.photo)
        self.said = "%s is not in library. Add it to library first." % self.lighthouse

    def assert_refused(self, reply):
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual(self.said, reply.get_json()["error"])
        self.assertTrue(os.path.exists(self.photo), "the photo was deleted")
        self.assertEqual(self.before, stamp(self.photo), "the photo was written")
        self.assertEqual([self.lighthouse], library_actions.not_held(self.library, [self.photo]), "rows were made")

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def test_saving_one_photo(self):
        self.assert_refused(self.post("/photo/save-metadata", {"path": self.photo, "title": "Lighthouse",
                                                              "tags": ["Trips/Lighthouse"]}))

    def test_tags_on_a_selection(self):
        self.assert_refused(self.post("/photos/bulk-tags", {"paths": [self.photo], "add_tags": ["Trips/Lighthouse"],
                                                           "remove_tags": []}))

    def test_apply_all(self):
        saved = {self.photo: {"tags": [{"tag": "Trips/Lighthouse", "score": 0.9}], "people": [], "title": None}}
        with mock.patch.object(suggestion_jobs.SuggestionRuns, "suggestions", return_value=saved):
            self.assert_refused(self.post("/folder/auto-apply", {"folder_path": self.lighthouse}))

    def test_rotating(self):
        self.assert_refused(self.post("/photo/rotate", {"path": self.photo, "direction": "left"}))

    def test_smart_rename(self):
        self.assert_refused(self.post("/folder/rename-photos", {"folder_path": self.lighthouse,
                                                               "photo_paths": [self.photo], "grouping": "Lighthouse"}))
        self.assertEqual(["IMG_0001.jpg"], os.listdir(self.lighthouse))

    def test_time_shift(self):
        tagpup_routes.folders.of(self.library).put(self.lighthouse, {self.photo: {"path": self.photo, "raw_metadata": {}}})
        self.assert_refused(self.post("/folder/time-shift", {"folder_path": self.lighthouse,
                                                            "camera_model": fields.ALL_CAMERAS, "shift_minutes": 60}))

    def test_delete(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=AssertionError("sent")):
            self.assert_refused(self.post("/photo/delete", {"path": self.photo}))

    def test_a_held_folder_is_written(self):
        held = os.path.join(self.regatta, "regatta_01.jpg")
        reply = self.post("/photo/rotate", {"path": held, "direction": "left"})
        self.assertEqual(200, reply.status_code, reply.data)


class TheCliWrite(unittest.TestCase):
    def test_refuses_suggestions_for_a_folder_the_library_does_not_hold(self):
        home = own_home.for_test(self)
        db_path = home.library("harbour.db")
        library_actions.create(db_path)
        photo = make_photo(os.path.join(home.root, "Share", "Lighthouse", "IMG_0001.jpg"))
        before = stamp(photo)
        suggestions = os.path.join(home.root, "suggestions.json")
        with open(suggestions, "w", encoding="utf-8") as handle:
            handle.write('[{"path": %s, "suggested_tags": [{"tag": "Trips/Lighthouse", "score": 0.9}]}]'
                         % __import__("json").dumps(photo))
        result = CliRunner().invoke(cli, ["--db", db_path, "write", suggestions, "-Live", "--nobackup"], input="YES\n")
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("is not in harbour", " ".join(result.output.split()))
        self.assertEqual(before, stamp(photo))


if __name__ == "__main__":
    unittest.main()

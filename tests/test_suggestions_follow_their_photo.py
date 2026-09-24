"""What Suggest offered a photo is the photo's: kept in the library by its id
(tagpup.store.suggestions; docs/findings.md, #64).

They were kept in a JSON file beside the library, keyed by path, that nothing but a
rename through TagPup's save kept in step: a deleted photo, a removed folder or a photo
relinked left its entry, and a photo renamed another way lost its suggestions and was
suggested for again.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.jobs.suggestions import SuggestionRuns  # noqa: E402
from tagpup.store import db, photos  # noqa: E402


class Model:
    model_key = "ViT-T|tiny"

    def suggest(self, photo, meta):
        return {"path": photo, "suggested_tags": [{"tag": "Activity/Rowing", "score": 0.8}]}

    def offered(self, suggestion):
        return [dict(t) for t in suggestion["suggested_tags"]], [], "Rowing"

    def consensus(self, suggestions):
        return suggestions


class Work:
    def __init__(self, photo_paths):
        self._photos = {p: {"path": p} for p in photo_paths}

    def photos(self):
        return self._photos

    def begin(self):
        return Model()


class Suggestions(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.a = self.lib.photo("a.jpg")
        self.b = self.lib.photo("b.jpg")
        self.lib.add_row(self.a)
        SuggestionRuns(self.lib.library.path).run(self.lib.photos, Work([self.a, self.b]))

    def saved(self):
        """What a process started afresh finds for the folder."""
        return SuggestionRuns(self.lib.library.path).status(self.lib.photos)

    def test_a_finished_run_is_there_after_a_restart(self):
        status = self.saved()
        self.assertEqual("completed", status["status"])
        self.assertEqual({self.a, self.b}, set(status["suggestions"]))
        self.assertEqual("Rowing", status["suggestions"][self.a]["title"])

    def test_a_photo_suggest_saw_that_was_never_indexed_gets_a_row(self):
        self.assertEqual([(1,)], self.lib.rows("SELECT COUNT(*) FROM photos WHERE path = ?", (self.b,)))

    def test_deleting_a_photo_takes_its_suggestions(self):
        photos.forget_photo(self.lib.library.path, self.a)
        self.assertEqual({self.b}, set(self.saved()["suggestions"]))

    def test_removing_the_folder_takes_them(self):
        conn = db.connect(self.lib.library.path)
        try:
            photos.remove_under(conn, self.lib.photos)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual({"status": "idle"}, self.saved())

    def test_a_rename_keeps_them(self):
        renamed = os.path.join(self.lib.photos, "renamed.jpg")
        photos.move_rows(self.lib.library.path, {self.a: renamed})
        self.assertEqual({renamed, self.b}, set(self.saved()["suggestions"]))

    def test_a_photo_in_a_folder_below_is_the_folders_too(self):
        # A folder's scan walks the folders below it, and a run suggests for what it
        # found; read as the folder's own photos only, those were never found (#91).
        below = os.path.join(self.lib.photos, "Heats")
        os.makedirs(below)
        other = os.path.join(below, "c.jpg")
        with open(other, "wb") as f:
            f.write(b"photo")
        SuggestionRuns(self.lib.library.path).run(self.lib.photos, Work([self.a, self.b, other]))
        self.assertIn(other, self.saved()["suggestions"])


if __name__ == "__main__":
    unittest.main()

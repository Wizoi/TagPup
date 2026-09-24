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
from unittest import mock

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


class CountingModel(Model):
    def __init__(self):
        self.asked = []

    def suggest(self, photo, meta):
        self.asked.append(photo)
        return super().suggest(photo, meta)


class CountingWork(Work):
    def __init__(self, photo_paths, model):
        super().__init__(photo_paths)
        self.model = model

    def begin(self):
        return self.model


class TheRunsTheirSelves(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.a = self.lib.photo("a.jpg")
        self.b = self.lib.photo("b.jpg")
        self.runs = SuggestionRuns(self.lib.library.path)
        self.runs.run(self.lib.photos, Work([self.a, self.b]))

    @unittest.skipIf(os.path.normcase("A") == "A", "paths differ by case only where the filesystem ignores it")
    def test_a_folder_spelled_another_way_is_not_suggested_for_again(self):
        # The rows matched the scan's spelling exactly: a folder typed in another case
        # was suggested for again, and the page found none of its suggestions (#92).
        spelled = [p.upper() for p in (self.a, self.b)]
        model = CountingModel()
        runs = SuggestionRuns(self.lib.library.path)
        runs.run(self.lib.photos.upper(), CountingWork(spelled, model))
        self.assertEqual([], model.asked)
        self.assertEqual(set(spelled), set(runs.status(self.lib.photos.upper())["suggestions"]))

    def test_a_poll_never_pairs_a_finished_run_with_rows_read_before_it_finished(self):
        # The rows were read first: a run finishing meanwhile was reported "completed"
        # with the rows from before its consensus (#93).
        from tagpup.core import paths
        from tagpup.services import suggestions as saved

        key = paths.key(self.lib.photos)
        self.runs.statuses[key] = {"status": "running", "completed": 1, "total": 2}
        real = saved.saved_in

        def finishing_while_read(db_path, folder):
            self.runs.statuses[key]["status"] = "completed"
            return real(db_path, folder)

        with mock.patch.object(saved, "saved_in", finishing_while_read):
            self.assertEqual("running", self.runs.status(self.lib.photos)["status"])

    def test_a_suggestion_that_cannot_be_kept_does_not_end_the_run(self):
        # One write locked past its retries ended the run in "error" (#95).
        from tagpup.services import suggestions as saved

        c = self.lib.photo("c.jpg")
        d = self.lib.photo("d.jpg")
        real = saved.keep

        def locked_for_one(db_path, photo, found, model_key=None):
            if photo == c:
                raise RuntimeError("database is locked")
            return real(db_path, photo, found, model_key)

        with mock.patch.object(saved, "keep", locked_for_one):
            self.runs.run(self.lib.photos, Work([self.a, self.b, c, d]))
        status = self.runs.status(self.lib.photos)
        self.assertEqual("completed", status["status"])
        self.assertIn(d, status["suggestions"])
        self.assertNotIn(c, status["suggestions"])


if __name__ == "__main__":
    unittest.main()

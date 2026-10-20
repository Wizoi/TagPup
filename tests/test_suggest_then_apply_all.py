"""V3 and V9: Suggest in a folder the library holds, then Apply All -- and what becomes of the suggestions and the photos'
CLIP vectors when the photos are renamed, deleted or their folder removed.

A person adds a folder, asks for suggestions, closes the tab or restarts the server, comes back and finds them, files
them with Apply All (tags written to the files as the panel listed them), renames some photos, undoes that, deletes one,
and removes the folder. Suggestions and vectors are the photo's own, kept by its row (tagpup.store.suggestions,
tagpup.store.embeddings), so each of those either carries them with the photo or takes them away. The models are
test_analyse_only_suggest's stand-ins; the app, the routes, ExifTool, the journal and the library are real. Names are
fictional.

Lifted (every assertion kept, run in this world): test_auto_apply_writes_what_it_offers (the offered-only list; its 400s
stay in test_tagpup_routes), test_saved_suggestions_do_not_cancel_a_run, test_suggestions_follow_their_photo (findings
#64, #91, #92, #93, #95), test_suggestions_pipeline (a failed photo is retried; "completed" only after consensus;
consensus over the whole folder; an empty folder asks for no model), test_rename_moves_suggestions_and_embeddings.
Mutation, each made in a scratch copy of the code and failing the test named:
- Apply All also writing the raw list, or leaving the people out: the restart-and-Apply-All test.
- status() preferring the saved rows to the run in memory (the old test_saved_suggestions_do_not_cancel_a_run), or reading
  the rows before the run's state (#93): the straight-after-a-restart test.
- The suggestions or vectors with no cascade on delete (schema trigger and foreign key), or a rename that deletes them:
  the renamed-deleted-removed test (#64).
- Suggestions read for the folder's own photos only (#91): five tests. The run's done-list looked up by the raw path, or
  the rows handed back in their own spelling (#92): the spelled-another-way and the second-run tests.
- A suggestion that cannot be kept raising instead of being skipped (#95): the cannot-be-kept test.
- A failed photo stored without its error; "completed" set before the consensus; the consensus taken over this run's
  photos only; the model asked for before the folder is known to hold a photo: the second-run, the restart and the
  empty-folder tests.
Not kept: Apply All's `threshold`, deleted (the page always sent 0.0: no one narrowed); and the pipeline test that a score is not boosted twice, which cannot happen
now that a run reads its raw list from the library's rows each time.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import test_analyse_only_suggest as base  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import faces as faces_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import suggester  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

Base = base.Base
EXTRA = {"tag": "People/Emory Kade", "score": 0.31, "has_face_match": True}   # in the raw list only, never offered
FIVE = ["cup_01.jpg", "cup_02.jpg", "cup_03.jpg", "cup_04.jpg", "cup_05.jpg"]


class ASuggestRunInAHeldFolder(Base):
    """Harbour Cup is added, with a photo in a folder below it and one cut short; Suggest has run in it."""

    def setUp(self):
        super().setUp()
        self.heats = os.path.join(self.cup, "Heats", "cup_05.jpg")
        damaged_photos.whole_jpeg(self.heats, seed=9)
        library_actions.record_added(self.library, [self.cup])         # the page asks first
        self.assertEqual({"success": True, "status": "running", "in_memory": False}, self.start(self.cup))
        self.offered = self.suggested(self.cup)

    def kept(self, table):
        """The photos of the cup holding a row of `table` (suggestions, embeddings), by name."""
        found = self.rows("SELECT p.path FROM %s t JOIN photos p ON p.id = t.photo_id" % table)
        return sorted(os.path.basename(p) for (p,) in found if paths.is_under(paths.key(p), paths.key(self.cup)))

    def restart(self):
        suggestion_jobs.forget(self.library)

    def orphans(self, table):
        return self.rows("SELECT COUNT(*) FROM %s WHERE photo_id NOT IN (SELECT id FROM photos)" % table)[0][0]

    def repair_the_cut_photo(self):
        """The cut photo is whole now; from here on the photos the suggester is asked about are noted."""
        damaged_photos.whole_jpeg(self.broken, seed=4)
        self.asked, real = [], suggester.TagSuggester.suggest_for_photo

        def spy(this, path, *args, **kwargs):
            self.asked.append(os.path.basename(path))
            return real(this, path, *args, **kwargs)
        patcher = mock.patch.object(suggester.TagSuggester, "suggest_for_photo", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_run_is_there_after_a_restart_and_apply_all_files_what_the_panel_showed(self):
        self.assertEqual(FIVE, sorted(self.offered), "a photo of a folder below is the folder's too (#91)")
        self.assertIn("error", self.offered["cup_04.jpg"])
        self.assertEqual(FIVE, self.kept("suggestions"))
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_03.jpg", "cup_05.jpg"], self.kept("embeddings"))
        self.restart()
        self.assertEqual(self.offered, self.suggested(self.cup))
        self.assertEqual("completed", self.status(self.cup)["status"])
        # Kept in the raw list and never offered (the panel's lists are made once): Apply All must not write it.
        runs = suggestion_jobs.runs_for(self.library)
        entry = runs.suggestions(self.cup)[self.loose[0]]
        entry["raw_suggestions"]["suggested_tags"].append(EXTRA)
        suggester.keep(self.library.path, self.loose[0], entry)
        shown = [t["tag"] for t in entry["tags"]] + [p["name"] for p in entry["people"]]
        self.assertTrue(shown)

        reply = self.post("/folder/auto-apply", {"folder_path": self.cup})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(shown, base.tags_in(self.loose[0]))
        self.assertNotIn(EXTRA["tag"], base.tags_in(self.loose[0]))
        self.assertEqual([], base.tags_in(self.broken), "nothing was suggested for the cut photo")

    def test_a_second_run_tries_only_the_cut_photo_and_says_completed_after_the_consensus(self):
        self.repair_the_cut_photo()
        self.restart()
        seen, real = [], suggester.TagSuggester.apply_folder_consensus

        def consensus(this, suggestions):
            seen.append((suggestion_jobs.runs_for(self.library).statuses[paths.key(self.cup)]["status"],
                         sorted(os.path.basename(s["path"]) for s in suggestions)))
            return real(this, suggestions)

        with mock.patch.object(suggester.TagSuggester, "apply_folder_consensus", consensus):
            self.start(self.cup)
        self.assertEqual(["cup_04.jpg"], self.asked, "only the failed photo")
        self.assertEqual([("running", FIVE)], seen, "consensus is over the whole folder, before 'completed'")
        self.assertNotIn("error", self.suggested(self.cup)["cup_04.jpg"])
        self.assertEqual("completed", self.status(self.cup)["status"])

    @unittest.skipIf(os.path.normcase("A") == "A", "paths differ by case only where the filesystem ignores it")
    def test_a_folder_spelled_another_way_is_not_suggested_for_again(self):
        self.repair_the_cut_photo()
        tagpup_routes.folders.forget(self.library)          # the scan is made again, in the spelling typed
        self.start(self.cup.upper())
        self.assertEqual(["cup_04.jpg"], self.asked, "(#92)")
        found = self.status(self.cup.upper())["suggestions"]
        self.assertEqual(FIVE, sorted(os.path.basename(p) for p in found))
        self.assertTrue(all(p.startswith(self.cup.upper()) for p in found), "the page's spelling")

    def test_a_folder_chosen_straight_after_a_restart_reports_its_run_not_its_saved_rows(self):
        self.start(self.regatta)
        self.restart()
        real = suggester.saved_in
        with mock.patch.object(suggestion_jobs, "threading",
                               types.SimpleNamespace(Lock=base.threading.Lock, Thread=base.Deferred)):
            self.assertEqual("running", self.start(self.cup)["status"])
            runs = suggestion_jobs.runs_for(self.library)
            live = self.status(self.cup)
            self.assertIn(live["status"], ("preparing", "running"))
            self.assertEqual(FIVE, sorted(os.path.basename(p) for p in live["suggestions"]), "the saved rows beside it")
            self.assertEqual("completed", self.status(self.regatta)["status"], "a folder nothing touched is offered")

            def finishing_while_read(db_path, folder):
                runs.statuses[paths.key(self.cup)]["status"] = "completed"
                return real(db_path, folder)

            runs.statuses[paths.key(self.cup)]["status"] = "running"
            with mock.patch.object(suggester, "saved_in", finishing_while_read):
                self.assertEqual("running", self.status(self.cup)["status"], "rows from before the finish (#93)")
            base.Deferred.waiting[0].run()
        self.assertEqual("completed", self.status(self.cup)["status"])

    def test_a_suggestion_that_cannot_be_kept_does_not_end_the_run(self):
        late, later = (os.path.join(self.cup, "cup_0%d.jpg" % n) for n in (6, 7))
        damaged_photos.whole_jpeg(late, seed=6)
        damaged_photos.whole_jpeg(later, seed=7)
        real = suggester.keep

        def locked_for_one(db_path, photo, found, model_key=None):
            if photo == late:
                raise RuntimeError("database is locked")
            return real(db_path, photo, found, model_key)

        with mock.patch.object(suggester, "keep", locked_for_one):
            self.start(self.cup)
        found = self.suggested(self.cup)
        self.assertEqual(("completed", True, False), (self.status(self.cup)["status"], "cup_07.jpg" in found,
                                                      "cup_06.jpg" in found), "(#95)")

    def test_an_empty_folder_asks_for_no_model(self):
        empty = os.path.join(self.home.root, "Share", "Empty")
        os.makedirs(empty)
        library_actions.record_added(self.library, [empty])
        with mock.patch.object(self.runtime, "begin") as begin:
            self.start(empty)
        begin.assert_not_called()
        self.assertEqual("No images found in this folder.", self.status(empty)["message"])

    def test_photos_renamed_deleted_or_removed_take_their_suggestions_and_vectors_with_them(self):
        wanted = [self.loose[0], self.loose[1]]
        renamed = self.post("/folder/rename-photos", {"folder_path": self.cup, "photo_paths": wanted,
                                                      "grouping": "Regatta"})
        self.assertEqual(200, renamed.status_code, renamed.data)
        new = sorted(os.path.basename(p) for p in renamed.get_json()["updated_paths"].values())
        after = sorted(new + ["cup_03.jpg", "cup_04.jpg", "cup_05.jpg"])
        self.assertEqual(after, self.kept("suggestions"))
        self.assertEqual(sorted(new + ["cup_03.jpg", "cup_05.jpg"]), self.kept("embeddings"))
        self.restart()
        self.assertEqual(after, sorted(self.suggested(self.cup)))
        for photo, entry in self.status(self.cup)["suggestions"].items():
            if "error" not in entry:
                self.assertEqual(photo, entry["raw_suggestions"]["path"], "the entry names the file as it is now (#90)")

        change = self.client.get("/library/api/history").get_json()["changes"][0]["id"]
        undone = self.post("/history/%d/undo" % change, {"apply": True})
        self.assertEqual(200, undone.status_code, undone.data)
        self.assertEqual(FIVE, self.kept("suggestions"))
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_03.jpg", "cup_05.jpg"], self.kept("embeddings"))

        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            deleted = self.post("/photo/delete", {"path": self.loose[2]})
        self.assertEqual(200, deleted.status_code, deleted.data)
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_04.jpg", "cup_05.jpg"], self.kept("suggestions"))
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_05.jpg"], self.kept("embeddings"))
        self.assertEqual((0, 0), (self.orphans("suggestions"), self.orphans("embeddings")), "left by the delete")

        self.assertTrue(faces_service.remove_folder(self.library, self.cup).ok)
        self.assertEqual(([], []), (self.kept("suggestions"), self.kept("embeddings")))
        self.assertEqual((0, 0), (self.orphans("suggestions"), self.orphans("embeddings")), "left by the removal")
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM embeddings")[0][0], "the library's own vectors stay")
        self.restart()
        self.assertEqual({"status": "idle"}, self.status(self.cup))


if __name__ == "__main__":
    unittest.main()

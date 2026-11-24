"""V3 and V9: Suggest in a folder the library holds, then Apply All -- and what becomes of the suggestions and the photos'
CLIP vectors when the photos are renamed, deleted or their folder removed. A folder's clips are Suggest's subjects too
(DECISIONS 2026-11-13): its tags, and the people its frames show (nothing of a face kept), Apply All patching each
clip's XMP in place.

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
Clips (AHeldFolderOfPhotosAndClips), on clips the machine's ffmpeg makes and its frames read, skipped without it: each
fails on the code before it (the route's photos-only filter: no clip is a subject). Who a clip's frames show, scenario by
scenario: tests/test_a_clips_people_are_the_people_its_frames_show.py.
Not kept: Apply All's `threshold`, deleted (the page always sent 0.0: no one narrowed); and the pipeline test that a score is not boosted twice, which cannot happen
now that a run reads its raw list from the library's rows each time.
"""
import os
import shutil
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402
import damaged_photos  # noqa: E402
import numpy as np  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import test_analyse_only_suggest as base  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values, images, shares  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import faces as faces_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import suggester  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

Base = base.Base
EXTRA = {"tag": "People/Emory Kade", "score": 0.31, "has_face_match": True}   # in the raw list only, never offered
FIVE = ["cup_01.jpg", "cup_02.jpg", "cup_03.jpg", "cup_04.jpg", "cup_05.jpg"]
TOOLS = config.locate_ffmpeg()


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


class FramesClip(base.FakeClip):
    """The stand-in CLIP shown a clip as the real one is (search.PhotoEmbeddings.prepared): each frame the machine's ffmpeg
    reads of it is a picture, made a vector near the photos of one set."""

    def picture_input(self, picture, load=True):
        self.images.append(picture)
        return picture

    def embed_inputs(self, pictures):
        return [base.vector_of("frame_%02d" % n) for n in range(len(pictures))]


class FramesFaces(base.FakeFaces):
    """The stand-in face model shown a clip's frames as the real one is (ScrfdAdaFace.picture_of_frame): each frame a
    picture labelled "<clip>, frame N", showing the face the clip's name gives it, as a photo's name gives its own."""

    def detect_and_embed(self, picture):
        return self.detect_and_embed_faces(picture.path.split(",")[0])


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine")
class AHeldFolderOfPhotosAndClips(Base):
    """Pictures/Dock is the library's: a photo and two clips the index has read, one clip with its vector and frames kept
    as the index keeps them, the other's not made yet (its frames are read when Suggest asks). Suggest offers each clip
    its tags and the people its frames show (dock_02's show Rowan, dock_03's Wren, whom no one has named); Apply All
    patches each clip's XMP in place, journaled, beside the photo's write, and Undo takes it off again."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.made = os.path.join(own_home.for_class(cls, "tagpup_suggest_clips_").root, "made.mp4")
        corpus.clip(cls.made, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1",
                               "-pix_fmt", "yuv420p", "-movflags", "+faststart"], TOOLS.ffmpeg)

    def app_home(self):
        super().app_home()
        self.clip = FramesClip(self.clip.settings)
        self.faces = FramesFaces()

    def setUp(self):
        super().setUp()
        self.dock = os.path.join(self.home.root, "Pictures", "Dock")
        self.photo = os.path.join(self.dock, "dock_01.jpg")
        self.clips = [os.path.join(self.dock, name) for name in ("dock_02.mp4", "dock_03.mp4")]
        damaged_photos.whole_jpeg(self.photo, seed=11)
        for path in self.clips:
            shutil.copyfile(self.made, path)
        library_actions.record_added(self.library, [self.dock])
        vector = np.array(base.vector_of("frame_00"), dtype=np.float32).tobytes()
        conn = db.connect(self.library.path)
        try:
            self.ids = {os.path.basename(path): photo_rows.add_read(conn, path, {})
                        for path in [self.photo] + self.clips}
            stamp = shares.read_stamp(self.clips[0])
            store_embeddings.put(conn, self.clips[0], store_embeddings.model_key(**self.clip.settings), stamp[0],
                                 stamp[1], vector, None, frames=[vector] * 4)
            conn.commit()
        finally:
            conn.close()
        self.before = base.library_dump.dump(self.library.path)
        self.clip_ids = tuple(self.ids[os.path.basename(path)] for path in self.clips)

    def only_patched(self):
        """ExifTool's whole-file write refuses a clip: a clip's tags are patched into its XMP (field_values)."""
        real = field_values._command

        def command(et, source, target, params, clear):
            if images.is_clip(source):
                raise AssertionError("ExifTool asked to write the clip %s" % os.path.basename(source))
            return real(et, source, target, params, clear)
        patcher = mock.patch.object(field_values, "_command", command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def change_files(self, change):
        return self.rows("SELECT photo_id, state FROM change_files WHERE change_id = ? AND photo_id IN (?, ?)"
                         " ORDER BY photo_id", (change,) + self.clip_ids)

    def test_each_clip_is_offered_tags_alone_and_apply_all_then_undo_write_its_xmp(self):
        self.assertEqual({"success": True, "status": "running", "in_memory": False}, self.start(self.dock))
        offered = self.suggested(self.dock)
        self.assertEqual(["dock_01.jpg", "dock_02.mp4", "dock_03.mp4"], sorted(offered), "the clips are subjects")
        for name in ("dock_02.mp4", "dock_03.mp4"):
            self.assertNotIn("error", offered[name], offered[name])
            self.assertIn("Trips/Regatta", [t["tag"] for t in offered[name]["tags"]], "its neighbours' tags")
            self.assertTrue(offered[name]["title"], "the title wand stays offered")
        self.assertEqual([("People/Rowan Thackeray", {"seen": 5, "of": 5})],
                         [(p["name"], p["frames"]) for p in offered["dock_02.mp4"]["people"]], "who its frames show")
        self.assertEqual([], offered["dock_03.mp4"]["people"], "Wren, whom no one has named, is offered as no one")
        self.assertTrue(offered["dock_01.jpg"]["people"], "the photo beside them is offered its face's person")
        self.assertEqual(["dock_01.jpg"] + ["dock_02.mp4"] * 5 + ["dock_03.mp4"] * 5,
                         [os.path.basename(path) for path in self.faces.detected], "the face model sees each frame")
        self.assertEqual([None] * 2, [error for (error,) in self.rows(
            "SELECT error FROM suggestions WHERE photo_id IN (?, ?) ORDER BY photo_id", self.clip_ids)],
            "the clips' rows, by id")
        self.assertEqual((0, 0), (
            self.rows("SELECT COUNT(*) FROM faces WHERE photo_id IN (?, ?)", self.clip_ids)[0][0],
            self.rows("SELECT COUNT(*) FROM faces_detected WHERE photo_id IN (?, ?)", self.clip_ids)[0][0]),
            "nothing of a face is recorded of a clip")
        self.assertEqual([(self.clip_ids[1], "video")], self.rows(
            "SELECT p.id, p.kind FROM photos p JOIN embeddings e ON e.photo_id = p.id WHERE p.id = ?",
            (self.clip_ids[1],)), "the vector made of its frames is kept, at its row")

        self.only_patched()
        reply = self.post("/folder/auto-apply", {"folder_path": self.dock})
        self.assertEqual(200, reply.status_code, reply.data)
        for path in self.clips:
            entry = offered[os.path.basename(path)]
            self.assertEqual(sorted([t["tag"] for t in entry["tags"]] + [p["name"] for p in entry["people"]]),
                             sorted(base.tags_in(path)))
        self.assertIn("People/Rowan Thackeray", base.tags_in(self.clips[0]), "the person its frames show, written")
        self.assertIn("People/Rowan Thackeray", base.tags_in(self.photo))
        change = self.client.get("/library/api/history").get_json()["changes"][0]["id"]
        self.assertEqual([(clip, "done") for clip in self.clip_ids], self.change_files(change), "journaled")

        undone = self.post("/history/%d/undo" % change, {"apply": True})
        self.assertEqual(200, undone.status_code, undone.data)
        self.assertEqual([[], [], []], [base.tags_in(path) for path in [self.photo] + self.clips])
        self.assertEqual([(clip, "undone") for clip in self.clip_ids], self.change_files(change))

    def test_a_clip_copied_in_before_the_index_read_it_is_given_a_clips_row(self):
        """A clip the index has not read yet (copied in since, or the folder added and not indexed) gets its row from
        Suggest's vector: a clip's row, so its thumbnail is its poster frame and the face pass does not owe it a look
        before the index reads it."""
        copied = os.path.join(self.dock, "dock_04.mp4")
        shutil.copyfile(self.made, copied)
        self.start(self.dock)
        entry = self.suggested(self.dock)["dock_04.mp4"]
        self.assertNotIn("error", entry, entry)
        self.assertIn("Trips/Regatta", [t["tag"] for t in entry["tags"]])
        kinds = [kind for path, kind in self.rows("SELECT path, kind FROM photos")
                 if os.path.basename(path) == "dock_04.mp4"]
        self.assertEqual(["video"], kinds, "the row Suggest made for the clip")

    def test_a_clip_with_no_vector_and_ffmpeg_gone_stops_the_run_with_the_install_sentence_and_the_next_run_goes_on(self):
        with mock.patch.object(config, "locate_ffmpeg", return_value=config.Ffmpeg(None, None, config.FFMPEG_MISSING)):
            self.start(self.dock)
        found = self.status(self.dock)
        self.assertEqual("error", found["status"])
        self.assertIn(config.FFMPEG_MISSING, found["message"])
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM suggestions WHERE photo_id = ?", (self.clip_ids[1],))[0][0],
                         "the clip with no vector is not marked, unread or done")
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM embeddings WHERE photo_id = ?", (self.clip_ids[1],))[0][0])
        suggestion_jobs.forget(self.library)
        self.start(self.dock)
        self.assertNotIn("error", self.suggested(self.dock)["dock_03.mp4"])
        self.assertNotIn("error", self.suggested(self.dock)["dock_02.mp4"])
        self.assertEqual("completed", self.status(self.dock)["status"])

    def test_a_clip_only_looked_at_is_offered_tags_keeps_nothing_and_apply_all_writes_the_photos_beside_it(self):
        looked = os.path.join(self.cup, "cup_09.mp4")
        shutil.copyfile(self.made, looked)
        with open(looked, "rb") as handle:
            before = handle.read()
        self.assertEqual({"success": True, "status": "running", "in_memory": True}, self.start(self.cup))
        entry = self.suggested(self.cup)["cup_09.mp4"]
        self.assertNotIn("error", entry, entry)
        self.assertEqual([], entry["people"], "its frames show no one")
        self.assertIn("Trips/Regatta", [t["tag"] for t in entry["tags"]])
        self.assertEqual(5, self.faces.detected.count(looked), "each of its frames was looked at")
        self.assert_library_unchanged()          # no vector, no row, no suggestion kept for it

        self.only_patched()
        reply = self.post("/folder/auto-apply", {"folder_path": self.cup})
        self.assertIn(images.CLIP_NOT_EDITED, reply.get_json().get("error", ""), "the clip's refusal is said")
        self.assertTrue(base.tags_in(self.loose[0]), "the photos beside it are written")
        with open(looked, "rb") as handle:
            self.assertEqual(before, handle.read(), "the clip a write of files alone cannot journal is left as it was")
        self.assert_library_unchanged()


class AFolderOfClipsWithFfmpegGone(Base):
    """ffmpeg is a prerequisite checked at the start; when it is gone while the server runs, Suggest on a folder of clips
    is a major error told to the owner with the install sentence (DECISIONS 2026-11-13), never a clip marked unread, a
    note, or tags changed: the run stops, no clip has a suggestion row, nothing is kept, in the library or only looked at."""

    def app_home(self):
        super().app_home()
        self.clip = FramesClip(self.clip.settings)

    def setUp(self):
        super().setUp()
        self.wharf = os.path.join(self.home.root, "Pictures", "Wharf")
        self.clips = [os.path.join(self.wharf, name) for name in ("wharf_01.mp4", "wharf_02.mp4")]
        os.makedirs(self.wharf)
        for path in self.clips:
            with open(path, "wb") as handle:
                handle.write(b"not read: ffmpeg is gone before any frame is asked of it")
        library_actions.record_added(self.library, [self.wharf])
        conn = db.connect(self.library.path)
        try:
            for path in self.clips:
                photo_rows.add_read(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        self.before = base.library_dump.dump(self.library.path)
        gone = mock.patch.object(config, "locate_ffmpeg", return_value=config.Ffmpeg(None, None, config.FFMPEG_MISSING))
        gone.start()
        self.addCleanup(gone.stop)

    def assert_only_the_words_changed(self):
        """The run's own candidate words were embedded and kept (a run does); no other table of the library moved."""
        after = base.library_dump.dump(self.library.path)
        for table in self.before:
            if table != "tag_embeddings":
                self.assertEqual(self.before[table], after[table], "the library's %s changed" % table)

    def test_suggest_answers_the_install_sentence_and_changes_no_clip(self):
        self.assertEqual({"success": True, "status": "running", "in_memory": False}, self.start(self.wharf))
        found = self.status(self.wharf)
        self.assertEqual("error", found["status"])
        self.assertIn(config.FFMPEG_MISSING, found["message"])
        self.assertEqual([], [n for n in found.get("notes", []) if "ffmpeg" in n.lower()], "not a quiet note")
        self.assert_only_the_words_changed()   # no suggestion row, no vector, no tag: no clip marked done or unread

    def test_a_clip_only_looked_at_answers_it_too_and_keeps_nothing(self):
        looked = os.path.join(self.cup, "cup_09.mp4")
        with open(looked, "wb") as handle:
            handle.write(b"not read")
        self.assertEqual({"success": True, "status": "running", "in_memory": True}, self.start(self.cup))
        found = self.status(self.cup)
        self.assertEqual("error", found["status"])
        self.assertIn(config.FFMPEG_MISSING, found["message"])
        self.assert_only_the_words_changed()


if __name__ == "__main__":
    unittest.main()

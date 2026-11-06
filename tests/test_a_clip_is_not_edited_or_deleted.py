"""A clip in the library is never written or deleted by TagPup yet (F12): a save, a turn, a tag edit of a selection, Add
to all selected, a Smart Rename and a delete that name a clip leave its file byte for byte as it was, journal nothing for
it, and say so in a plain sentence (tagpup.files.images.CLIP_NOT_EDITED, CLIP_NOT_DELETED); the photos named beside it
are written or deleted as always. With writing clips on (field_values.CLIP_PATCH_IN_PLACE; DECISIONS 2026-11-05, M7) a
tag or caption save is patched into its XMP (tests/test_a_clip_tagged_through_the_journal.py); a turn, a Save carrying
a Date Taken and a Smart Rename stay refused, here.

What the owner lives through: the library view lists clips beside photos (V1), so a selection of a day holds both, and a
click on a clip's card opens the photo panel. ExifTool's write of a clip is a rewrite of the whole file -- gigabytes, over
a share -- and the date the view shows is read from the clip's own QuickTime dates, which a Date Taken write does not
change (DECISIONS 2026-11-02, items 1, 3 and 7).

The real ExifTool and a real one-second clip made by the machine's ffmpeg (as tests/test_a_clip_is_indexed_as_a_video.py
makes them); rows as the indexer records a read (tests/view_library.py, which gives a .mov its kind 'video' as the indexer
does). Flask's test client in a home of its own. Skipped, saying so, without ffmpeg or ExifTool. Fictional names only.
"""
import hashlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.files import field_values, images, recycle_bin  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TOOLS = config.locate_ffmpeg()
TAKEN = "2024:07:04 10:00:00"


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AClipBesideAPhoto(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        patcher = mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast")
        self.folder = os.path.join(self.vl.pictures, "Harbour")
        self.photo_path = os.path.join(self.folder, "pier.jpg")
        make_jpeg(self.photo_path, size=(32, 32))
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo_path], tags={"EXIF:DateTimeOriginal": TAKEN}, params=["-overwrite_original"])
        self.photo = self.vl.photo("Harbour", "pier.jpg", taken=TAKEN, at=self.photo_path)
        self.clip_path = os.path.join(self.folder, "jetty.mov")
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2021-07-04T18:30:00Z",
                              self.clip_path], capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)
        self.clip = self.vl.photo("Harbour", "jetty.mov", at=self.clip_path)
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip),
                         "the clip's row is of kind video, as the indexer records it")
        self.clip_bytes = digest(self.clip_path)

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def assert_clip_untouched(self):
        self.assertTrue(os.path.exists(self.clip_path), "the clip is where it was")
        self.assertEqual(self.clip_bytes, digest(self.clip_path), "the clip's file is byte for byte as it was")
        self.assertEqual([], self.vl.rows("SELECT change_id FROM change_files WHERE photo_id = ? OR path = ?",
                                          self.clip, paths.stored(self.clip_path)),
                         "nothing is journaled for the clip")
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip), "its row stays")

    def subjects(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            held = et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject", [])
        return held if isinstance(held, list) else [held]

    def assert_refused(self, route, body):
        reply = self.post(route, body)
        self.assertNotEqual(200, reply.status_code)
        self.assertIn(images.CLIP_NOT_EDITED, reply.get_json()["error"])
        self.assert_clip_untouched()

    def test_a_tag_save_from_the_photo_panel_is_refused_with_the_sentence_while_writing_clips_is_off(self):
        self.assert_refused("photo/save-metadata", {"path": self.clip_path, "tags": ["Trips/Coast"]})

    def test_a_turn_is_refused_with_the_sentence_and_the_clip_is_not_written(self):
        self.assert_refused("photo/rotate", {"path": self.clip_path, "direction": "left"})

    def test_with_writing_clips_on_a_turn_a_save_carrying_date_taken_and_a_smart_rename_stay_refused(self):
        # Writing clips on lets a keyword or caption save patch its XMP (DECISIONS 2026-11-05, M7); a turn, a Date Taken
        # and a name are still ExifTool's rewrite of the whole file, refused.
        with mock.patch.object(field_values, "CLIP_PATCH_IN_PLACE", True):
            self.assert_refused("photo/rotate", {"path": self.clip_path, "direction": "left"})
            self.assert_refused("photo/save-metadata", {"path": self.clip_path, "tags": ["Trips/Coast"],
                                                        "date_taken": "2020:01:01 12:00:00"})
            self.test_a_smart_rename_naming_a_clip_renames_the_photo_and_names_the_clip()

    def test_a_selection_of_a_photo_and_a_clip_tags_the_photo_and_reports_the_clip(self):
        reply = self.post("library/bulk-edit", {"op": "tags", "selection": {"ids": [self.photo, self.clip]},
                                                "params": {"add": ["Trips/Coast"]}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        done = reply.get_json()
        self.assertEqual((1, 1), (done["changed"], done["error_count"]))
        self.assertEqual([(self.clip, images.CLIP_NOT_EDITED)], [(e["id"], e["why"]) for e in done["errors"]])
        self.assertEqual(["Trips/Coast"], self.subjects(self.photo_path), "the photo beside it is written")
        self.assert_clip_untouched()

    def test_add_to_all_selected_writes_the_photo_after_the_clip_and_names_the_clip(self):
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path, self.photo_path], "add_tags": ["Trips/Coast"]})
        body = reply.get_json()
        self.assertIn(images.CLIP_NOT_EDITED, body["error"])
        self.assertEqual([paths.key(self.photo_path)], [paths.key(path) for path in body["written"]],
                         "a clip is not the first error that stops the write: the photo after it is written")
        self.assertEqual(["Trips/Coast"], self.subjects(self.photo_path))
        self.assert_clip_untouched()

    def test_a_smart_rename_naming_a_clip_renames_the_photo_and_names_the_clip(self):
        reply = self.post("folder/rename-photos", {"folder_path": self.folder, "grouping": "Harbour",
                                                   "photo_paths": [self.photo_path, self.clip_path]})
        self.assertNotEqual(200, reply.status_code)
        body = reply.get_json()
        self.assertIn(images.CLIP_NOT_EDITED, body["error"])
        self.assertEqual([paths.key(self.photo_path)], [paths.key(old) for old in body["updated_paths"]])
        self.assertFalse(os.path.exists(self.photo_path), "the photo is renamed")
        self.assertIn(paths.key(self.clip_path), [paths.key(record["path"]) for record in body["updated_photos"]],
                      "the folder the page is answered still holds the clip (#2564)")
        self.assert_clip_untouched()

    def test_a_delete_of_the_clip_is_refused_with_the_sentence_and_nothing_is_copied_or_removed(self):
        with mock.patch.object(recycle_bin, "delete_file") as deleted:
            reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(400, reply.status_code, reply.get_json())
        self.assertIn(images.CLIP_NOT_DELETED, reply.get_json()["error"])
        deleted.assert_not_called()
        self.assert_clip_untouched()

    def test_a_selection_deleted_through_this_pc_deletes_the_photo_and_never_copies_the_clip(self):
        sent = []

        def recycle(path):
            sent.append(path)
            os.remove(path)
            return True

        def reason(path):
            # The photos' folder is a share, as far as the test says; this PC's mirror folder is not.
            return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.folder)) else None

        with mock.patch.object(recycle_bin, "no_bin_reason", side_effect=reason), \
                mock.patch.object(recycle_bin, "can_copy_here", return_value=None) as room, \
                mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle), \
                mock.patch.object(recycle_bin, "mirror_root", return_value=os.path.join(self.home.root, "Mirror")):
            asked = self.post("library/selection/delete-check", {"selection": {"ids": [self.photo, self.clip]}}).get_json()
            self.assertEqual(os.path.getsize(self.photo_path), asked["copy_bytes"],
                             "the question counts the copies of the photos only: a clip is never copied")
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": [self.photo, self.clip]},
                                                    "params": {"token": asked["token"]}})
            self.assertEqual(200, reply.status_code, reply.get_json())
            done = reply.get_json()
            sizes = [call.args[0] for call in room.call_args_list]
        self.assertEqual((1, 1), (done["changed"], done["error_count"]), done["errors"])
        self.assertEqual([(self.clip, images.CLIP_NOT_DELETED)], [(e["id"], e["why"]) for e in done["errors"]])
        self.assertNotIn(paths.key(self.clip_path), [paths.key(path) for path in sent])
        self.assertTrue(all(size < os.path.getsize(self.clip_path) for size in sizes),
                        "this PC is asked for room for the photo's copy, never the clip's")
        self.assertFalse(os.path.exists(self.photo_path), "the photo beside it is deleted")
        self.assert_clip_untouched()


if __name__ == "__main__":
    unittest.main()

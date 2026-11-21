"""A clip in the library is not turned by TagPup, nor moved by a Camera Time Shift (C2): a turn that names a clip leaves
its file byte for byte as it was, journals nothing for it, and says so in a plain sentence
(tagpup.files.images.CLIP_NOT_EDITED); a time shift over a folder of photos and clips moves the photos' dates and leaves
each clip's file as it was. A clip is deleted as a photo is (DECISIONS 2026-11-08, A5, cut B). A tag, title or Date
Taken save is patched into its XMP (field_values.writes, since DECISIONS 2026-11-07 and 2026-11-08, A5 (D) REVISED; the
selections that tag photos and clips together: tests/test_a_clip_tagged_through_the_journal.py; a date:
tests/test_a_clip_dated_through_the_journal.py; a rename: tests/test_a_clip_renamed.py).

What the owner lives through: the library view lists clips beside photos (V1), so a selection of a day holds both, and a
click on a clip's card opens the photo panel. A turn of a clip is ExifTool's rewrite of the whole file -- gigabytes, over
a share (DECISIONS 2026-11-02, items 1 and 3); a time shift moves a camera's EXIF dates, which a clip does not hold
(DECISIONS 2026-11-08, A5 (D) REVISED: the Camera Time Shift leaves clips out).

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

from tests import page_writes  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.files import images  # noqa: E402
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

    def test_a_tag_save_from_the_photo_panel_writes_the_clips_tags(self):
        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": ["Trips/Coast"]}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual(["Trips/Coast"], self.subjects(self.clip_path))

    def test_a_tag_save_of_a_clip_titled_in_quicktime_alone_is_written_not_refused_as_changed_on_disk(self):
        """#2584: 2,017 photo_index clips hold only QuickTime:Title (counted 2026-11-06). The page shows it as the title;
        the save's compare reads the title fields alone, so a tag save from the panel was refused 409 'changed on disk'."""
        titled = os.path.join(self.folder, "slipway.mov")
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "title=Slipway at dusk", titled],
                             capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)
        clip = self.vl.photo("Harbour", "slipway.mov", at=titled, fields={"QuickTime:Title": "Slipway at dusk"})
        reply = self.client.get("/library/api/library/photo", query_string={"id": clip})
        self.assertEqual(200, reply.status_code, reply.data)
        record = reply.get_json()["photo"]
        self.assertEqual("Slipway at dusk", record["title"])
        before = digest(titled)
        saved = self.post("photos/write", page_writes.save(self.vl.library, **{
            "path": record["path"], "title": record["title"], "tags": record["tags"] + ["Trips/Coast"],
            "stamp": {"mtime": record["mtime"], "size": record["size"]},
            "base": {"tags": record["tags"], "title": record["title"]}}))
        self.assertEqual((200, True), (saved.status_code, saved.get_json()["success"]), saved.data)
        self.assertNotEqual(before, digest(titled), "the clip's XMP is patched")
        self.assertEqual(["Trips/Coast"], self.subjects(titled))

    def test_a_turn_or_a_mirror_is_refused_with_the_sentence_and_the_clip_is_not_written(self):
        for direction in ("left", "mirror"):
            with self.subTest(direction=direction):
                self.assert_refused("photo/rotate", {"path": self.clip_path, "direction": direction})

    def test_a_time_shift_over_a_folder_moves_the_photos_date_and_leaves_the_clip_byte_for_byte(self):
        reply = self.post("folder/time-shift", {"folder_path": self.folder, "camera_model": "All Cameras",
                                                "shift_minutes": 30})
        self.assertEqual(200, reply.status_code, reply.get_json())
        body = reply.get_json()
        self.assertEqual((True, 1, 1), (body["success"], body["updated_count"], body["requested_count"]),
                         "the photo alone is shifted, and counted")
        with ExifToolSession(executable=EXIFTOOL) as et:
            moved = et.get_tags([self.photo_path], tags=["EXIF:DateTimeOriginal"])[0]["EXIF:DateTimeOriginal"]
        self.assertEqual("2024:07:04 10:30:00", moved, "the photo's date moved")
        self.assert_clip_untouched()

if __name__ == "__main__":
    unittest.main()

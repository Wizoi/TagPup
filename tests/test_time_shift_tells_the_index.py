"""Shifting Date Taken says how many photos it changed, and tells the index.

Time shift rewrote DateTimeOriginal in every matching file and then refreshed only the
page's folder cache. The index rows kept the old Date Taken -- which orders photos and
picks the era a face is compared against -- and the old mtime and size, so the next
scan distrusted every one of them. And it answered "success" with the number of
photos it had tried, even when ExifTool wrote none of them.

TagPup and TagTuner each had their own copy. TagTuner's is gone, and TagPup's handler
calls the service, tagpup.services.photos.shift_date_taken.
"""
import inspect
import json
import os
import unittest

from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

import tagpup_server
import tuner_server
from exiftool_session import ExifToolSession

TAKEN = "2024:07:04 10:00:00"
SHIFTED = "2024:07:04 10:30:00"


def make_photo(path):
    from PIL import Image

    Image.new("RGB", (8, 8), (40, 60, 80)).save(path)
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags={"EXIF:DateTimeOriginal": TAKEN, "EXIF:Model": "Harbour Cam"},
                    params=["-overwrite_original"])
    return path


def date_taken(raw):
    return raw.get("EXIF:DateTimeOriginal") or raw.get("DateTimeOriginal")


@requires_exiftool
class TimeShiftTellsTheIndex(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.photos = [make_photo(os.path.join(self.lib.photos, n)) for n in ("a.jpg", "b.jpg")]
        # A file ExifTool cannot write: it is not a JPEG at all.
        self.broken = os.path.join(self.lib.photos, "broken.jpg")
        with open(self.broken, "wb") as handle:
            handle.write(b"not a picture")
        for path in self.photos:
            self.lib.execute(
                "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                " VALUES (?, 1.0, 1, '[]', '[]', ?)",
                (path, json.dumps({"EXIF:DateTimeOriginal": TAKEN, "EXIF:Model": "Harbour Cam"})))

    def shift(self):
        handler = self.lib.handler(EXIFTOOL)
        handler.body = {"folder_path": self.lib.photos, "camera_model": "All Cameras",
                        "shift_minutes": 30}
        handler.handle_post_folder_time_shift()
        return handler.reply

    def test_it_reports_the_photos_it_changed_not_the_ones_it_tried(self):
        reply = self.shift()
        self.assertTrue(reply["success"], reply)
        self.assertEqual(2, reply["updated_count"])
        self.assertEqual(3, reply["requested_count"])

    def test_the_index_has_the_new_date_and_the_files_new_stat(self):
        self.shift()
        for path in self.photos:
            raw, mtime, size = self.lib.rows(
                "SELECT raw_metadata, mtime, size FROM photos WHERE path = ?", (path,))[0]
            self.assertEqual(SHIFTED, date_taken(json.loads(raw)), path)
            stat = os.stat(path)
            self.assertEqual((stat.st_mtime, stat.st_size), (mtime, size), path)


class TheHandlerUsesTheSharedShift(unittest.TestCase):
    # TagTuner had a copy of this route too; its page never called it, and it is gone.
    def test_it_shifts_through_the_service(self):
        handler = tagpup_server.TagPupHTTPRequestHandler
        source = inspect.getsource(handler.handle_post_folder_time_shift)
        self.assertIn("shift_date_taken(", source)
        self.assertNotIn("DateTimeOriginal", source,
                         "the handler shifts files itself instead of through the service")

    def test_tagtuner_has_no_copy(self):
        self.assertFalse(hasattr(tuner_server.TunerHTTPRequestHandler,
                                 "handle_post_folder_time_shift"))


if __name__ == "__main__":
    unittest.main()

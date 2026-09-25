"""Shifting Date Taken says how many photos it changed, and tells the index.

Time shift rewrote DateTimeOriginal in every matching file and then refreshed only the
page's folder cache. The index rows kept the old Date Taken -- which orders photos and
picks the era a face is compared against -- and the old mtime and size, so the next
scan distrusted every one of them. And it answered "success" with the number of
photos it had tried, even when ExifTool wrote none of them.

TagPup and TagTuner each had their own copy. TagTuner's is gone, and TagPup's route
calls the service, tagpup.services.photos.shift_date_taken.
"""
import inspect
import json
import os
import unittest

from tests import photo_rows
from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

from tagpup.files.exiftool_session import ExifToolSession
from tagpup.store import db

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
        # Rows as the indexer makes them, describing their files.
        conn = db.connect(self.lib.db_path)
        try:
            for path in self.photos:
                photo_rows.add_read(conn, path, {"EXIF:DateTimeOriginal": TAKEN, "EXIF:Model": "Harbour Cam"})
            conn.commit()
        finally:
            conn.close()

    def shift(self):
        status, reply = self.lib.post("/api/folder/time-shift", {
            "folder_path": self.lib.photos, "camera_model": "All Cameras", "shift_minutes": 30})
        self.assertEqual(status, 200, reply)
        return reply

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


class TheRouteUsesTheSharedShift(unittest.TestCase):
    # TagTuner had a copy of this route too; its page never called it, and it is gone.
    def test_it_shifts_through_the_service(self):
        from tagpup.web import tagpup_routes

        source = inspect.getsource(tagpup_routes.folder_time_shift)
        self.assertIn("shift_date_taken(", source)
        self.assertNotIn("DateTimeOriginal", source,
                         "the route shifts files itself instead of through the service")

    def test_tagtuner_has_no_copy(self):
        from tagpup.web import app as web

        self.assertNotIn("/api/folder/time-shift",
                         [rule.rule for rule in web.create_app("tuner").url_map.iter_rules()])


if __name__ == "__main__":
    unittest.main()

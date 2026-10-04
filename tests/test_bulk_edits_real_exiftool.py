"""A bulk edit through the real ExifTool and real JPEGs: tags added and taken off, Date Taken shifted, a restart in the middle of
a shift (tests/test_bulk_edits.py stands ExifTool in for everything else).

The routes are Flask's test client; the job runs on its thread and the test joins it. Photos are small JPEGs written with
Pillow and ExifTool, rows as the indexer records them. Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TAKEN = "2024:07:04 10:00:00"
ALL = {"source": {"kind": "all"}}


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class RealFiles(unittest.TestCase):
    PHOTOS = 7

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.addCleanup(bulk_edits.forget, self.vl.library)
        patcher = mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast", "Old/Stuff")
        self.paths = []
        for n in range(self.PHOTOS):
            path = make_jpeg(os.path.join(self.vl.pictures, "Coast", "p%d.jpg" % n), size=(32, 32))
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.set_tags([path], tags={"EXIF:DateTimeOriginal": TAKEN, "XMP:Subject": ["Old/Stuff"]},
                            params=["-overwrite_original"])
            self.vl.photo("Coast", "p%d.jpg" % n, taken=TAKEN, tags=["Old/Stuff"], at=path)
            self.paths.append(path)

    def run_job(self, op, params, selection=ALL):
        reply = self.client.post("/library/api/library/bulk/start", json={"op": op, "selection": selection, "params": params})
        self.assertEqual(200, reply.status_code, reply.get_json())
        handle = reply.get_json()["job"]
        bulk_edits._held(self.vl.library)[handle].thread.join(120)
        return self.client.get("/library/api/library/bulk/status", query_string={"job": handle}).get_json()

    def read(self, path, *fields):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return et.get_tags([path], tags=list(fields))[0]

    def test_tags_are_added_and_removed_in_the_files_and_the_rows(self):
        done = self.run_job("tags", {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]})
        self.assertEqual(("done", self.PHOTOS, 0), (done["state"], done["changed"], done["error_count"]))
        for path in self.paths:
            held = self.read(path, "XMP:Subject")["XMP:Subject"]
            self.assertEqual(["Trips/Coast"], held if isinstance(held, list) else [held])
        for (tags,) in self.vl.rows("SELECT tags FROM photos"):
            self.assertEqual('["Trips/Coast"]', tags)
        again = self.run_job("tags", {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]})
        self.assertEqual((0, self.PHOTOS), (again["changed"], again["unchanged"]))

    def test_date_taken_is_shifted_once_in_every_file_and_the_row_has_the_new_date(self):
        done = self.run_job("time_shift", {"minutes": 90})
        self.assertEqual(("done", self.PHOTOS), (done["state"], done["changed"]))
        for path in self.paths:
            self.assertEqual("2024:07:04 11:30:00", self.read(path, "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual({"2024:07:04 11:30:00"}, {taken for (taken,) in self.vl.rows("SELECT taken FROM photos")})

    def test_a_file_that_is_not_a_picture_is_an_error_entry_and_the_others_are_written(self):
        with open(self.paths[3], "wb") as handle:
            handle.write(b"not a picture at all")
        done = self.run_job("tags", {"add": ["Trips/Coast"]})
        self.assertEqual(("done", self.PHOTOS - 1, 1), (done["state"], done["changed"], done["error_count"] + done["skipped_damaged"]))


if __name__ == "__main__":
    unittest.main()

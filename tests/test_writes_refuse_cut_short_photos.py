"""Nothing is written into a photo whose file is cut short, or filled with zeros after some point, though no
record of it was ever made (DECISIONS 2026-10-08, "Truncated JPEGs stay refused, not read").

The refusal used to come from a table of photos the indexer had found damaged. It comes from the file now: ExifTool's
whole-file scan (-validate) says "JPEG format error" of a JPEG that stops short, and a write that would put metadata
into such a file is left out, counted as skipped (details "skipped_damaged"), the file as it was, byte for byte and
modified time. Real ExifTool, real JPEGs made as the owner's were damaged (tests/damaged_photos.py); rows as the indexer
records them. Fictional names only.
"""
import hashlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damaged_photos  # noqa: E402
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import tagging  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TAKEN = "2024:07:04 10:00:00"
THEN = 1_700_000_000
ALL = {"source": {"kind": "all"}}


def fingerprint(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest(), os.stat(path).st_mtime_ns


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class CutShortPhotos(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.addCleanup(bulk_edits.forget, self.vl.library)
        patcher = mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast")
        folder = os.path.join(self.vl.pictures, "Coast")
        self.whole = [make_jpeg(os.path.join(folder, "whole%d.jpg" % n), size=(32, 32)) for n in range(3)]
        self.cut = damaged_photos.truncated(os.path.join(folder, "cut short.jpg"))
        self.half = damaged_photos.second_half_zeros(os.path.join(folder, "copy stopped.jpg"))
        self.ids = {}
        for path in self.whole + [self.cut, self.half]:
            os.utime(path, (THEN, THEN))
            self.ids[path] = self.vl.photo("Coast", os.path.basename(path), taken=TAKEN, at=path)
        self.before = {path: fingerprint(path) for path in (self.cut, self.half)}

    def keywords(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            held = et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject", [])
        return held if isinstance(held, list) else [held]

    def test_a_bulk_tag_write_skips_them_and_writes_the_others(self):
        reply = self.client.post("/library/api/library/bulk/start", json={
            "op": "tags", "selection": ALL, "params": {"add": ["Trips/Coast"], "remove": []}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        handle = reply.get_json()["job"]
        bulk_edits._held(self.vl.library)[handle].thread.join(120)
        done = self.client.get("/library/api/library/bulk/status", query_string={"job": handle}).get_json()
        self.assertEqual(("done", 3, 2, 0), (done["state"], done["changed"], done["skipped_damaged"],
                                             done["error_count"]))
        for path in self.whole:
            self.assertEqual(["Trips/Coast"], self.keywords(path))
        for path in (self.cut, self.half):
            self.assertEqual(self.before[path], fingerprint(path), "a photo cut short was written to")

    def test_the_save_of_one_is_not_made_and_does_not_rename_it(self):
        result = tagging.save_photo(self.vl.library, self.cut, "Quay at dusk", ["Trips/Coast"], None, EXIFTOOL, "{}")
        self.assertFalse(result.ok, "the save of a photo cut short went through")
        self.assertEqual(0, result.changed)
        self.assertTrue(os.path.exists(self.cut), "it was renamed after a caption it did not get")
        self.assertEqual(self.before[self.cut], fingerprint(self.cut))


if __name__ == "__main__":
    unittest.main()

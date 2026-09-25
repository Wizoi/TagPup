"""A folder opened while empty shows the photos copied into it afterwards.

The old server sent [] for an empty folder and kept nothing; the Flask port cached the
empty scan like any other, so the folder stayed empty until Refresh, and a time shift
on it found "no photos" (docs/findings.md, #125). Found in review of the port.
"""
import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402


class AnEmptyFolder(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.folder = os.path.join(self.home.root, "Photos", "New Shoot")
        os.makedirs(self.folder)

    def scan(self, **query):
        reply = self.client.get("/library/api/folder/scan", query_string=dict(path=self.folder, **query))
        self.assertEqual(200, reply.status_code, reply.data)
        return [record["filename"] for record in reply.get_json()]

    def test_photos_added_after_the_first_look_are_found_without_refresh(self):
        self.assertEqual([], self.scan())
        Image.new("RGB", (8, 8)).save(os.path.join(self.folder, "a.jpg"), "JPEG")
        self.assertEqual(["a.jpg"], self.scan())

    def test_a_time_shift_finds_them_too(self):
        self.assertEqual([], self.scan())
        Image.new("RGB", (8, 8)).save(os.path.join(self.folder, "a.jpg"), "JPEG")
        reply = self.client.post("/library/api/folder/time-shift",
                                 json={"folder_path": self.folder, "camera_model": "All Cameras", "shift_minutes": 30})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertNotIn("No photos matched", reply.get_json().get("message", ""))


if __name__ == "__main__":
    unittest.main()

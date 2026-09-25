"""A path or a name in a request is decoded once, by Flask (docs/findings.md, #32).

The routes decoded `request.args` a second time, so a folder named "Scan%41" reached
them as "ScanA": its photos and the folder itself were not found.
"""
import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402


class APercentInAPath(unittest.TestCase):
    def setUp(self):
        self.folder = None

    def app(self, kind):
        app, home = web_client.app_for(self, kind)
        self.folder = os.path.join(home.root, "Photos", "Scan%41")
        os.makedirs(os.path.join(self.folder, "Sub%42"))
        self.photo = os.path.join(self.folder, "IMG_%41.jpg")
        Image.new("RGB", (8, 8)).save(self.photo, "JPEG")
        return app.test_client()

    def get(self, client, route, **query):
        return client.get("/library" + route, query_string=query)

    def test_tagpup_serves_the_photo(self):
        reply = self.get(self.app("tagpup"), "/api/photo-file", path=self.photo)
        self.assertEqual(200, reply.status_code, reply.data)

    def test_tagpup_scans_the_folder(self):
        reply = self.get(self.app("tagpup"), "/api/folder/scan", path=self.folder)
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["IMG_%41.jpg"], [record["filename"] for record in reply.get_json()])

    def test_tagpup_completes_the_folder_typed(self):
        client = self.app("tagpup")
        reply = self.get(client, "/api/autocomplete-folder", path=self.folder + os.sep)
        self.assertEqual([os.path.join(self.folder, "Sub%42")], reply.get_json())

    def test_tagtuner_serves_the_photo(self):
        reply = self.get(self.app("tuner"), "/api/photo-file", path=self.photo)
        self.assertEqual(200, reply.status_code, reply.data)

    def test_tagtuner_lists_the_subfolders(self):
        reply = self.get(self.app("tuner"), "/api/folder/subfolders", path=self.folder)
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Sub%42"], [entry["name"] for entry in reply.get_json()["folders"]])


if __name__ == "__main__":
    unittest.main()

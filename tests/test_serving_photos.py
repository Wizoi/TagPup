"""GET /api/photo-file, as each app serves it (tagpup.web.responses.photo).

The two apps share the code and differ on purpose in two ways. TagPup turns a photo
upright and lets the browser keep it for a day. TagTuner draws face boxes over its
photos in the stored pixels' coordinates, so it serves them as stored, and keeps nothing.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402


class ServingAPhoto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="serving_photos_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        # Stored 60 wide and 30 high; its Orientation (6) shows it 30 wide and 60 high.
        self.photo = os.path.join(self.dir, "lake.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (60, 30)).save(self.photo, exif=exif)
        self.clients = {}

    def get(self, kind, path, size=None):
        if kind not in self.clients:
            app, _home = web_client.app_for(self, kind)
            self.clients[kind] = app.test_client()
        query = {"path": path}
        if size is not None:
            query["size"] = size
        return self.clients[kind].get("/library/api/photo-file", query_string=query)

    def shown_size(self, reply):
        with Image.open(io.BytesIO(reply.data)) as img:
            return img.size

    def test_tagpup_serves_it_upright_and_lets_the_browser_keep_it(self):
        reply = self.get("tagpup", self.photo, "100")
        self.assertEqual((reply.status_code, self.shown_size(reply)), (200, (30, 60)))
        self.assertEqual(reply.headers["Cache-Control"], "max-age=86400")

    def test_tagtuner_serves_it_as_stored_and_keeps_nothing(self):
        reply = self.get("tuner", self.photo, "100")
        self.assertEqual((reply.status_code, self.shown_size(reply)), (200, (60, 30)))
        self.assertNotIn("Cache-Control", reply.headers)

    def test_the_file_itself_without_a_size(self):
        for kind in ("tagpup", "tuner"):
            reply = self.get(kind, self.photo)
            with open(self.photo, "rb") as f:
                self.assertEqual(reply.data, f.read(), kind)
            self.assertEqual(reply.headers["Content-Type"], "image/jpeg", kind)
            self.assertEqual(reply.headers["Content-Length"], str(os.path.getsize(self.photo)), kind)

    def test_a_size_that_is_not_a_number_gets_the_file_itself(self):
        reply = self.get("tagpup", self.photo, "big")
        with open(self.photo, "rb") as f:
            self.assertEqual(reply.data, f.read())

    def test_what_is_refused_and_what_is_not_found(self):
        secret = os.path.join(self.dir, "secret.txt")
        with open(secret, "w") as f:
            f.write("private")
        for kind in ("tagpup", "tuner"):
            self.assertEqual(self.get(kind, secret).status_code, 400, kind)
            self.assertEqual(self.get(kind, os.path.join(self.dir, "ghost.jpg")).status_code, 404, kind)
            reply = self.clients[kind].get("/library/api/photo-file")
            self.assertEqual(reply.status_code, 400, kind)
            self.assertIn(b"Missing &#39;path&#39; parameter", reply.data, kind)


if __name__ == "__main__":
    unittest.main()

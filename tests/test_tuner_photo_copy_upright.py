"""TagTuner serves a turned photo upright when the page asks, and as stored otherwise
(#31, found in review).

The Tags view's cards asked for a small copy (size=300) once they stopped loading the
original, and TagTuner's /api/photo-file makes its copies as stored -- its face views
draw boxes in the stored pixels' coordinates (#1) -- so a photo carrying an Orientation
showed sideways, where the browser had turned the original by itself. The cards draw no
boxes: they ask `upright=1`, and the face views keep asking without it.
"""
import io
import os
import sys
import unittest
import urllib.parse

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

ORIENTATION = 0x0112
TURNED_RIGHT = 6   # stored on its side: seen upright, width and height swap


class TunerPhotoCopyUpright(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()
        folder = os.path.join(self.home.root, "Photos", "Harbour Walk")
        os.makedirs(folder)
        self.photo = os.path.join(folder, "lighthouse.jpg")
        exif = Image.Exif()
        exif[ORIENTATION] = TURNED_RIGHT
        Image.new("RGB", (60, 30), (200, 40, 40)).save(self.photo, "JPEG", exif=exif)

    def served_size(self, query=""):
        reply = self.client.get("/library/api/photo-file?path=%s&size=300%s"
                                % (urllib.parse.quote(self.photo), query))
        self.assertEqual(200, reply.status_code, reply.data[:200])
        with Image.open(io.BytesIO(reply.data)) as img:
            return img.size

    def test_asked_upright_the_copy_is_turned(self):
        self.assertEqual((30, 60), self.served_size("&upright=1"))

    def test_asked_without_it_the_copy_is_as_stored_for_the_face_boxes(self):
        self.assertEqual((60, 30), self.served_size())
        self.assertEqual((60, 30), self.served_size("&upright=0"))


if __name__ == "__main__":
    unittest.main()

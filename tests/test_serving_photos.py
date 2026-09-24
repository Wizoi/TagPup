"""GET /api/photo-file, as each app serves it (localserver.serve_photo_file).

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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tagpup_server import TagPupHTTPRequestHandler  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402


def handler_of(server_class):
    """A request handler of `server_class` with no socket, that keeps what it sends."""
    class Handler(server_class):
        def __init__(self):  # noqa: D107 -- no socket, on purpose
            self.status = None
            self.sent = {}
            self.error = None
            self.wfile = io.BytesIO()

        def send_response(self, code, message=None):
            self.status = code

        def send_header(self, name, value):
            self.sent[name] = value

        def end_headers(self):
            pass

        def send_error(self, code, message=None, explain=None):
            self.status, self.error = code, message

    return Handler()


class ServingAPhoto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="serving_photos_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        # Stored 60 wide and 30 high; its Orientation (6) shows it 30 wide and 60 high.
        self.photo = os.path.join(self.dir, "lake.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (60, 30)).save(self.photo, exif=exif)

    def get(self, server_class, path, size=None):
        handler = handler_of(server_class)
        query = {"path": [path]}
        if size is not None:
            query["size"] = [size]
        handler.handle_serve_photo_file(query)
        return handler

    def shown_size(self, handler):
        with Image.open(io.BytesIO(handler.wfile.getvalue())) as img:
            return img.size

    def test_tagpup_serves_it_upright_and_lets_the_browser_keep_it(self):
        handler = self.get(TagPupHTTPRequestHandler, self.photo, "100")
        self.assertEqual((handler.status, self.shown_size(handler)), (200, (30, 60)))
        self.assertEqual(handler.sent["Cache-Control"], "max-age=86400")

    def test_tagtuner_serves_it_as_stored_and_keeps_nothing(self):
        handler = self.get(TunerHTTPRequestHandler, self.photo, "100")
        self.assertEqual((handler.status, self.shown_size(handler)), (200, (60, 30)))
        self.assertNotIn("Cache-Control", handler.sent)

    def test_the_file_itself_without_a_size(self):
        for server_class in (TagPupHTTPRequestHandler, TunerHTTPRequestHandler):
            handler = self.get(server_class, self.photo)
            with open(self.photo, "rb") as f:
                self.assertEqual(handler.wfile.getvalue(), f.read())
            self.assertEqual(handler.sent["Content-Type"], "image/jpeg")
            self.assertEqual(handler.sent["Content-Length"], str(os.path.getsize(self.photo)))

    def test_a_size_that_is_not_a_number_gets_the_file_itself(self):
        handler = self.get(TagPupHTTPRequestHandler, self.photo, "big")
        with open(self.photo, "rb") as f:
            self.assertEqual(handler.wfile.getvalue(), f.read())

    def test_what_is_refused_and_what_is_not_found(self):
        secret = os.path.join(self.dir, "secret.txt")
        with open(secret, "w") as f:
            f.write("private")
        for server_class in (TagPupHTTPRequestHandler, TunerHTTPRequestHandler):
            self.assertEqual(self.get(server_class, secret).status, 400)
            self.assertEqual(self.get(server_class, os.path.join(self.dir, "ghost.jpg")).status, 404)
            handler = handler_of(server_class)
            handler.handle_serve_photo_file({})
            self.assertEqual((handler.status, handler.error), (400, "Missing 'path' parameter"))


if __name__ == "__main__":
    unittest.main()

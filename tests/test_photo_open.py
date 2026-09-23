"""Opening a photo from Image Details, and showing it in Explorer.

Clicking the path opens the photo in the default app; "Show in File Explorer"
selects it in its folder. The Explorer command quoted the whole "/select,<path>"
switch for any path with a space, which Explorer does not parse as a selection.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import tagpup_server  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler as Handler  # noqa: E402


class Call:
    """Run one POST handler on a JSON body and capture what it sent."""

    def __init__(self, method, body):
        handler = Handler.__new__(Handler)
        raw = json.dumps(body).encode("utf-8")
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        self.sent = []
        handler.send_json = lambda data: self.sent.append((200, data))
        handler.send_json_error = lambda code, message: self.sent.append((code, message))
        getattr(handler, method)()


class OpenPhoto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="open photo ")
        self.photo = os.path.join(self.dir, "Meet - 01.jpg")
        self.script = os.path.join(self.dir, "run me.bat")
        for path in (self.photo, self.script):
            open(path, "wb").close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_photo_opens_in_its_default_app(self):
        with mock.patch("os.startfile", create=True) as startfile:
            call = Call("handle_post_photo_open", {"path": self.photo.replace("\\", "/")})
        self.assertEqual(call.sent, [(200, {"success": True})])
        startfile.assert_called_once_with(os.path.abspath(self.photo))

    def test_anything_that_is_not_a_photo_is_refused(self):
        for path in (self.script, os.path.join(self.dir, "missing.jpg"), self.dir, ""):
            with mock.patch("os.startfile", create=True) as startfile:
                call = Call("handle_post_photo_open", {"path": path})
            self.assertEqual(call.sent[0][0], 400, path)
            startfile.assert_not_called()

    def test_explorer_gets_the_path_quoted_after_the_switch(self):
        self.assertEqual(tagpup_server.explorer_select_command(self.photo),
                         'explorer.exe /select,"%s"' % os.path.abspath(self.photo))


if __name__ == "__main__":
    unittest.main()

"""Opening a photo from Image Details, and showing it in Explorer.

Clicking the path opens the photo in the default app; "Show in File Explorer"
selects it in its folder. The Explorer command quoted the whole "/select,<path>"
switch for any path with a space, which Explorer does not parse as a selection.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup.web import desktop  # noqa: E402


class OpenPhoto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="open photo ")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.photo = os.path.join(self.dir, "Meet - 01.jpg")
        self.script = os.path.join(self.dir, "run me.bat")
        for path in (self.photo, self.script):
            open(path, "wb").close()
        app, _home = web_client.app_for(self, "tagpup")
        self.client = app.test_client()

    def open(self, path):
        reply = self.client.post("/library/api/photo/open", json={"path": path})
        return reply.status_code, reply.get_json()

    def test_a_photo_opens_in_its_default_app(self):
        with mock.patch("os.startfile", create=True) as startfile:
            self.assertEqual(self.open(self.photo.replace("\\", "/")), (200, {"success": True}))
        startfile.assert_called_once_with(os.path.abspath(self.photo))

    def test_anything_that_is_not_a_photo_is_refused(self):
        for path in (self.script, os.path.join(self.dir, "missing.jpg"), self.dir, ""):
            with mock.patch("os.startfile", create=True) as startfile:
                status, _reply = self.open(path)
            self.assertEqual(status, 400, path)
            startfile.assert_not_called()

    def test_explorer_gets_the_path_quoted_after_the_switch(self):
        self.assertEqual(desktop.explorer_select_command(self.photo),
                         'explorer.exe /select,"%s"' % os.path.abspath(self.photo))


if __name__ == "__main__":
    unittest.main()

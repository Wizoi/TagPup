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

    def test_explorer_gets_the_path_quoted_after_the_switch_and_no_shell(self):
        """Lifted from test_tagpup_server_api's TestOpenExplorer: the command is one string handed to CreateProcess, as an
        argument list quotes the whole `/select,<path>` for a path with a space and Explorer then selects nothing; a path
        that is no file launches nothing. Broken in a scratch copy and this fails: explorer_select_command leaving the path
        unquoted."""
        self.assertEqual(desktop.explorer_select_command(self.photo),
                         'explorer.exe /select,"%s"' % os.path.abspath(self.photo))
        with mock.patch("subprocess.Popen") as popen:
            ok = self.client.post("/library/api/photo/open-explorer", json={"path": self.photo})
            ghost = self.client.post("/library/api/photo/open-explorer", json={"path": self.photo + ".gone"})
        self.assertEqual((200, 400), (ok.status_code, ghost.status_code))
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual((desktop.explorer_select_command(self.photo), False), (args[0], kwargs.get("shell", False)))


if __name__ == "__main__":
    unittest.main()

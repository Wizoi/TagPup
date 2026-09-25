"""Reloading the index keeps the connection it has.

remove_paths() and build_or_update() reload the index, and every reload opened a new
connection and dropped the old one unclosed. On Windows an unclosed handle keeps the
database file locked, which is how test directories and sandboxes could not be
deleted afterwards.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tagpup.services.search import PhotoIndex  # noqa: E402


class ReloadReusesItsConnection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reload_")
        self.index = PhotoIndex(os.path.join(self.dir, "lib.db"))

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_reload_keeps_the_same_connection(self):
        self.index.load()
        first = self.index.conn
        self.index.remove_paths({os.path.join(self.dir, "gone.jpg")})
        self.assertIs(self.index.conn, first)

    def test_after_close_the_file_can_be_deleted(self):
        self.index.load()
        self.index.load()
        self.index.close()
        os.remove(os.path.join(self.dir, "lib.db"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "lib.db")))


if __name__ == "__main__":
    unittest.main()

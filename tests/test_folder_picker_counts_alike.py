"""The folder picker counts indexed photos the same way it counts images.

Each child folder's `images` counted every photo under it, and its `indexed` only the
photos directly in it. A folder of subfolders, fully indexed, showed "8756 image(s)"
as though new, and was preselected. The folder itself had no count at all, so an
indexed leaf folder was always offered as new. In photo_index.db 72 folders held their
indexed photos only below them.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
from index import PhotoIndex  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402


class Handler(TunerHTTPRequestHandler):
    def __init__(self, db_path):  # noqa: D107 -- no socket, on purpose
        self.db_path = db_path
        self.reply = None
        self.wfile = io.BytesIO()

    def send_json(self, data):
        self.reply = data

    def send_json_error(self, code, message):
        self.reply = {"status": code, "error": message}


class FolderPickerCountsAlike(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="picker_counts_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.parent = os.path.join(self.dir, "Photos")
        photos = [os.path.join(self.parent, "Season", "Meet 1", "a.jpg"),
                  os.path.join(self.parent, "Season", "Meet 1", "b.jpg"),
                  os.path.join(self.parent, "Season", "Meet 2", "c.jpg"),
                  os.path.join(self.parent, "own.jpg")]
        for photo in photos:
            os.makedirs(os.path.dirname(photo), exist_ok=True)
            with open(photo, "wb") as handle:
                handle.write(b"a photo")
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = tagpup_db.connect(self.db)
        conn.executemany("INSERT INTO photos (path) VALUES (?)", [(p,) for p in photos])
        conn.commit()
        conn.close()

    def listing(self):
        handler = Handler(self.db)
        handler.handle_get_folder_subfolders({"path": [self.parent]})
        return handler.reply

    def test_a_folder_of_subfolders_counts_the_photos_below_it(self):
        season = next(f for f in self.listing()["folders"] if f["name"] == "Season")
        self.assertEqual(3, season["images"])
        self.assertEqual(3, season["indexed"], "indexed photos below the folder were not counted")

    def test_the_folder_itself_says_how_many_of_its_own_are_indexed(self):
        reply = self.listing()
        self.assertEqual(1, reply["own_images"])
        self.assertEqual(1, reply.get("own_indexed"))


if __name__ == "__main__":
    unittest.main()

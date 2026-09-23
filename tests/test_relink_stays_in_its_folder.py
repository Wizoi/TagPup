"""relink_renamed_photos.py only matches a dead row to a renamed file beside it.

Matching is by the stem of the name a file was renamed from, and camera names repeat
across a library. Every folder's matches were merged into one dict keyed by stem
alone, so a dead IMG_0421 in one meet's folder could be re-pointed -- with its named
faces -- at a different IMG_0421 renamed in another meet's folder.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import paths  # noqa: E402
import relink_renamed_photos  # noqa: E402


class FakeExifTool:
    """Answers get_tags from a table of {file name: tags}, reading no file."""
    table = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_tags(self, batch, tags=None):
        rows = []
        for path in batch:
            row = {"SourceFile": path.replace(os.sep, "/")}
            row.update(self.table.get(os.path.basename(path), {}))
            rows.append(row)
        return rows


class RelinkStaysInItsFolder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="relink_folder_")
        self.meet_a = os.path.join(self.dir, "2025-10 Invitational")
        self.meet_b = os.path.join(self.dir, "2025-11 Classic")
        for folder in (self.meet_a, self.meet_b):
            os.makedirs(folder)
        # Renamed files on disk, each remembering its camera name.
        for folder, name in ((self.meet_a, "Invitational - 01.jpg"),
                             (self.meet_b, "Classic - 01.jpg"),
                             (self.meet_b, "Classic - 02.jpg")):
            open(os.path.join(folder, name), "wb").close()
        FakeExifTool.table = {
            "Invitational - 01.jpg": {"XMP:PreservedFileName": "IMG_0001.CR3"},
            "Classic - 01.jpg": {"XMP:PreservedFileName": "IMG_0421.CR3"},
            "Classic - 02.jpg": {"XMP:PreservedFileName": "IMG_0002.CR3"},
        }
        # Dead rows: meet A's IMG_0421 was never renamed into meet B.
        self.dead_a = os.path.join(self.meet_a, "IMG_0421.jpg")
        self.dead_b = os.path.join(self.meet_b, "IMG_0002.jpg")
        self.db = os.path.join(self.dir, "lib.db")
        conn = db.connect(self.db)
        conn.executescript("CREATE TABLE photos (path TEXT PRIMARY KEY, document_id TEXT);"
                           "CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_path TEXT, name TEXT);")
        conn.executemany("INSERT INTO photos (path) VALUES (?)", [(self.dead_a,), (self.dead_b,)])
        conn.execute("INSERT INTO faces (photo_path, name) VALUES (?, ?)", (self.dead_a, "Rowan Thackeray"))
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def plan(self):
        import exiftool
        with mock.patch.object(exiftool, "ExifToolHelper", FakeExifTool):
            return relink_renamed_photos.plan_for(self.db)

    def test_a_row_is_not_matched_to_a_file_in_another_folder(self):
        moves, unmatched = self.plan()
        targets = {paths.key(m["from"]): m["to"] for m in moves}
        self.assertNotIn(paths.key(self.dead_a), targets)
        self.assertIn(self.dead_a, unmatched)

    def test_a_row_is_still_matched_beside_it(self):
        moves, _ = self.plan()
        by_from = {m["from"]: m["to"] for m in moves}
        self.assertEqual(paths.key(by_from[self.dead_b]),
                         paths.key(os.path.join(self.meet_b, "Classic - 02.jpg")))


if __name__ == "__main__":
    unittest.main()

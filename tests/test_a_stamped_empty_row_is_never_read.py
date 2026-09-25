"""A row stamped with its file's mtime and size but holding no metadata at all is one no
read ever made, and the refresh reads its file (docs/findings.md, #250).

Suggest makes a row for a photo the index never read: the path, raw_metadata `{}`, no
stamp (store.photos.ensure_row). A caption-only write, or a rotation under the code
before #247, stamped such a row, and the scan and the refresh trusted it from then on.
The refresh's "never read" asked for a row holding grouped names and no bare ones,
and passed over one holding nothing. A read always records something: the file's
SourceFile, and each field under both its names (tests/photo_rows.py).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from tagpup.services import refresh_rows  # noqa: E402
from tagpup.store import db, schema  # noqa: E402


class AStampedEmptyRow(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.db_path = home.library("harbour.db")
        schema.ensure(self.db_path)
        self.photo = os.path.join(home.root, "a.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"a photo's bytes")
        self.read = os.path.join(home.root, "b.jpg")
        with open(self.read, "wb") as handle:
            handle.write(b"another photo's bytes")
        stat = os.stat(self.photo)
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_unread(conn, self.photo)
            # What the damage left: stamped with the file's, and nothing read.
            conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE path = ?",
                         (stat.st_mtime, stat.st_size, self.photo))
            photo_rows.add_read(conn, self.read, {"EXIF:Model": "Harbour Cam"})
            conn.commit()
        finally:
            conn.close()

    def why(self, path):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            row = conn.execute("SELECT path, mtime, size, tags, captions, raw_metadata FROM photos WHERE path = ?",
                               (path,)).fetchone()
        finally:
            conn.close()
        return refresh_rows.why_stale(row)

    def test_is_never_read(self):
        self.assertTrue(refresh_rows.never_read("{}"))
        self.assertEqual(["never read"], self.why(self.photo))

    def test_a_row_the_indexer_made_is_not(self):
        self.assertEqual([], self.why(self.read))
        self.assertFalse(refresh_rows.never_read(json.dumps(photo_rows.as_read(self.read, {})["raw_metadata"])))


if __name__ == "__main__":
    unittest.main()

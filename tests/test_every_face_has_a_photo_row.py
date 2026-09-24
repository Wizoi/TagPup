"""A face is never recorded for a photo the library has no row for.

Suggest detects faces in photos it was shown but that were never indexed, and recorded
them against a path no row had: 859 such faces in photo_index, on 95 photos
(docs/findings.md, #42, #48). Phase 4 points faces at photos by id, which needs every
photo a face is on to have a row. The row made for it holds the path and nothing read
from the file: its mtime and size stay empty, so the folder scan and the refresh read
the file rather than trust an empty row.
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from index import PhotoIndex  # noqa: E402

from tagpup.store import checks, db  # noqa: E402


class EveryFaceHasAPhotoRow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_rows_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db_path = os.path.join(self.dir, "library.db")
        self.index = PhotoIndex(self.db_path)
        self.index.load()
        self.addCleanup(self.index.close)
        self.photo = os.path.join(self.dir, "never indexed.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"photo")

    def face(self):
        return {"box": [0, 0, 10, 10], "embedding": np.ones(8, dtype=np.float32), "prob": 0.99}

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT path, mtime, size FROM photos").fetchall()
        finally:
            conn.close()

    def test_a_face_suggest_found_on_a_photo_never_indexed_gets_its_photo_a_row(self):
        self.assertEqual(1, self.index.save_faces_if_absent(self.photo, [self.face()]))
        self.assertEqual([(self.photo, None, None)], self.rows())
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual(0, checks.faces_without_a_photo(conn).count)
        finally:
            conn.close()

    def test_a_photo_with_a_row_is_not_given_another(self):
        self.index.save_faces_if_absent(self.photo, [self.face()])
        self.index.save_faces_for_path(self.photo.upper() if os.name == "nt" else self.photo, [self.face()])
        self.assertEqual(1, len(self.rows()))


if __name__ == "__main__":
    unittest.main()

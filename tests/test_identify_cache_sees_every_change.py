"""Identify Faces' cache notices a rename or a reassignment, not only a new name.

The queue and the match lists are cached against a fingerprint of the faces table:
how many faces, how many named, the highest id, how many excluded. Renaming a person
changes none of those, and neither does moving a face from one person to another --
so after either, the screen went on showing the old names until something else
happened to move a count.

The database now keeps a generation counter that triggers bump on every change that
matters, whoever makes it: TagTuner, TagPup, the CLI or a script. Caching a face's
crop is not such a change, and must not throw the cache away.
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import tuner_server  # noqa: E402
from index import PhotoIndex  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"


class IdentifyCacheSeesEveryChange(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="identify_generation_")
        self.index = PhotoIndex(os.path.join(self.dir, "lib.db"))
        self.index.load()
        self.conn = self.index.conn
        self.conn.execute("INSERT INTO photos (path) VALUES (?)", (PHOTO,))
        emb = np.zeros(4, dtype=np.float32).tobytes()
        for name in ("Rowan Thackeray", "Rowan Thackeray", "Imogen Vale"):
            self.conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, '[0,0,1,1]', ?, ?)",
                (PHOTO, emb, name))
        self.conn.commit()

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def fingerprint(self):
        return tuner_server.TunerHTTPRequestHandler.faces_fingerprint(None, self.conn)

    def changes(self, sql, *params):
        before = self.fingerprint()
        self.conn.execute(sql, params)
        self.conn.commit()
        return before != self.fingerprint()

    def test_renaming_a_person_changes_the_fingerprint(self):
        self.assertTrue(self.changes(
            "UPDATE faces SET name = 'Rowan Thackeray-Vale' WHERE name = 'Rowan Thackeray'"))

    def test_moving_a_face_to_someone_else_changes_the_fingerprint(self):
        self.assertTrue(self.changes(
            "UPDATE faces SET name = 'Imogen Vale', name_source = 'manual'"
            " WHERE id = (SELECT MIN(id) FROM faces)"))

    def test_caching_a_crop_does_not(self):
        self.assertFalse(self.changes(
            "INSERT OR REPLACE INTO face_crops (face_id, jpeg) SELECT MIN(id), x'FFD8' FROM faces"))


if __name__ == "__main__":
    unittest.main()

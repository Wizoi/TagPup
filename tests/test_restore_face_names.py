"""restore_face_names.py puts back only names nothing has replaced, and never un-names a face.

It used to write every (id, name) in the backup, NULLs included: run a day after the
backup it would have un-named 748 faces named since and overwritten corrected names.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import restore_face_names  # noqa: E402
from index import PhotoIndex  # noqa: E402

PHOTO = r"D:\Pictures\Meets\2025-11 Classic\Meet - 01.jpg"


class RestoreFaceNames(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="restore_names_")
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.conn.execute(
            "INSERT INTO photos (path, tags, people, captions, raw_metadata) VALUES (?, '[]', '[]', '[]', '{}')",
            (PHOTO,))
        emb = np.zeros(4, dtype=np.float32).tobytes()
        # id: (name now, name_source, excluded)
        now = {
            1: (None, None, 0),               # cleared by a re-cluster: restore it
            2: ("Imogen Vale", "manual", 0),  # named since the backup: keep
            3: ("Tobin Marsh", None, 0),      # renamed since: conflict, keep
            4: (None, "manual", 0),           # a person said "nobody": keep
            5: (None, None, 1),               # excluded since: keep
            6: ("Quinn Adair", "manual", 0),  # unnamed in the backup: must stay named
        }
        for face_id, (name, source, excluded) in now.items():
            index.conn.execute(
                "INSERT INTO faces (id, photo_path, box, embedding, name, name_source, excluded)"
                " VALUES (?, ?, '[0,0,1,1]', ?, ?, ?, ?)", (face_id, PHOTO, emb, name, source, excluded))
        index.conn.commit()
        index.close()
        self.backup_file = os.path.join(self.dir, "backup.json")
        with open(self.backup_file, "w", encoding="utf-8") as fh:
            json.dump({"db": self.db, "taken": "then", "faces": [
                [1, "Rowan Thackeray"], [2, None], [3, "Wren Adair"],
                [4, "Rowan Thackeray"], [5, "Rowan Thackeray"], [6, None], [99, "Rowan Thackeray"],
            ]}, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_script(self, *extra):
        # Through the command line, as a person runs it.
        with mock.patch.object(restore_face_names, "backup", return_value="(skipped)", create=True), \
                mock.patch.object(sys, "argv", ["restore_face_names.py", self.backup_file, *extra]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            restore_face_names.main()
        return out.getvalue()

    def names(self):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return dict(conn.execute("SELECT id, name FROM faces"))
        finally:
            conn.close()

    def test_dry_run_changes_nothing(self):
        before = self.names()
        self.assertIn("Dry run", self.run_script())
        self.assertEqual(self.names(), before)

    def test_only_a_cleared_face_gets_its_name_back(self):
        out = self.run_script("--apply")
        self.assertEqual(self.names(), {
            1: "Rowan Thackeray", 2: "Imogen Vale", 3: "Tobin Marsh",
            4: None, 5: None, 6: "Quinn Adair"})
        self.assertIn("faces given back their name: 1", out)

    def test_the_photo_lists_the_restored_person(self):
        self.run_script("--apply")
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        people = json.loads(conn.execute("SELECT people FROM photos").fetchone()[0])
        conn.close()
        self.assertIn("Rowan Thackeray", people)

    def test_conflicts_are_listed(self):
        out = self.run_script()
        self.assertIn("'Wren Adair'", out)
        self.assertIn("'Tobin Marsh'", out)


if __name__ == "__main__":
    unittest.main()

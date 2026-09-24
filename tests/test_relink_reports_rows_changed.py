"""relink_renamed_photos.py reports the rows it changed, not the rows it planned.

It printed "re-pointed N row(s)" from the length of its plan. A plan whose rows had
gone -- or were spelled differently from what was planned -- reported success and
wrote nothing.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import relink_renamed_photos  # noqa: E402


class RelinkReportsRowsChanged(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="relink_")
        self.db = os.path.join(self.dir, "lib.db")
        conn = db.connect(self.db)
        conn.executescript("""
            CREATE TABLE photos (path TEXT PRIMARY KEY);
            CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_path TEXT, name TEXT);
        """)
        conn.execute("INSERT INTO photos VALUES (?)", (r"D:\Pictures\Run\2Z6A0001.jpg",))
        conn.executemany("INSERT INTO faces (photo_path, name) VALUES (?, ?)",
                         [(r"D:\Pictures\Run\2Z6A0001.jpg", "Rowan Thackeray"),
                          (r"D:\Pictures\Run\2Z6A0001.jpg", None)])
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_what_moved(self):
        moves = [
            {"from": r"D:\Pictures\Run\2Z6A0001.jpg", "to": r"D:\Pictures\Run\Run - 01.jpg"},
            {"from": r"D:\Pictures\Run\2Z6A0002.jpg", "to": r"D:\Pictures\Run\Run - 02.jpg"},
        ]
        # One row moved, of the two planned; the second had nothing to move.
        self.assertEqual(relink_renamed_photos.apply_moves(self.db, moves), (1, []))
        conn = db.connect(self.db)
        try:
            faces = conn.execute("SELECT COUNT(*) FROM faces WHERE photo_path = ?",
                                 (r"D:\Pictures\Run\Run - 01.jpg",)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(2, faces, "the faces did not come with the row")


if __name__ == "__main__":
    unittest.main()

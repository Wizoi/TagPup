"""relink_renamed_photos leaves a row where it is when the new name already has one.

It re-pointed each dead row at the renamed file with UPDATE ... WHERE path = ?, and
never looked at what the new name already held. A renamed photo browsed in TagPup under
its new name has faces saved there; re-pointing the old row brought its faces too, and
every face showed twice -- which is how 233 duplicate faces were made. Smart Rename
already moved rows through move_photo_rows, which checks; relink now does as well.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
import relink_renamed_photos  # noqa: E402
from index import PhotoIndex  # noqa: E402

OLD = r"D:\Pictures\Regatta\IMG_0001.jpg"
NEW = r"D:\Pictures\Regatta\Regatta - 01.jpg"


class RelinkChecksTheDestination(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="relink_dest_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        self.execute("INSERT INTO photos (path) VALUES (?)", (OLD,))
        self.execute("INSERT INTO faces (photo_path, box, name, name_source) VALUES (?, '[1,2,3,4]', 'Rowan Thackeray', 'manual')", (OLD,))

    def execute(self, sql, params=()):
        conn = tagpup_db.connect(self.db)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def count(self, sql, params=()):
        conn = tagpup_db.connect(self.db)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def test_a_destination_with_faces_already_is_left_alone(self):
        self.execute("INSERT INTO faces (photo_path, box) VALUES (?, '[1,2,3,4]')", (NEW,))

        moved, skipped = relink_renamed_photos.apply_moves(self.db, [{"from": OLD, "to": NEW}])

        self.assertEqual(0, moved)
        self.assertEqual([(OLD, NEW)], skipped)
        self.assertEqual(1, self.count("SELECT COUNT(*) FROM faces WHERE photo_path = ?", (NEW,)),
                         "the old row's faces were added to the ones already there")

    def test_a_free_destination_takes_the_row_and_its_faces(self):
        moved, skipped = relink_renamed_photos.apply_moves(self.db, [{"from": OLD, "to": NEW}])
        self.assertEqual((1, []), (moved, skipped))
        self.assertEqual(1, self.count("SELECT COUNT(*) FROM faces WHERE photo_path = ? AND name IS NOT NULL", (NEW,)))


if __name__ == "__main__":
    unittest.main()

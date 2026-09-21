"""The data paths behind Identify Faces, and what they are allowed to touch.

This screen is the one that meets a real library head on: 225,000 face rows, 189,000 of
them still nameless, and every button on it ran work proportional to all of them. The
tests here pin the first of the things that made it slow: the faces table had no
index the identify queries could use, so counting the excluded bucket read every row.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex


class TestFacesTableIsIndexedForIdentifying(unittest.TestCase):
    """The identify screen filters on `excluded` and `name`. An index has to cover it.

    Without one, `SELECT COUNT(*) FROM faces WHERE excluded = 1` -- which the sidebar
    asks for on every load -- scans 225,000 rows carrying a 2 KB embedding and a 6 KB
    JPEG crop apiece. Measured on the real library: 0.37s for a number that is almost
    always zero, and the queue asks four such questions before it draws anything.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_idx_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db = os.path.join(self.tmpdir, "photo_index.db")

    def indexes_on_faces(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            names = [r[1] for r in conn.execute("PRAGMA index_list(faces)")]
            return {
                n: [c[2] for c in conn.execute("PRAGMA index_info(%s)" % n)]
                for n in names
            }
        finally:
            conn.close()

    def test_a_new_database_is_created_with_the_identify_index(self):
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        indexes = self.indexes_on_faces(self.db)
        self.assertIn(
            "idx_faces_identify", indexes,
            "faces has no index covering the identify filter; every count scans the table",
        )
        self.assertEqual(
            ["excluded", "name"], indexes["idx_faces_identify"],
            "the index has to lead with `excluded` so a covering count is possible",
        )

    def test_an_existing_database_gains_the_index_on_open(self):
        """The libraries that need this most are the ones that already exist."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
        conn.execute("DROP INDEX IF EXISTS idx_faces_identify")
        conn.commit()
        conn.close()
        self.assertNotIn("idx_faces_identify", self.indexes_on_faces(self.db))

        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()
        self.assertIn(
            "idx_faces_identify", self.indexes_on_faces(self.db),
            "opening an older database did not add the index it is missing",
        )

    def test_the_excluded_count_is_answered_from_the_index_alone(self):
        """A covering index means the count never reaches the row, crop and all."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
        try:
            plan = " ".join(
                r[3] for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM faces WHERE excluded = 1")
            )
        finally:
            conn.close()
        self.assertIn("idx_faces_identify", plan, "plan was: %s" % plan)
        self.assertIn(
            "COVERING", plan.upper(),
            "the count still reaches the table rows; plan was: %s" % plan,
        )


if __name__ == "__main__":
    unittest.main()

"""The doctor watches the tables phase 4 derived from photos: vectors, people and
suggestions each belong to a photo, and a trigger takes them with it. A row whose photo
is gone -- written past the store, or on a copy restored without its triggers -- is a
rule broken. A photo with no vector for the configured model is reported, not broken.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_vector  # noqa: E402

from tagpup.store import checks, db, schema  # noqa: E402

GONE = 999


class DerivedTables(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="doctor_derived_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db_path = os.path.join(self.dir, "library.db")
        schema.ensure(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def broken(self):
        return {check.name: check.count for check in checks.run(self.conn) if check.count}

    def test_a_new_library_breaks_none_of_them(self):
        self.assertEqual({}, self.broken())

    def test_a_vector_whose_photo_is_gone(self):
        self.conn.execute("INSERT INTO embeddings (photo_id, model, vector) VALUES (?, 'm', x'00')", (GONE,))
        self.assertEqual({"vectors whose photo is gone": 1}, self.broken())

    def test_people_listed_for_a_photo_that_is_gone(self):
        self.conn.execute("INSERT INTO photo_people (photo_id, position, name, source)"
                          " VALUES (?, 0, 'Oda Castellane', 'keyword')", (GONE,))
        self.assertEqual({"people listed for a photo that is gone": 1}, self.broken())

    def test_suggestions_for_a_photo_that_is_gone(self):
        self.conn.execute("INSERT INTO suggestions (photo_id, tags) VALUES (?, '[]')", (GONE,))
        self.assertEqual({"suggestions for a photo that is gone": 1}, self.broken())

    def test_photos_without_a_vector_for_the_model_are_counted(self):
        for name in ("a.jpg", "b.jpg"):
            self.conn.execute("INSERT INTO photos (path, mtime, size) VALUES (?, 1, 1)", (os.path.join(self.dir, name),))
        add_vector(self.conn, os.path.join(self.dir, "a.jpg"), b"\x00" * 16, model="m")
        self.assertEqual(1, checks.without_a_vector(self.conn, "m"))
        self.assertEqual(2, checks.without_a_vector(self.conn, "another model"))


if __name__ == "__main__":
    unittest.main()

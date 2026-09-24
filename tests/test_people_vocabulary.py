"""A taxonomy read once resolves people exactly as a read per photo did.

extract_people read the face roots and every face tag from the database for each
photo, then scanned all of them per keyword: 30s over a 68,000-photo library. Anything
resolving many photos -- clustering above all -- now loads a PeopleVocabulary once.
On both real libraries the two agree on every one of 69,194 photos; these pin it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
from metadata import PeopleVocabulary, extract_people  # noqa: E402


class PeopleVocabularyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "lib.db")
        conn = tagpup_db.connect(self.db_path)
        conn.execute("CREATE TABLE tag_taxonomy (id INTEGER PRIMARY KEY, tag TEXT, name TEXT,"
                     " parent_id INTEGER, has_face INTEGER)")
        conn.executemany(
            "INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face) VALUES (?, ?, ?, ?, ?)",
            [(1, "Pets", "Pets", None, 1),
             (2, "Pets/Biscuit", "Biscuit", 1, 1),
             (3, "People", "People", None, 1),
             (4, "People/Imogen Vale", "Imogen Vale", 3, 1),
             (5, "Activity", "Activity", None, 0),
             (6, "Activity/Rowing", "Rowing", 5, 0)])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    CASES = [
        ["Pets/Biscuit"],                 # a root only the taxonomy knows
        ["Imogen Vale"],                  # a bare leaf
        ["people/imogen vale"],           # case
        ["Pets", "People"],               # roots alone name nobody
        ["Activity/Rowing", "Rowing"],    # not a face tag
        ["Family/Immediate/Tamsin Oakes"],  # a root this taxonomy does not have
    ]

    def test_a_vocabulary_resolves_as_a_database_read_does(self):
        vocabulary = PeopleVocabulary.load(self.db_path)
        for tags in self.CASES:
            with self.subTest(tags=tags):
                self.assertEqual(extract_people({}, tags, db_path=self.db_path),
                                 extract_people({}, tags, vocabulary=vocabulary))

    def test_what_it_resolves(self):
        vocabulary = PeopleVocabulary.load(self.db_path)
        self.assertEqual(["Biscuit"], extract_people({}, ["Pets/Biscuit"], vocabulary=vocabulary))
        self.assertEqual(["Imogen Vale"], extract_people({}, ["Imogen Vale"], vocabulary=vocabulary))
        self.assertEqual([], extract_people({}, ["Pets", "People"], vocabulary=vocabulary))

    def test_without_a_library_only_a_new_librarys_people_root_counts(self):
        # docs/findings.md, #66: Family and Friends counted too, whatever a tree said.
        self.assertEqual([], extract_people({}, ["Pets/Biscuit", "Imogen Vale"]))
        self.assertEqual([], extract_people({}, ["Family/Immediate/Tamsin Oakes"]))
        self.assertEqual(["Tamsin Oakes"], extract_people({}, ["People/Immediate/Tamsin Oakes"]))


if __name__ == "__main__":
    unittest.main()

"""A library always has a root that holds faces.

Which roots hold faces is the tree's flag and nothing else (docs/findings.md, #66). A new
library is given People; taking away the last face root -- deleting it, retiring it,
merging it into a word, or switching its flag off -- would leave every person in the
library a word. The owner chose to refuse that (2026-09-24): flag another root first.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import tags  # noqa: E402
from tagpup.store import db, taxonomy  # noqa: E402


class ALibraryKeepsAFaceRoot(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.write(lambda conn: taxonomy.add_path(conn, "People/Wren Halloway", root_has_face=1))
        self.write(lambda conn: taxonomy.add_path(conn, "Activity/Harbour Walk"))

    def write(self, change):
        db.write_with_connection(self.lib.library.path, change)

    def node(self, tag):
        return taxonomy.find(self.lib.library.path, tag)["id"]

    def face_roots(self):
        return taxonomy.face_roots(self.lib.library.path)

    def test_its_last_face_root_keeps_its_flag(self):
        result = tags.set_flags(self.lib.library, self.node("People"), has_face=0)
        self.assertIn("only root that holds faces", result.refused)
        self.assertEqual(["People"], self.face_roots())

    def test_its_last_face_root_is_not_deleted(self):
        result = tags.delete(self.lib.library, self.node("People"), "remove", None, None)
        self.assertIn("only root that holds faces", result.refused)
        self.assertIsNotNone(taxonomy.find(self.lib.library.path, "People"))

    def test_its_last_face_root_is_not_retired(self):
        result = tags.merge(self.lib.library, "People", None, None, retire=True, apply=True)
        self.assertIn("only root that holds faces", result.refused)
        self.assertEqual(["People"], self.face_roots())

    def test_its_last_face_root_is_not_merged_into_a_word(self):
        result = tags.merge(self.lib.library, "People", "Activity", None, apply=True)
        self.assertIn("only root that holds faces", result.refused)

    def test_with_another_face_root_it_may_go(self):
        self.write(lambda conn: taxonomy.add_path(conn, "Crew", root_has_face=1))
        result = tags.set_flags(self.lib.library, self.node("People"), has_face=0)
        self.assertFalse(result.refused)
        self.assertEqual(["Crew"], self.face_roots())

    def test_a_word_root_may_go_whatever_the_face_roots(self):
        result = tags.delete(self.lib.library, self.node("Activity"), "remove", None, None)
        self.assertFalse(result.refused)


if __name__ == "__main__":
    unittest.main()

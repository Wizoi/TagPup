"""Which roots hold faces is said by the tree, and by nothing else.

Five lists of root names in the code said it as well, disagreeing with the tree and with
each other: a new root called Family was made holding faces, a library that never
flagged Family still had Family people, and the suggester and the CLI left People out
(docs/findings.md, #66). A new library is given one face root, People.

A library also always keeps one: taking away the last face root -- deleting it, retiring it, merging it into a word, or
switching its flag off -- would leave every person in the library a word. The owner chose to refuse that (2026-09-24):
flag another root first.
"""
import os
import shutil
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary

from tagpup.core import vocabulary
from tagpup.services import tags
from tagpup.store import db, schema, taxonomy


class FaceRootsAreTheTrees(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_roots_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db_path = os.path.join(self.dir, "library.db")
        schema.ensure(self.db_path)

    def write(self, change):
        db.write_with_connection(self.db_path, change)

    def flags(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return dict(conn.execute("SELECT tag, has_face FROM tag_taxonomy WHERE parent_id IS NULL"))
        finally:
            conn.close()

    def vocabulary(self):
        return taxonomy.people_vocabulary(self.db_path)

    def test_a_new_library_has_one_face_root(self):
        taxonomy.seed(self.db_path)
        flags = self.flags()
        self.assertEqual(1, flags["People"])
        self.assertEqual({0}, {flag for tag, flag in flags.items() if tag != "People"})

    def test_a_root_is_not_made_holding_faces_for_its_name(self):
        self.write(lambda conn: taxonomy.add_path(conn, "Family/Wren Halloway"))
        self.assertEqual(0, self.flags()["Family"])

    def test_a_keyword_under_a_root_the_tree_does_not_flag_names_nobody(self):
        self.write(lambda conn: taxonomy.add_path(conn, "Friends/Ansel Ditmore"))
        people = vocabulary.extract_people(["Friends/Ansel Ditmore"], self.vocabulary())
        self.assertEqual([], people)

    def test_a_keyword_under_a_root_the_tree_flags_names_its_leaf(self):
        self.write(lambda conn: taxonomy.add_path(conn, "Crew/Ansel Ditmore", root_has_face=1))
        people = vocabulary.extract_people(["Crew/Ansel Ditmore"], self.vocabulary())
        self.assertEqual(["Ansel Ditmore"], people)

    def test_everyone_under_a_flagged_people_root_is_a_person_to_the_indexer(self):
        # The suggester and the CLI listed family, friends and pets, and not people.
        self.write(lambda conn: taxonomy.add_path(conn, "People/Wren Halloway", root_has_face=1))
        tree = taxonomy.TagTaxonomy(self.db_path)
        tree.load()
        self.assertIn("people", tree.people_roots())
        self.assertNotIn("family", tree.people_roots())

    def test_an_empty_tree_is_a_new_librarys(self):
        # The indexer resolves a new library's people before it first saves its tree.
        people = vocabulary.extract_people(["People/Wren Halloway"], self.vocabulary())
        self.assertEqual(["Wren Halloway"], people)

    def test_a_caption_files_people_by_the_trees_roots(self):
        # The writer listed family and friends, and put everyone under People among the
        # others.
        from tagpup.core.suggesting import caption_from_tags
        self.write(lambda conn: taxonomy.add_path(conn, "People/Wren Halloway", root_has_face=1))
        roots = taxonomy.people_vocabulary(self.db_path).roots
        self.assertEqual("Wren Halloway - Harbour Walk",
                         caption_from_tags(["People/Wren Halloway", "Activity/Harbour Walk"], roots))


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
        self.write(lambda conn: taxonomy.add_path(conn, "Crew/Divers Club", root_has_face=1))   # a root with someone under it
        result = tags.set_flags(self.lib.library, self.node("People"), has_face=0)
        self.assertFalse(result.refused)
        self.assertEqual(["Crew"], self.face_roots())

    def test_a_word_root_may_go_whatever_the_face_roots(self):
        result = tags.delete(self.lib.library, self.node("Activity"), "remove", None, None)
        self.assertFalse(result.refused)


if __name__ == "__main__":
    unittest.main()

"""tagpup.store.people.names: the people the pages offer while a name is typed.

TagPup lists the names given to faces; TagTuner adds the people keywords name, and can
be asked for the hidden ones too. Both had a copy of the rest.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402
from face_rows import add_people  # noqa: E402

from tagpup.store import db, people  # noqa: E402


class ThePeopleALibraryKnows(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("reunion.jpg")
        self.lib.add_row(self.photo)
        self.node_ids = {}
        self.file("People", has_face=1)

    def face(self, name):
        self.lib.add_face(self.photo, [0, 0, 10, 10], name=name)

    def file(self, tag, has_face=1, hidden=0):
        """A node in the tag tree, under its parent."""
        parent = self.node_ids.get(tag.rsplit("/", 1)[0]) if "/" in tag else None
        self.node_ids[tag] = self.lib.execute(
            "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face, hidden_from_autocomplete)"
            " VALUES (?, ?, ?, ?, ?)", (tag, tag.rsplit("/", 1)[-1], parent, has_face, hidden))

    def names(self, **options):
        return people.names(self.lib.library.path, **options)

    def test_the_names_given_to_faces_sorted_once_each(self):
        for name in ("Wren Halloway", "Ada Pembrook", "Wren Halloway", None):
            self.face(name)
        self.assertEqual(self.names(), ["Ada Pembrook", "Wren Halloway"])

    def test_the_people_keywords_name_only_when_asked(self):
        self.face("Ada Pembrook")
        conn = db.connect(self.lib.library.path)
        try:
            add_people(conn, self.photo, ["Milo Garrick", ""])
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.names(), ["Ada Pembrook"])
        self.assertEqual(self.names(keywords_too=True), ["Ada Pembrook", "Milo Garrick"])

    def test_a_person_hidden_from_autocomplete_is_left_out_unless_asked_for(self):
        self.face("Ada Pembrook")
        self.face("Wren Halloway")
        self.file("People/Wren Halloway", hidden=1)
        self.assertEqual(self.names(), ["Ada Pembrook"])
        self.assertEqual(self.names(include_hidden=True), ["Ada Pembrook", "Wren Halloway"])

    def test_so_is_one_filed_in_a_hidden_branch(self):
        self.face("Wren Halloway")
        self.file("People/Archive", hidden=1)
        self.file("People/Archive/Wren Halloway")
        self.assertEqual(self.names(), [])

    def test_one_filed_twice_is_offered_while_either_place_is_visible(self):
        self.face("Wren Halloway")
        self.file("People/Wren Halloway", hidden=1)
        self.file("Family")
        self.file("Family/Wren Halloway")
        self.assertEqual(self.names(), ["Wren Halloway"])

    def test_a_node_that_holds_no_faces_hides_nobody(self):
        self.face("Wren Halloway")
        self.file("Places", has_face=0)
        self.file("Places/Wren Halloway", has_face=0, hidden=1)
        self.assertEqual(self.names(), ["Wren Halloway"])

    def test_a_person_the_tree_does_not_file_is_offered(self):
        self.face("Ada Pembrook")
        self.assertEqual(self.names(), ["Ada Pembrook"])

    def test_asking_of_a_library_that_is_not_there_does_not_create_it(self):
        missing = os.path.join(self.lib.root, "missing.db")
        self.assertEqual(people.names(missing), [])
        self.assertFalse(os.path.exists(missing))


if __name__ == "__main__":
    unittest.main()

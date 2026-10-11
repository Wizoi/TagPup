"""A person is a node marked as a person with no node under it, wherever it sits (owner, 2026-10-10, "every tag has an
id"): one directly under the tree's root is as much a person as People/Wren Halloway. A node with nodes under it is a group
(never a person), and the root of a branch that holds people is that group.

Before, a top-level node was a *category* by its place alone, so a name given its own top-level node would have named nobody.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import db, faces, people, person_ids, taxonomy  # noqa: E402

SOLO = "Ines Calloway"


class TopLevelPerson(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.solo_id = people_by_id.write(self.path, self.make_solo)

    def make_solo(self, conn):
        with people.tree_edit(conn):
            return taxonomy.add_path(conn, SOLO, root_has_face=1)

    def read(self, operation):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return operation(conn)
        finally:
            conn.close()

    def test_a_top_level_node_that_holds_faces_and_has_none_under_it_is_a_person(self):
        found = self.read(lambda conn: person_ids.resolve(conn, SOLO))
        self.assertEqual((self.solo_id, SOLO, SOLO), tuple(found))
        self.assertEqual(found, self.read(lambda conn: person_ids.resolve(conn, self.solo_id)))

    def test_the_root_of_a_branch_is_still_a_group_and_no_person(self):
        with self.assertRaises(person_ids.GroupNotPerson):
            self.read(lambda conn: person_ids.resolve(conn, "Family"))

    def test_a_new_librarys_empty_people_root_is_where_people_are_filed_and_no_person(self):
        def empty(conn):
            taxonomy.remove_node(conn, people_by_id.WREN)
        people_by_id.write(self.path, empty)
        self.assertIsNone(self.read(lambda conn: person_ids.resolve(conn, "People")))
        self.assertNotIn("People", taxonomy.people_paths(self.path))
        self.assertEqual([], vocabulary.extract_refs({}, ["People"], self.read(taxonomy.read_people_vocabulary)))

    def test_a_top_level_person_becomes_a_group_when_a_node_goes_under_it(self):
        def under(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, SOLO + "/Swim Team")
        people_by_id.write(self.path, under)
        with self.assertRaises(person_ids.GroupNotPerson):
            self.read(lambda conn: person_ids.resolve(conn, SOLO))

    def test_a_keyword_that_is_a_top_level_person_names_them(self):
        known = self.read(taxonomy.read_people_vocabulary)
        refs = vocabulary.extract_refs({}, [SOLO.lower()], known)
        self.assertEqual([vocabulary.Ref(self.solo_id, SOLO)], refs)
        self.assertEqual([], vocabulary.extract_refs({}, ["Family"], known))

    def test_a_photo_carrying_the_top_level_person_lists_them(self):
        def add(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, add)
        self.assertEqual([(self.solo_id, SOLO, "keyword")], self.listed(self.other))

    def test_a_node_may_not_be_put_under_a_top_level_person_a_photo_carries(self):
        def carry(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, carry)
        with self.assertRaises(person_ids.PersonHasNoChildren):
            self.read(lambda conn: taxonomy.refuse_child_of_person(conn, SOLO + "/Swim Team"))

    def test_the_filing_of_people_lists_the_top_level_person_by_their_path(self):
        filed, _roots = taxonomy.people_filing(self.path)
        self.assertEqual([SOLO], filed[vocabulary.key(SOLO)])
        self.assertEqual(SOLO, taxonomy.people_paths(self.path)[vocabulary.key(SOLO)])

    def test_the_navigator_files_them_at_the_top_level_and_not_as_unfiled(self):
        from tagpup.services import library_view
        def add(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, add)
        found = library_view.navigator(self.library, "people")
        self.assertEqual(0, found["unfiled"])
        self.assertIn(("Top level", 1), [(group["tag"], group["count"]) for group in found["groups"]])
        self.assertEqual("Top level", [each for each in found["people"] if each["name"] == SOLO][0]["group"])

    def test_faces_can_name_them(self):
        def name(conn):
            return faces.name(conn, [self.faces[0]], self.solo_id)
        self.assertEqual(1, people_by_id.write(self.path, name))


if __name__ == "__main__":
    unittest.main()

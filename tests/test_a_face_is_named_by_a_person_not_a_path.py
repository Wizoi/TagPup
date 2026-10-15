"""The store refuses to name a face with a tag's path that is no person's: `a/b` once made nodes `a` and `a/b` (has_face 0) and
wrote the face pointing at a node that is not a person. A person's own path (an id's tag) still names them; a bare name still
gives its node (tests/people_by_id.py: the library of two people called alike).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402

from tagpup.store import faces  # noqa: E402


class FaceNamedByAPath(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()

    def nodes(self):
        return people_by_id.look(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0]

    def test_a_path_no_person_has_makes_no_node_and_names_no_face(self):
        before = self.nodes()
        with self.assertRaises(ValueError):
            people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], "Vessels/Gull"))
        self.assertEqual(before, self.nodes())
        self.assertEqual((None, None), self.face(self.faces[0])[:2])

    def test_a_persons_path_and_a_new_bare_name_still_name_a_face(self):
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], people_by_id.WREN))
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[1]], "Zed Quill"))
        self.assertEqual((self.node(people_by_id.WREN), "Wren Halloway"), self.face(self.faces[0])[:2])
        self.assertEqual((self.node("Zed Quill"), "Zed Quill"), self.face(self.faces[1])[:2])


if __name__ == "__main__":
    unittest.main()

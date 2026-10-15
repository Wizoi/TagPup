"""A listed person whose node is gone is not listed: whichever door settles it (repair, or the photos' own follow), the row is
deleted, as follow_tree and release delete it -- never kept as a name with no id (owner, 2026-10-11: a row has the id or is not
a person). A face of that node keeps its name and loses the id (the doctor lists it).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402

from tagpup.store import faces, person_ids  # noqa: E402


class ListedPersonOfAGoneNode(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.wren = self.node(people_by_id.WREN)

        def seed(conn):
            # A row as a snapshot or an older writer left it: the node it holds was removed without following.
            conn.execute("INSERT INTO photo_people (photo_id, position, name, source, tag_id)"
                         " SELECT id, 5, 'Wren Halloway', 'keyword', ? FROM photos WHERE path = ?", (self.wren, self.photo))
            faces.name(conn, [self.faces[0]], self.wren)
            conn.execute("DROP TRIGGER person_tag_not_deleted_while_named")   # a library from before the guard
            conn.execute("DELETE FROM tag_taxonomy WHERE id = ?", (self.wren,))
        people_by_id.write(self.path, seed)

    def test_repair_deletes_the_row_and_leaves_the_face_a_name(self):
        self.assertEqual({"faces": 1, "photo_people": 1}, person_ids.repair(self.path))
        self.assertEqual(["Sam"], [name for _id, name, _source in self.listed(self.photo)])
        self.assertEqual((None, "Wren Halloway"), self.face(self.faces[0])[:2])

    def test_the_photos_own_follow_deletes_the_row(self):
        def follow(conn):
            return person_ids.follow_listed(conn, [people_by_id.look(self.path, "SELECT id FROM photos WHERE path = ?",
                                                                     (self.photo,))[0][0]])
        self.assertEqual(1, people_by_id.write(self.path, follow))
        self.assertEqual(["Sam"], [name for _id, name, _source in self.listed(self.photo)])


if __name__ == "__main__":
    unittest.main()

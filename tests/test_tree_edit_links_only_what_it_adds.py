"""People by id, stage 2, fix round 1: after an edit of the tree, which rows that hold a name and no id may be linked to a
person (docs/ARCHITECTURE.md, "People by id, stage 2", "Part B as built"). ONE rule (`person_ids.follow_tree`): only the rows of a
name the edit newly gave a person -- a node made, moved or renamed INTO that name.
A name that became exactly one person's because a same-named node LEFT (renamed away, merged, force-deleted) was ambiguous before
and stays unresolved, a hand decision included: it is the owner's to settle in the review list, not guessed.

Two people called Max (a pet and a friend); a hand decision "Max" with no id (a migration-21 row, or a face named while the name
was ambiguous), an automatic one, and a keyword row, each as that state leaves them. Fictional names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import MAX_FRIEND, MAX_PET, TwoSams, look, write  # noqa: E402

from tagpup.store import faces, people, taxonomy  # noqa: E402


class Edits(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.max_pet, self.max_friend = self.node(MAX_PET), self.node(MAX_FRIEND)

        def unresolved(conn):
            self.manual = faces.insert(conn, self.other, [40, 0, 50, 10], b"\x03" * 8)
            self.automatic = faces.insert(conn, self.other, [60, 0, 70, 10], b"\x04" * 8)
            conn.execute("UPDATE faces SET name = 'Max', tag_id = NULL, name_source = 'manual' WHERE id = ?", (self.manual,))
            conn.execute("UPDATE faces SET name = 'Max', tag_id = NULL, name_source = NULL WHERE id = ?", (self.automatic,))
            photo = conn.execute("SELECT id FROM photos WHERE path = ?", (self.other,)).fetchone()[0]
            conn.execute("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, 0, 'Max', 'keyword', NULL)",
                         (photo,))

        write(self.path, unresolved)

    def rows(self):
        return (self.face(self.manual), self.face(self.automatic), self.listed(self.other))

    def unchanged(self):
        self.assertEqual(((None, "Max", "manual"), (None, "Max", None), [(None, "Max", "keyword")]), self.rows())

    def test_a_node_renamed_away_makes_the_other_unique_and_links_nothing(self):
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_PET, "Pets/Rex"))
        self.unchanged()

    def test_a_node_moved_to_another_leaf_name_links_nothing_of_the_one_left(self):
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_FRIEND, "Friends/Rowan Thackeray"))
        self.unchanged()

    def test_a_merge_links_nothing(self):
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_PET, MAX_FRIEND))
        self.unchanged()

    def test_a_force_delete_of_one_of_two_links_nothing(self):
        write(self.path, lambda conn: taxonomy.delete_branch(conn, MAX_PET, force=True))
        self.unchanged()

    def test_a_person_added_under_a_name_nobody_had_links_its_unresolved_rows_the_hand_decision_too(self):
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_PET, "Pets/Rex"))
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_FRIEND, "Friends/Rowan Thackeray"))

        def add(conn):
            with people.tree_edit(conn):   # as TagTuner's tree view makes a node
                taxonomy.add_path(conn, "Friends/Max")

        write(self.path, add)
        new = self.node("Friends/Max")
        self.assertEqual((new, "Max", "manual"), self.face(self.manual), "added for that name: the decision was to it")
        self.assertEqual((new, "Max", None), self.face(self.automatic))
        self.assertEqual([(new, "Max", "keyword")], self.listed(self.other))
        self.assertEqual(0, look(self.path, "SELECT COUNT(*) FROM faces WHERE name = 'Max' AND tag_id IS NULL")[0][0])


if __name__ == "__main__":
    unittest.main()

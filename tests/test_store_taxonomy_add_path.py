"""tagpup.store.taxonomy.add_path: the one writer of new nodes in the tag tree.

There were five, each with its own copy of which new roots hold faces
(docs/findings.md, #39). The second test class keeps it the only one.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402

from tagpup.store import db  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402


class AddingAPath(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)

    def add(self, path, **options):
        return db.write_with_connection(
            self.lib.library.path, lambda conn: taxonomy.add_path(conn, path, **options))

    def nodes(self):
        """tag -> (parent's tag, name, has_face), for every node."""
        rows = self.lib.rows("SELECT id, tag, parent_id, name, has_face FROM tag_taxonomy")
        tags = {node_id: tag for node_id, tag, _, _, _ in rows}
        return {tag: (tags.get(parent_id), name, has_face) for _, tag, parent_id, name, has_face in rows}

    def test_each_missing_level_is_made_below_the_last(self):
        node = self.add("Crew/Divers/Jane Olsen")
        self.assertEqual(self.nodes(), {
            "Crew": (None, "Crew", 0),
            "Crew/Divers": ("Crew", "Divers", 0),
            "Crew/Divers/Jane Olsen": ("Crew/Divers", "Jane Olsen", 0),
        })
        self.assertEqual(self.lib.rows("SELECT tag FROM tag_taxonomy WHERE id = ?", (node,)),
                         [("Crew/Divers/Jane Olsen",)])

    def test_the_tag_is_spelled_as_the_tree_spells_it(self):
        self.add(" Trips | Boston MA / Harbor ")
        self.assertEqual(sorted(self.nodes()), ["Trips", "Trips/Boston MA", "Trips/Boston MA/Harbor"])

    def test_a_root_is_not_made_holding_faces_for_its_name(self):
        # docs/findings.md, #66: a new root named People, Family, Friends or Pets was
        # flagged for its name. Only the tree says which roots hold faces.
        for root in ("People", "family", "Friends", "Pets"):
            self.add(root + "/Someone/Anyone")
        self.assertFalse(any(has_face for _, _, has_face in self.nodes().values()))

    def test_all_below_a_root_asked_to_hold_faces_holds_them(self):
        self.add("People/Someone/Anyone", root_has_face=1)
        self.assertTrue(all(has_face for _, _, has_face in self.nodes().values()))

    def test_a_new_root_holds_faces_when_asked(self):
        self.add("Crew/Divers", root_has_face=1)
        self.assertEqual({tag: row[2] for tag, row in self.nodes().items()},
                         {"Crew": 1, "Crew/Divers": 1})

    def test_being_asked_does_not_change_a_root_that_is_there(self):
        self.add("Crew")
        self.add("Crew/Divers", root_has_face=1)
        self.assertEqual({tag: row[2] for tag, row in self.nodes().items()},
                         {"Crew": 0, "Crew/Divers": 0})

    def test_a_new_node_takes_its_parents_flag(self):
        self.add("Crew", root_has_face=1)
        self.lib.execute("UPDATE tag_taxonomy SET has_face = 0 WHERE tag = 'Crew'")
        self.add("Crew/Divers")
        self.assertEqual(self.nodes()["Crew/Divers"][2], 0)

    def test_a_path_already_there_is_its_node(self):
        first = self.add("Crew/Divers")
        self.assertEqual(self.add("Crew / Divers"), first)
        self.assertEqual(len(self.nodes()), 2)

    def test_a_tag_with_no_levels_is_nothing(self):
        self.assertIsNone(self.add(" / "))
        self.assertEqual(self.nodes(), {})


class NothingElseMakesNodes(unittest.TestCase):
    INSERT = re.compile(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+tag_taxonomy", re.IGNORECASE)

    def test_only_the_store_inserts_into_the_tag_tree(self):
        writers = []
        for name in python_sources():
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                if self.INSERT.search(f.read()):
                    writers.append(name.replace(os.sep, "/"))
        self.assertEqual(writers, ["tagpup/store/taxonomy.py"])


if __name__ == "__main__":
    unittest.main()

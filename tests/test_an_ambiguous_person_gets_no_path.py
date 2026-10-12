"""A name filed under two people paths has both (docs/findings.md, #27); a face's person is suggested under the path of their
node, and a face whose name has no node under none (owner, 2026-10-11).

A lookup of a person's path returned the first path it met in a set, so a face of a person filed
twice was suggested under one path on one run and the other on the next. The suggester now takes the tag of the person's node.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import _root  # noqa: E402,F401
from tagpup.core import clustering  # noqa: E402
from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services.suggester import TagSuggester  # noqa: E402
from tagpup.store.taxonomy import TagTaxonomy  # noqa: E402

TWICE = {"People/Harbour/Rowan Thackeray", "People/School/Rowan Thackeray", "People/Imogen Vale"}


def taxonomy(paths):
    # A library that does not exist: its people root is People, and nothing is opened.
    tree = TagTaxonomy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "no such library.db"))
    tree.paths = set(paths)
    return tree


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class Index:
    conn = None
    db_path = "no such library.db"

    def search(self, embedding, k=15):
        return []


class OneFace:
    def detect_and_embed_faces(self, path):
        return [{"box": [0, 0, 100, 100], "embedding": unit([1, 0, 0]).tolist(), "prob": 0.99}]


class FindingAPerson(unittest.TestCase):
    def test_a_name_filed_twice_has_both_paths(self):
        self.assertEqual(["People/Harbour/Rowan Thackeray", "People/School/Rowan Thackeray"],
                         taxonomy(TWICE).person_paths("Rowan Thackeray"))

    def test_a_name_filed_once_has_its_path(self):
        self.assertEqual(["People/Imogen Vale"], taxonomy(TWICE).person_paths("imogen vale"))

    def test_a_name_filed_twice_is_not_filed_a_third_time(self):
        tree = taxonomy(TWICE)
        tree.add_tag("Rowan Thackeray")
        self.assertEqual(TWICE, tree.paths)


class SuggestingAPerson(unittest.TestCase):
    def suggested(self, tags_by_id, person_id):
        """The tags suggested for a face like the decided face of the person `person_id` (a node of the tree, or not)."""
        known = clustering.KnownFaces.of([(person_id, unit([1, 0, 0]), None, r"D:\Pictures\Before\a.jpg",
                                           Ref(person_id, "Imogen Vale"))])
        run = TagSuggester(Index(), taxonomy(TWICE), faces=OneFace())
        with mock.patch.object(face_records, "known_faces", return_value=known), \
                mock.patch.object(TagSuggester, "_tags_by_id", return_value=tags_by_id), \
                mock.patch.object(face_records, "record_detected", return_value=0):
            result = run.suggest_for_photo(r"D:\Pictures\Run\01.jpg", [1.0, 0.0, 0.0])
        return [t["tag"] for t in result["suggested_tags"]]

    def test_a_face_of_a_person_the_tree_files_is_offered_under_their_path(self):
        self.assertIn("People/Imogen Vale", self.suggested({7: "People/Imogen Vale", 8: "People/School/Imogen Vale"}, 7))
        self.assertIn("People/School/Imogen Vale", self.suggested({7: "People/Imogen Vale", 8: "People/School/Imogen Vale"}, 8))

    def test_a_face_of_a_name_no_node_has_is_offered_under_no_path(self):
        self.assertEqual([], [t for t in self.suggested({}, 9) if t.endswith("Imogen Vale")])


if __name__ == "__main__":
    unittest.main()

"""A name filed under two people paths is resolved to neither (docs/findings.md, #27).

find_person_path returned the first path it met in a set, so a face of a person filed
twice was suggested under one path on one run and the other on the next. It now
refuses the name as find_by_leaf does, and the suggester offers no path for it.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import _root  # noqa: E402,F401
from tagpup.core import clustering  # noqa: E402
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
    def test_a_name_filed_twice_has_no_path(self):
        self.assertIsNone(taxonomy(TWICE).find_person_path("Rowan Thackeray"))

    def test_a_name_filed_once_has_its_path(self):
        self.assertEqual("People/Imogen Vale", taxonomy(TWICE).find_person_path("imogen vale"))

    def test_a_name_filed_twice_is_not_filed_a_third_time(self):
        tree = taxonomy(TWICE)
        tree.add_people(["Rowan Thackeray"])
        tree.add_tag("Rowan Thackeray")
        self.assertEqual(TWICE, tree.paths)


class SuggestingAPerson(unittest.TestCase):
    def suggested(self, paths, name):
        known = clustering.KnownFaces.of([(name, unit([1, 0, 0]), None, r"D:\Pictures\Before\a.jpg")])
        run = TagSuggester(Index(), taxonomy(paths), faces=OneFace())
        with mock.patch.object(face_records, "known_faces", return_value=known), \
                mock.patch.object(face_records, "record_detected", return_value=0):
            result = run.suggest_for_photo(r"D:\Pictures\Run\01.jpg", [1.0, 0.0, 0.0])
        return [t["tag"] for t in result["suggested_tags"]]

    def test_a_face_of_a_name_filed_twice_is_offered_under_no_path(self):
        tags = self.suggested(TWICE, "Rowan Thackeray")
        self.assertEqual([], [t for t in tags if t.endswith("Rowan Thackeray")])

    def test_a_face_of_a_name_filed_once_is_offered_under_its_path(self):
        self.assertIn("People/Imogen Vale", self.suggested(TWICE, "Imogen Vale"))


if __name__ == "__main__":
    unittest.main()

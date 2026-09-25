"""Who a face is, and whether its name looks right, is decided in one place:
tagpup.core.clustering -- KnownFaces, and is_offered, names_unasked and looks_wrong.

A face was compared with a person's mean face in clustering, their geometric median in
TagTuner's grid, and a mean with no years in the suggester, each against a value of its
own; the grid flagged half of every correctly named face (docs/findings.md, #71, #75).
"""
import os
import re
import unittest

import numpy as np

from tagpup.core import clustering

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def face(*values):
    vector = np.zeros(8, dtype=np.float32)
    vector[:len(values)] = values
    return vector


class TheDecisions(unittest.TestCase):
    def test_are_the_values_the_owner_chose(self):
        self.assertTrue(clustering.is_offered(0.70))
        self.assertFalse(clustering.is_offered(0.69))
        self.assertTrue(clustering.names_unasked(0.80))
        self.assertFalse(clustering.names_unasked(0.79))
        self.assertTrue(clustering.looks_wrong(0.69))
        self.assertFalse(clustering.looks_wrong(0.70))

    def test_the_bands_follow_the_two_values(self):
        self.assertEqual("likely", clustering.band(0.80))
        self.assertEqual("possible", clustering.band(0.79))
        self.assertEqual("possible", clustering.band(0.70))
        self.assertIsNone(clustering.band(0.69))
        self.assertIsNone(clustering.band(None))

    def test_grouping_is_the_radius_the_owner_kept(self):
        self.assertEqual(0.885, clustering.GROUPING)
        self.assertAlmostEqual(0.48, clustering.distance(clustering.GROUPING), places=2)

    def test_nothing_is_decided_about_nothing(self):
        for decide in (clustering.is_offered, clustering.names_unasked, clustering.looks_wrong):
            self.assertFalse(decide(None))


class KnownFacesCompareWithTheClosestFace(unittest.TestCase):
    def setUp(self):
        self.known = clustering.KnownFaces()

    def test_the_closest_face_not_an_average(self):
        # Two faces of one person far apart; a face beside one of them is like her.
        self.known.add("Oda Castellane", face(1, 0), 2020, "a.jpg")
        self.known.add("Oda Castellane", face(0, 1), 2020, "b.jpg")
        self.assertAlmostEqual(1.0, self.known.likeness("Oda Castellane", face(1, 0), 2020, "c.jpg"), places=5)

    def test_never_a_face_of_the_same_photo(self):
        self.known.add("Oda Castellane", face(1, 0), 2020, "a.jpg")
        self.known.add("Oda Castellane", face(0, 1), 2020, "b.jpg")
        self.assertAlmostEqual(0.0, self.known.likeness("Oda Castellane", face(1, 0), 2020, "a.jpg"), places=5)

    def test_from_the_years_around_the_photo(self):
        # A child's faces from long ago say less than those of the years around.
        for n in range(5):
            self.known.add("Wren Halloway", face(1, 0), 2010, "old%d.jpg" % n)
            self.known.add("Wren Halloway", face(0, 1), 2024, "new%d.jpg" % n)
        self.assertAlmostEqual(1.0, self.known.likeness("Wren Halloway", face(0, 1), 2024, "x.jpg"), places=5)
        self.assertAlmostEqual(0.0, self.known.likeness("Wren Halloway", face(1, 0), 2024, "x.jpg"), places=5)

    def test_the_person_most_like_skips_whom_the_photo_has(self):
        self.known.add("Oda Castellane", face(1, 0), None, "a.jpg")
        self.known.add("Wren Halloway", face(0.9, 0.1), None, "b.jpg")
        self.assertEqual("Oda Castellane", self.known.most_like(face(1, 0))[0])
        self.assertEqual("Wren Halloway", self.known.most_like(face(1, 0), skip={"Oda Castellane"})[0])

    def test_nobody_to_compare_with(self):
        self.assertEqual((None, None), self.known.most_like(face(1, 0)))
        self.assertIsNone(self.known.likeness("Nobody Known", face(1, 0)))


class OneOwner(unittest.TestCase):
    def shipped(self):
        for folder in ("tagpup", "scripts"):
            for base, _dirs, files in os.walk(os.path.join(ROOT, folder)):
                for name in files:
                    if name.endswith(".py"):
                        yield os.path.join(base, name)
        for name in ("tagpup_cli.py", "runner.py"):
            yield os.path.join(ROOT, name)

    def test_nothing_else_reads_the_values(self):
        # Callers ask for a decision; the numbers are clustering's alone.
        owner = os.path.join(ROOT, "tagpup", "core", "clustering.py")
        found = []
        for path in self.shipped():
            if os.path.samefile(path, owner):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if re.search(r"\b(OFFER_A_NAME|NAME_WITHOUT_ASKING)\b", text):
                found.append(os.path.relpath(path, ROOT))
        self.assertEqual([], found)

    def test_the_page_asks_the_server_which_names_look_wrong(self):
        with open(os.path.join(ROOT, "web", "tuner", "main.js"), encoding="utf-8") as f:
            page = f.read()
        self.assertNotRegex(page, r"similarity\s*<\s*0\.\d")
        self.assertIn("possibly_wrong", page)

    def test_the_page_shows_the_bands_the_server_names(self):
        # Four sets of numbers decided Likely and Possible on the page; it keeps none.
        with open(os.path.join(ROOT, "web", "tuner", "main.js"), encoding="utf-8") as f:
            page = f.read()
        self.assertEqual([], re.findall(r"(?:similarity|sim|ranked)\s*>=\s*0\.\d+", page))

    def test_nobody_writes_the_radius_as_a_number(self):
        for path in self.shipped():
            if path.endswith(os.path.join("core", "clustering.py")):
                continue
            with open(path, encoding="utf-8") as f:
                self.assertNotRegex(f.read(), r"eps\s*=\s*0\.\d|CLUSTER_EPS\s*=\s*0\.\d",
                                    os.path.relpath(path, ROOT))


if __name__ == "__main__":
    unittest.main()

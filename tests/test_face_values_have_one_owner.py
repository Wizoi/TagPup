"""Who a face is is decided in one place, tagpup.core.clustering's KnownFaces; the values that decide
whether a name looks right are held to it in test_rules_have_one_owner.

A face was compared with a person's mean face in clustering, their geometric median in
TagTuner's grid, and a mean with no years in the suggester, each against a value of its
own; the grid flagged half of every correctly named face (docs/findings.md, #71, #75).
"""
import unittest

import numpy as np

from tagpup.core import clustering


def face(*values):
    vector = np.zeros(8, dtype=np.float32)
    vector[:len(values)] = values
    return vector


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

    def test_a_row_is_read_by_the_people_it_was_made_for(self):
        self.known.add("Oda Castellane", face(1, 0), None, "a.jpg")
        table, columns = self.known.likeness_table([(face(0, 1), None, "c.jpg")])
        self.known.add("Wren Halloway", face(0, 1), None, "b.jpg")   # added after the row was made
        self.assertEqual("Oda Castellane", self.known.best(table[0], columns)[0])

    def test_nobody_to_compare_with(self):
        self.assertEqual((None, None), self.known.most_like(face(1, 0)))
        self.assertIsNone(self.known.likeness("Nobody Known", face(1, 0)))


if __name__ == "__main__":
    unittest.main()

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


def a_library_of_faces():
    """A person photographed over twenty years, one over six with undated photos, one with a single face, one whose
    faces are all from long before the faces asked, and one whose only face has no vector (not known); 1,500 faces
    asked (more than a BLOCK), a third from photos a known face is in, some undated."""
    rng = np.random.default_rng(7)
    years = {"Oda Castellane": rng.integers(2005, 2025, 30).tolist(), "Pim Arkwright": [2019],
             "Wren Halloway": [None, None] + rng.integers(2015, 2021, 10).tolist(),
             "Lior Fenwick": rng.integers(1985, 1989, 8).tolist()}
    centres = {name: rng.normal(size=64) for name in years}
    known, photos = clustering.KnownFaces(), []
    for name, taken in years.items():
        for year in taken:
            photos.append("%s_%d.jpg" % (name.split()[0], len(photos)))
            known.add(name, centres[name] + rng.normal(scale=0.8, size=64), year, photos[-1])
    known.add("Sable Quint", None, 2020, "sable.jpg")
    asked = [((centres[list(centres)[n % 4]] if n % 5 else rng.normal(size=64)) + rng.normal(scale=0.8, size=64),
              None if n % 7 == 0 else int(rng.integers(1984, 2025)),
              photos[int(rng.integers(len(photos)))] if n % 3 == 0 else "asked_%d.jpg" % n) for n in range(1500)]
    return known, asked


class OneFaceIsAsMuchLikeAPersonWhoeverAsks(unittest.TestCase):
    """The person page's similarity of each face (likeness_many), Suggest's best person (most_like) and cluster-faces'
    last pass (likeness_table read by best) give one answer, a face and a person, to float32's last bits (#1548)."""

    def assertSame(self, expected, found, name):   # None as NaN: where one has nothing to compare, so has the other
        np.testing.assert_allclose(np.array(expected, float), np.array(found, float), rtol=0, atol=1e-6, err_msg=name)

    def test_the_page_the_suggester_and_cluster_faces_agree(self):
        known, asked = a_library_of_faces()
        table, columns = known.likeness_table(asked)
        self.assertEqual(["Oda Castellane", "Pim Arkwright", "Wren Halloway", "Lior Fenwick"], list(columns))
        self.assertFalse(np.isnan(table[:, columns["Lior Fenwick"]]).any())   # another era: all of theirs, not none
        for name in list(columns) + ["Sable Quint", "Nobody Known"]:
            column = known.likeness_many(name, asked)
            self.assertSame(table[:, columns[name]] if name in columns else [None] * len(asked), column, name)
            self.assertSame(column[:40], [known.likeness(name, *face) for face in asked[:40]], name)
        for number, face in enumerate(asked):
            best = known.best(table[number], columns)
            self.assertEqual(float(np.nanmax(table[number])), best[1])
            self.assertEqual(best[0], known.most_like(*face)[0])
            among = ["Pim Arkwright", "Wren Halloway", best[0]]
            skipped = known.best(table[number], columns, among, {best[0]})
            self.assertNotEqual(best[0], skipped[0])
            self.assertEqual(skipped[0], known.most_like(*face, among=among, skip={best[0]})[0])
        self.assertEqual((None, None), clustering.KnownFaces().most_like(asked[0][0]))   # nobody to compare with


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

    def test_a_row_is_read_by_the_people_it_was_made_for(self):
        self.known.add("Oda Castellane", face(1, 0), None, "a.jpg")
        table, columns = self.known.likeness_table([(face(0, 1), None, "c.jpg")])
        self.known.add("Wren Halloway", face(0, 1), None, "b.jpg")   # added after the row was made
        self.assertEqual("Oda Castellane", self.known.best(table[0], columns)[0])


if __name__ == "__main__":
    unittest.main()

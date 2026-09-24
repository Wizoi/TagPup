"""The photos' nearest neighbours by cosine similarity (tagpup.ml.vector_index).

Moved out of PhotoIndex, which built it inline from the rows it read.
"""
import unittest

import numpy as np

from tagpup.ml.vector_index import VectorIndex


class NearestNeighbours(unittest.TestCase):
    def setUp(self):
        self.index = VectorIndex([[1, 0, 0], [0, 10, 0], [1, 1, 0]], ["east", "north", "north-east"])

    def test_nearest_come_first_whatever_their_length(self):
        # [0, 10, 0] is long; cosine ignores length, so it is exactly north.
        found = self.index.search([0, 1, 0], k=3)
        self.assertEqual(["north", "north-east", "east"], [item for _, item in found])
        self.assertAlmostEqual(1.0, found[0][0], places=5)

    def test_asking_for_more_than_there_are_returns_them_all(self):
        self.assertEqual(3, len(self.index.search([1, 0, 0], k=50)))

    def test_says_its_size_as_faiss_does(self):
        self.assertEqual((3, 3), (self.index.d, self.index.ntotal))

    def test_a_zero_vector_does_not_break_it(self):
        index = VectorIndex([np.zeros(3, dtype=np.float32), [1, 0, 0]], ["nothing", "east"])
        self.assertEqual("east", index.search([1, 0, 0], k=1)[0][1])


if __name__ == "__main__":
    unittest.main()

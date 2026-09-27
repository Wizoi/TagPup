"""A FAISS search runs on its caller's thread alone (tagpup.ml.vector_index).

Measured on photo_index's 68,387 vectors: the first flat search grew the process's
private bytes by 2.3 GB -- about 128 MB for each of the machine's 16 OpenMP threads,
kept until the process ended -- and a search on one thread took 10 ms against 12.5 ms on
sixteen. The always-on process would hold those 2 GB from its first Suggest on. Suggest's
own four workers each search on their own thread.
"""
import unittest

import faiss
import numpy as np

from tagpup.ml.vector_index import VectorIndex


class ASearch(unittest.TestCase):
    def test_uses_one_thread(self):
        faiss.omp_set_num_threads(4)   # as a process starts: one thread per core
        index = VectorIndex(np.eye(4, dtype=np.float32), ["a", "b", "c", "d"])
        self.assertEqual("b", index.search([0, 1, 0, 0], k=1)[0][1])
        self.assertEqual(1, faiss.omp_get_max_threads())


if __name__ == "__main__":
    unittest.main()

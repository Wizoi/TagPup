"""Grouping faces a block at a time, so the screen can say how far along it is.

The per-person grid spends most of its minute inside one sklearn DBSCAN call, and one
call is one call: it says nothing until it finishes, so the screen could only sit
blank. `cluster_candidates` does the same work by blocks of rows, which gives a number
to report and -- because each block is a single BLAS matrix multiply, where a ball tree
in 512 dimensions makes the same comparisons with more bookkeeping -- costs nothing.
Measured on the real library: 25.8s against 25.1s for the call it replaced, over 79,580
faces, with 252 progress reports along the way.

What matters is that it is the same clustering. On that run: 4,202 clusters both ways,
the same partition face for face, and the same 63,184 left as noise. These tests hold
that line on cases small enough to read.
"""
import os
import sys
import unittest

import numpy as np
from sklearn.cluster import DBSCAN

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tuner_server import CLUSTER_EPS, cluster_candidates


def unit_rows(matrix):
    return (matrix / np.linalg.norm(matrix, axis=1, keepdims=True)).astype(np.float32)


def partition(labels):
    """The grouping, independent of what the labels are numbered."""
    groups = {}
    for index, label in enumerate(labels):
        if label != -1:
            groups.setdefault(label, set()).add(index)
    return {frozenset(members) for members in groups.values()}


def reference(embeddings):
    """The call cluster_candidates replaced."""
    return DBSCAN(eps=CLUSTER_EPS, min_samples=2, metric="euclidean",
                  n_jobs=-1).fit_predict(embeddings)


def people_shaped_pool(groups, per_group, spread, seed, dimensions=512):
    """A few tight knots of near-identical vectors, plus strangers, as faces arrive."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(groups):
        centre = rng.standard_normal(dimensions)
        centre /= np.linalg.norm(centre)
        for _ in range(per_group):
            rows.append(centre + rng.standard_normal(dimensions) * spread)
    for _ in range(groups * 2):          # strangers, who should group with nobody
        rows.append(rng.standard_normal(dimensions))
    pool = unit_rows(np.array(rows))
    rng.shuffle(pool)
    return pool


class TestItIsTheSameClustering(unittest.TestCase):
    def assert_same_as_dbscan(self, pool):
        expected, actual = reference(pool), cluster_candidates(pool)
        self.assertEqual(
            partition(expected), partition(actual),
            "the grouping differs from the DBSCAN call this replaced")
        self.assertEqual(
            int((expected == -1).sum()), int((actual == -1).sum()),
            "a different number of faces were left ungrouped")

    def test_tight_groups_among_strangers(self):
        self.assert_same_as_dbscan(people_shaped_pool(8, 6, 0.02, seed=1))

    def test_groups_loose_enough_to_sit_near_the_threshold(self):
        """Where the two implementations would disagree if they disagreed anywhere."""
        self.assert_same_as_dbscan(people_shaped_pool(6, 5, 0.22, seed=2))

    def test_a_pool_of_nothing_but_strangers(self):
        rng = np.random.default_rng(3)
        self.assert_same_as_dbscan(unit_rows(rng.standard_normal((60, 512))))

    def test_a_pool_that_is_all_one_person(self):
        rng = np.random.default_rng(4)
        centre = rng.standard_normal(512)
        pool = unit_rows(centre + rng.standard_normal((40, 512)) * 0.01)
        self.assert_same_as_dbscan(pool)

    def test_identical_vectors(self):
        """Distance zero has to survive being stored in a sparse matrix."""
        one = unit_rows(np.random.default_rng(5).standard_normal((1, 512)))
        pool = np.repeat(one, 5, axis=0)
        labels = cluster_candidates(pool)
        self.assertEqual(partition(reference(pool)), partition(labels))
        self.assertEqual(
            1, len(partition(labels)),
            "five copies of one face did not come out as one group")

    def test_it_crosses_more_than_one_block(self):
        """The blocks are an implementation detail; the answer must not notice them."""
        import tuner_server

        pool = people_shaped_pool(5, 5, 0.02, seed=6)
        original = tuner_server.CLUSTER_BLOCK_BYTES
        try:
            # Small enough to force several blocks over a pool of this size.
            tuner_server.CLUSTER_BLOCK_BYTES = 4 * 512 * 7
            blocked = cluster_candidates(pool)
        finally:
            tuner_server.CLUSTER_BLOCK_BYTES = original
        self.assertEqual(partition(reference(pool)), partition(blocked))


class TestTooFewToGroup(unittest.TestCase):
    """min_samples is 2, so nothing smaller than that can form a group."""

    def test_no_faces(self):
        labels = cluster_candidates(np.zeros((0, 512), dtype=np.float32))
        self.assertEqual(0, len(labels))

    def test_one_face(self):
        one = unit_rows(np.random.default_rng(7).standard_normal((1, 512)))
        self.assertEqual([-1], list(cluster_candidates(one)))


class TestItReportsProgress(unittest.TestCase):
    """The reason this exists rather than the one-line call."""

    def test_progress_runs_forward_and_finishes(self):
        pool = people_shaped_pool(4, 4, 0.02, seed=8)
        seen = []
        cluster_candidates(pool, on_progress=lambda done, total: seen.append((done, total)))

        self.assertTrue(seen, "nothing was reported at all")
        self.assertTrue(
            all(b[0] >= a[0] for a, b in zip(seen, seen[1:])),
            "progress went backwards: %s" % (seen,))
        self.assertEqual(
            (len(pool), len(pool)), seen[-1],
            "the last report did not say the work was finished")
        for done, total in seen:
            self.assertEqual(len(pool), total)
            self.assertLessEqual(done, total)

    def test_a_pool_too_small_to_group_reports_nothing(self):
        seen = []
        cluster_candidates(np.zeros((1, 512), dtype=np.float32),
                           on_progress=lambda done, total: seen.append(done))
        self.assertEqual([], seen)


if __name__ == "__main__":
    unittest.main()

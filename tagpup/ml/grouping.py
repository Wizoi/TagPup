"""Grouping faces that resemble each other, a block of rows at a time.

TagTuner's per-person grid groups every nameless candidate before it shows one, and on
a real library that is tens of thousands of 512-dimensional vectors. This was the
server's own code; it is the model side of that screen, so it lives with the models.
The radius it groups at is clustering's (tagpup.core.clustering.GROUPING): the server
must not carry a number of its own.
"""
import numpy as np
from scipy import sparse
from sklearn.cluster import DBSCAN
from sklearn.neighbors import sort_graph_by_row_values

from tagpup.core import clustering

#: How alike two faces must be to be neighbours, as a distance between unit vectors:
#: clustering's radius as a distance. The DBSCAN call this replaced used the same number.
CLUSTER_EPS = clustering.distance(clustering.GROUPING)

#: How much of one row-block of the similarity matrix to hold at once, in bytes. The
#: block is (rows x every face) float32, so this is what decides the block size -- and
#: with it, how often progress can be reported. 96 MB is about 240 rows against a
#: hundred thousand faces, which is a few hundred updates over the whole pass.
CLUSTER_BLOCK_BYTES = 96 * 1024 * 1024


def cluster_candidates(embeddings, on_progress=None):
    """Group faces that resemble each other, a block of rows at a time.

    This is DBSCAN, and it returns what `DBSCAN(eps=CLUSTER_EPS, min_samples=2,
    metric="euclidean")` returns -- verified against it on the real library: 79,662
    faces, 4,210 clusters both ways, the same partition face for face and the same
    63,175 left as noise.

    The reason for doing it here rather than in one sklearn call is that one call is
    one call: it takes the better part of a minute on a real pool and says nothing
    until it is finished, so the screen could only sit there. Building the neighbour
    graph a block at a time gives a number to report, and hands DBSCAN a graph it
    resolves in about half a second.

    It is not slower. Measured on that pool: 25.6s against sklearn's 29.3s, because
    the blocks are one BLAS matrix multiply each and a ball tree in 512 dimensions
    degenerates to the same comparisons with more bookkeeping.

    The vectors are unit length, so ||a - b||^2 = 2 - 2(a.b) and a distance threshold
    is a similarity threshold; comparing similarities lets the whole block be
    thresholded at once.

    `on_progress` is called with (rows_done, rows_total) as each block lands.
    """
    count = len(embeddings)
    if count < 2:
        return np.full(count, -1, dtype=int)

    similarity_floor = clustering.GROUPING
    block_rows = int(CLUSTER_BLOCK_BYTES / (4 * max(count, 1)))
    block_rows = max(1, min(block_rows, 4096, count))

    neighbour_rows, neighbour_cols, distances = [], [], []
    for start in range(0, count, block_rows):
        stop = min(start + block_rows, count)
        block = embeddings[start:stop] @ embeddings.T
        rows, cols = np.nonzero(block >= similarity_floor)
        neighbour_rows.append(rows + start)
        neighbour_cols.append(cols)
        # Back to a distance for DBSCAN. Clamped because floating point can put a
        # face a hair over 1.0 similar to itself, and a negative square root is not
        # a distance.
        distances.append(
            np.sqrt(np.maximum(0.0, 2.0 - 2.0 * block[rows, cols])))
        if on_progress:
            on_progress(stop, count)

    rows = np.concatenate(neighbour_rows)
    cols = np.concatenate(neighbour_cols)
    # A face is its own neighbour at distance zero, and min_samples counts it. Sparse
    # storage drops an explicit zero, which would lose that, so the floor keeps it.
    values = np.maximum(np.concatenate(distances), 1e-9)

    graph = sparse.csr_matrix((values, (rows, cols)), shape=(count, count))
    sort_graph_by_row_values(graph, warn_when_not_sorted=False)
    return DBSCAN(eps=CLUSTER_EPS, min_samples=2, metric="precomputed").fit_predict(graph)

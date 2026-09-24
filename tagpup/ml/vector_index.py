"""Nearest neighbours among the photos' CLIP embeddings, in memory, by cosine similarity.

Built from the vectors and the items they belong to, which the caller reads from the
library; this module never sees the database. On a GPU when FAISS has one.
"""
import logging

import faiss
import numpy as np

logger = logging.getLogger(__name__)


def _flat_index(dim):
    """A flat inner-product index: on the GPU when there is one, else on the CPU."""
    if hasattr(faiss, "get_num_gpus"):
        try:
            if faiss.get_num_gpus() > 0:
                index = faiss.index_cpu_to_gpu(faiss.StandardGpuResources(), 0, faiss.IndexFlatIP(dim))
                logger.info("Initialized GPU-accelerated FAISS index.")
                return index
        except Exception as e:
            logger.warning("Failed to initialize GPU FAISS index: %s. Falling back to CPU index.", e)
    return faiss.IndexFlatIP(dim)


class VectorIndex:
    """`items[i]` is what `vectors[i]` belongs to; `search` returns the items nearest a
    query. Vectors are scaled to unit length, so the inner product is the cosine."""

    def __init__(self, vectors, items):
        matrix = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.items = list(items)
        self._index = _flat_index(matrix.shape[1])
        self._index.add(matrix / norms)

    @property
    def d(self):
        """The vectors' dimension, as FAISS names it."""
        return self._index.d

    @property
    def ntotal(self):
        """How many vectors, as FAISS names it."""
        return self._index.ntotal

    def search(self, query_vector, k=15):
        """The `k` items nearest `query_vector`: [(similarity, item)], nearest first."""
        if self._index.ntotal == 0:
            return []
        query = np.array([query_vector], dtype=np.float32)
        norm = np.linalg.norm(query)
        if norm > 0:
            query = query / norm
        k = min(k, self._index.ntotal)
        if k == 0:
            return []
        scores, indices = self._index.search(query, k)
        return [(float(score), self.items[i]) for score, i in zip(scores[0], indices[0])
                if 0 <= i < len(self.items)]

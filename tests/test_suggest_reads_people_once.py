"""Suggesting for a folder reads who is known once, and the face models load once.

Choosing a folder of three photos and clicking Suggest took 102 seconds on a real
library, 58 with the models already loaded. Most of it was the suggester reading every
face in the library -- 225,000 rows, each with its crop image -- twice for every photo,
to use the few thousand that carry a name. The warm-up that was meant to load the
face models only constructed the processor, and the pool's workers then each loaded
the models themselves.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import faces  # noqa: E402
import suggester  # noqa: E402
from index import PhotoIndex  # noqa: E402
from suggester import TagSuggester  # noqa: E402


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class FakeTaxonomy:
    paths = ["People/Rowan Thackeray", "Activity/Running"]

    def expand_tag(self, tag):
        return [tag]

    def find_person_path(self, name):
        return "People/" + name

    def find_by_leaf(self, leaf):
        return None

    def people_roots(self):
        # The roots the library's tree flags as holding faces (docs/findings.md, #66).
        return {"people"}


class CountingIndex:
    """The two ways of learning who is known, counted."""
    conn = None

    def __init__(self):
        self.all_faces_reads = 0
        self.centroid_reads = 0

    def get_all_faces(self):
        self.all_faces_reads += 1
        return [{"name": "Rowan Thackeray", "embedding": unit([1, 0, 0])}]

    def get_person_centroids(self):
        self.centroid_reads += 1
        return {"Rowan Thackeray": unit([1, 0, 0])}

    def search(self, embedding, k=15):
        return []

    def save_faces_if_absent(self, photo_path, detected):
        return 0


class FakeFaceProcessor:
    def detect_and_embed_faces(self, path):
        return [{"box": [0, 0, 100, 100], "embedding": unit([1, 0, 0]).tolist(), "prob": 0.99}]


class ReadsPeopleOncePerRun(unittest.TestCase):
    def setUp(self):
        self._saved = suggester._global_face_processor
        suggester._global_face_processor = FakeFaceProcessor()

    def tearDown(self):
        suggester._global_face_processor = self._saved

    def test_three_photos_read_the_people_once_and_never_the_whole_table(self):
        index = CountingIndex()
        run = TagSuggester(index, FakeTaxonomy())
        results = [run.suggest_for_photo(r"D:\Pictures\Run\%02d.jpg" % n, [1.0, 0.0, 0.0])
                   for n in range(3)]
        self.assertEqual(index.all_faces_reads, 0)
        self.assertEqual(index.centroid_reads, 1)
        # And the matching still works: each photo's face is Rowan.
        for result in results:
            self.assertIn("People/Rowan Thackeray", [t["tag"] for t in result["suggested_tags"]])


class PersonCentroids(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="centroids_")
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        rows = [
            ("Rowan Thackeray", 0, [1, 0, 0]),
            ("Rowan Thackeray", 0, [0.8, 0.6, 0]),
            ("Rowan Thackeray", 1, [0, 0, 1]),   # excluded: must not pull the centroid
            (None, 0, [0, 1, 0]),                 # unnamed: nobody's
            ("Imogen Vale", 0, [0, 1, 0]),
        ]
        index.conn.execute("INSERT INTO photos (path) VALUES (?)", (r"D:\Pictures\a.jpg",))
        for name, excluded, emb in rows:
            index.conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name, excluded) VALUES (?,?,?,?,?)",
                (r"D:\Pictures\a.jpg", "[0,0,1,1]", np.asarray(emb, dtype=np.float32).tobytes(),
                 name, excluded))
        index.conn.commit()
        self.index = index

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_named_faces_only_excluded_left_out_unit_length(self):
        centroids = self.index.get_person_centroids()
        self.assertEqual(sorted(centroids), ["Imogen Vale", "Rowan Thackeray"])
        np.testing.assert_allclose(centroids["Rowan Thackeray"], unit([0.9, 0.3, 0]), atol=1e-6)
        for vector in centroids.values():
            self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=5)


class FaceModelsLoadOnce(unittest.TestCase):
    def test_warm_up_actually_loads_the_models(self):
        import tagpup_server
        loaded = []

        class Processor:
            def _init_models(self):
                loaded.append(True)

        embedder = mock.Mock()
        saved = suggester._global_face_processor
        suggester._global_face_processor = None
        try:
            with mock.patch.object(faces, "FaceProcessor", Processor):
                tagpup_server.warmup_embedder_thread(embedder)
        finally:
            suggester._global_face_processor = saved
        self.assertEqual(loaded, [True])

    def test_four_workers_arriving_together_load_the_models_once(self):
        processor = faces.FaceProcessor(device="cpu")
        loads = []

        def slow_load():
            loads.append(threading.get_ident())
            time.sleep(0.05)
            processor.mtcnn = object()

        with mock.patch.object(processor, "_load_models", side_effect=slow_load, create=True):
            workers = [threading.Thread(target=processor._init_models) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        self.assertEqual(len(loads), 1)


if __name__ == "__main__":
    unittest.main()

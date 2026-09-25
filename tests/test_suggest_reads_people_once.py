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

import _root  # noqa: E402,F401
from tagpup.core import clustering  # noqa: E402
from tagpup.ml import clip as clip_model  # noqa: E402
from tagpup.ml import faces as face_model  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import search  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.services.suggester import TagSuggester  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402


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


class Index:
    """A library with no neighbours and no faces on file."""
    conn = None
    db_path = "no such library.db"

    def search(self, embedding, k=15):
        return []


class FakeFaceProcessor:
    def detect_and_embed_faces(self, path):
        return [{"box": [0, 0, 100, 100], "embedding": unit([1, 0, 0]).tolist(), "prob": 0.99}]


class ReadsPeopleOncePerRun(unittest.TestCase):
    def test_three_photos_read_the_people_once_and_never_the_whole_table(self):
        known_reads = []

        def known_faces(db_path):
            known_reads.append(db_path)
            return clustering.KnownFaces.of([("Rowan Thackeray", unit([1, 0, 0]), None, r"D:\Pictures\Before\a.jpg")])

        run = TagSuggester(Index(), FakeTaxonomy(), faces=FakeFaceProcessor())
        with mock.patch.object(face_records, "known_faces", side_effect=known_faces), \
                mock.patch.object(face_records, "record_detected", return_value=0), \
                mock.patch.object(store_faces, "for_clustering", side_effect=AssertionError("read every face")):
            results = [run.suggest_for_photo(r"D:\Pictures\Run\%02d.jpg" % n, [1.0, 0.0, 0.0])
                       for n in range(3)]
        self.assertEqual(len(known_reads), 1)
        # And the matching still works: each photo's face is Rowan.
        for result in results:
            self.assertIn("People/Rowan Thackeray", [t["tag"] for t in result["suggested_tags"]])


class KnownFacesFromTheLibrary(unittest.TestCase):
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
                "INSERT INTO faces (photo_id, box, embedding, name, excluded) VALUES ((SELECT id FROM photos WHERE path = ?),?,?,?,?)",
                (r"D:\Pictures\a.jpg", "[0,0,1,1]", np.asarray(emb, dtype=np.float32).tobytes(),
                 name, excluded))
        index.conn.commit()
        self.index = index

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_named_faces_only_excluded_left_out(self):
        known = face_records.known_faces(self.db)
        self.assertEqual(sorted(known.names()), ["Imogen Vale", "Rowan Thackeray"])
        # Rowan's closest face to each of their two named ones is itself; the excluded
        # face is not among them.
        self.assertAlmostEqual(1.0, known.likeness("Rowan Thackeray", unit([0.8, 0.6, 0])), places=5)
        self.assertAlmostEqual(0.0, known.likeness("Rowan Thackeray", unit([0, 0, 1])), places=5)


class FaceModelsLoadOnce(unittest.TestCase):
    def test_warm_up_actually_loads_the_models(self):
        loaded = []

        class Faces:
            def load(self):
                loaded.append(True)

        Runtime(clip=mock.Mock(), faces=Faces()).warm_up()
        self.assertEqual(loaded, [True])

    def test_warm_up_builds_each_model_once_from_the_settings(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import own_home
        from tagpup.core.library import Library
        from tagpup.services import libraries as library_actions
        from tagpup.services import settings as library_settings
        home = own_home.for_test(self)
        library_actions.create(home.library("harbour.db"))
        library = Library(home.library("harbour.db"))
        with mock.patch.object(clip_model, "ClipModel") as clip, \
                mock.patch.object(face_model, "FaceModel") as faces:
            runtime = Runtime()
            runtime.warm_up([library])
            runtime.warm_up([library])
        found = library_settings.of(library)
        clip.assert_called_once_with(**found.embedder)
        faces.assert_called_once_with(**found.faces)
        self.assertEqual(2, clip.return_value.load.call_count)
        self.assertEqual(2, faces.return_value.load.call_count)

    def test_warm_up_opens_no_library(self):
        """The warm-up made a PhotoIndex on the startup library and kept it open until
        the process ended; a thread starting after the file had gone made an empty
        library in its place (docs/findings.md, #99). The models are the process's."""
        opened = []
        with mock.patch.object(search, "PhotoIndex", side_effect=lambda *a, **k: opened.append(a)), \
                self.assertNoLogs("tagpup.runtime", level="ERROR"):
            Runtime(clip=mock.Mock(), faces=mock.Mock()).warm_up()
        # The warm-up catches what a model raises, and logs it: a patch that raised was
        # swallowed there, so the library it opened is counted instead.
        self.assertEqual([], opened)

    def test_four_workers_arriving_together_load_the_models_once(self):
        processor = face_model.FaceModel(min_face_size=20, confidence_threshold=0.85,
                                         mtcnn_thresholds=[0.6, 0.7, 0.7], device="cpu")
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

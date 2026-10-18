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
from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.ml import clip as clip_model  # noqa: E402
from tagpup.ml import faces as face_model  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import search  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.services.suggester import TagSuggester  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stand_in_models  # noqa: E402
from face_rows import person_of  # noqa: E402


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class Index:
    """A library with no neighbours, no faces on file and no tree (its file is not there): a run over it is handed the
    suggester model_for_run makes (tests/stand_in_models.py)."""
    conn = None
    db_path = "no such library.db"

    def search(self, embedding, k=15):
        return []


class FakeFaceProcessor(stand_in_models.Faces):
    def ready(self):
        """The run's turn on the graphics card (tagpup.runtime._OnTheCard): nothing to wait for here."""

    def detect_and_embed_faces(self, path, *, max_side):
        return [{"box": [0, 0, 100, 100], "embedding": unit([1, 0, 0]).tolist(), "prob": 0.99}]


class ReadsPeopleOncePerRun(unittest.TestCase):
    def test_three_photos_read_the_people_once_and_never_the_whole_table(self):
        known_reads = []

        def known_faces(db_path):
            known_reads.append(db_path)
            return clustering.KnownFaces.of([(7, unit([1, 0, 0]), None, r"D:\Pictures\Before\a.jpg", Ref(7, "Rowan Thackeray"))])

        with mock.patch.object(face_records, "known_faces", side_effect=known_faces), \
                mock.patch.object(TagSuggester, "_tags_by_id", return_value={7: "People/Rowan Thackeray"}), \
                mock.patch.object(face_records, "record_detected", return_value=0), \
                mock.patch.object(store_faces, "for_clustering", side_effect=AssertionError("read every face")):
            run = stand_in_models.run_for(Index(), FakeFaceProcessor(), dim=3).suggester
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
                "INSERT INTO faces (photo_id, box, embedding, name, tag_id, excluded)"
                " VALUES ((SELECT id FROM photos WHERE path = ?),?,?,?,?,?)",
                (r"D:\Pictures\a.jpg", "[0,0,1,1]", np.asarray(emb, dtype=np.float32).tobytes(),
                 name, person_of(index.conn, name) if name else None, excluded))
        index.conn.commit()
        self.index = index

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_named_faces_only_excluded_left_out(self):
        known = face_records.known_faces(self.db)
        self.assertEqual(sorted(person.name for person in known.labels.values()), ["Imogen Vale", "Rowan Thackeray"])
        # Rowan's closest face to each of their two named ones is itself; the excluded
        # face is not among them.
        # Keyed by person (the id of their node), the Ref each is in `labels`.
        rowan = next(key for key, person in known.labels.items() if person.name == "Rowan Thackeray")
        self.assertAlmostEqual(1.0, known.likeness(rowan, unit([0.8, 0.6, 0])), places=5)
        self.assertAlmostEqual(0.0, known.likeness(rowan, unit([0, 0, 1])), places=5)


class FaceModelsLoadOnce(unittest.TestCase):
    def library(self):
        import own_home
        from tagpup.core.library import Library
        from tagpup.services import libraries as library_actions
        home = own_home.for_test(self)
        library_actions.create(home.library("harbour.db"))
        return Library(home.library("harbour.db"))

    def test_warm_up_builds_each_model_once_from_the_settings_and_loads_them(self):
        """The warm-up that was meant to load the face models only built them; each load is counted here."""
        from tagpup.services import settings as library_settings
        library = self.library()
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
        library = self.library()
        opened = []
        with mock.patch.object(search, "PhotoIndex", side_effect=lambda *a, **k: opened.append(a)), \
                self.assertNoLogs("tagpup.runtime", level="ERROR"):
            Runtime(build_clip=lambda settings: mock.Mock(), build_faces=lambda settings: mock.Mock()).warm_up([library])
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


class FourWorkersShareEveryonesFaces(unittest.TestCase):
    """Suggest's pool workers ask most_like of the one KnownFaces the run read, at once: everyone's faces are put in one
    matrix once, not once a worker (about 72 MB each on photo_index, the last one kept)."""

    def test_four_workers_asking_together_make_everyones_faces_once(self):
        known = clustering.KnownFaces.of([(person, unit([1, 0, 0]) if person == 1 else unit([0, 1, 0]), 2020, None)
                                          for person in (1, 2, 3)])
        real, made = clustering.KnownFaces._build_table, []

        def slow_build(self, among):
            made.append(threading.get_ident())
            time.sleep(0.05)
            return real(self, among)

        answers = []
        with mock.patch.object(clustering.KnownFaces, "_build_table", slow_build):
            workers = [threading.Thread(target=lambda: answers.append(known.most_like(unit([1, 0, 0]), 2020)))
                       for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        self.assertEqual(1, len(made))
        self.assertEqual([1] * 4, [person for person, _likeness in answers])


if __name__ == "__main__":
    unittest.main()

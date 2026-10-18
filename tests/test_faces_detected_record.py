"""A photo the index read and found no face in is not detected again (docs/findings.md, #773).

The index recorded nothing for a photo with no face, so Suggest could not tell it from one
never looked at: it took the graphics card -- waiting behind any index -- loaded the face
model and detected again, every time. 10,732 of photo_index's 68,324 photos have no face
row (counted 2026-10-04). Now every detection that ran is recorded, faces found or not
(tagpup.store.faces_detected): by the indexer's record_batch and by Suggest's
record_detected; one that failed is not; and Suggest reads it before it detects.

The photo's row and vector are made as the indexer makes them (tests/photo_rows.py,
store.embeddings.put); the detection as the indexer records it (record_batch). The face
model is a stand-in that says which settings it detects with and counts what is asked of
it: `ready` is what takes a Suggest run's turn on the graphics card (tagpup.runtime). Suggest is reached as a run reaches
it (model_for_run), CLIP stood in for (tests/stand_in_models.py).

A photo whose face rows are deleted is detected again (#779): the record says detection ran on a photo, and deleting
every face row of it -- by the faces store, by id, or by the journal -- left the record, so Suggest believed the photo's
faces known. A photo that keeps a face keeps its record; a detection that replaces the faces is recorded.
Lifted: test_faces_detected_follows_the_faces. Break that fails the kept tests: faces_detected.forget_faceless doing
nothing fails test_a_photo_that_loses_its_last_face_is_detected_again.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402
from face_rows import add_face, FACENET  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.ml.faces import NotDetected  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import faces, faces_detected, journal  # noqa: E402


class FaceModel:
    """A face model on the card: its settings, and what it was asked."""
    device = "cuda"

    def __init__(self, min_face_size=20):
        self.min_face_size = min_face_size
        self.confidence_threshold = 0.9
        self.mtcnn_thresholds = [0.6, 0.7, 0.7]
        self.readied = self.detected = 0

    def ready(self):
        self.readied += 1

    def detect_and_embed_faces(self, path, *, max_side):
        self.detected += 1
        return []


class Case(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="faces_detected_")
        self.db_path = home.library("harbour.db")
        library_actions.create(self.db_path)
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        self.photo = os.path.join(folder, "empty quay.jpg")
        Image.new("RGB", (32, 32), "grey").save(self.photo, "JPEG")
        settings = library_settings.of(Library(self.db_path))
        self.model_key = store_embeddings.model_key(**settings.embedder)
        self.vector = [1.0] + [0.0] * 7
        stamp = store_embeddings.stamp_of(self.photo)
        conn = db.connect(self.db_path)
        try:
            photo_rows.hold(conn, folder)
            photo_rows.add_read(conn, self.photo, {})
            store_embeddings.put(conn, self.photo, self.model_key, stamp[0], stamp[1],
                                 np.array(self.vector, dtype=np.float32).tobytes())
            conn.commit()
        finally:
            conn.close()

    def index_detects(self, model, found):
        """Record a detection as the indexer does, at the end of a batch."""
        conn = db.connect(self.db_path)
        try:
            face_records.record_batch(conn, {self.photo: found}, detector=face_records.detector_of(model, None), model=FACENET)
        finally:
            conn.close()

    def recorded(self, model):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return faces_detected.detected(conn, self.photo, face_records.detector_of(model, None))
        finally:
            conn.close()

    def suggest(self, model):
        index = PhotoIndex(db_path=self.db_path, model=self.model_key)
        index.load()
        try:
            stand_in_models.run_for(index, model, dim=len(self.vector)).suggester.suggest_for_photo(self.photo, self.vector)
        finally:
            index.close()


class AFacelessPhotoTheIndexRead(Case):
    def test_takes_no_card_and_is_not_detected_again(self):
        model = FaceModel()
        self.index_detects(model, [])
        self.assertTrue(self.recorded(model))
        self.suggest(model)
        self.assertEqual((0, 0), (model.readied, model.detected), "Suggest took the card for a photo already detected")

    def test_other_face_settings_detect_again(self):
        self.index_detects(FaceModel(min_face_size=40), [])
        model = FaceModel(min_face_size=20)
        self.suggest(model)
        self.assertEqual((1, 1), (model.readied, model.detected))

    def test_a_detection_that_failed_is_not_recorded(self):
        model = FaceModel()
        self.index_detects(model, NotDetected())
        self.assertFalse(self.recorded(model))


class SuggestRecordsWhatItDetects(Case):
    def test_once_detected_by_suggest_the_next_run_does_not_detect(self):
        model = FaceModel()
        self.suggest(model)
        self.assertEqual((1, 1), (model.readied, model.detected))
        self.assertTrue(self.recorded(model))
        again = FaceModel()
        self.suggest(again)
        self.assertEqual((0, 0), (again.readied, again.detected))


class ThePhotoThatLosesItsFaces(unittest.TestCase):
    DETECTOR = faces_detected.detector({"min_face_size": 20})

    def setUp(self):
        home = own_home.for_test(self, prefix="faces_detected_")
        self.db_path = home.library("harbour.db")
        library_actions.create(self.db_path)
        self.photo = os.path.join(home.root, "Regatta", "a.jpg")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, self.photo, {})
            self.faces = [add_face(conn, self.photo, [0, 0, 10 + n, 10]) for n in range(2)]
            faces_detected.record(conn, self.photo, self.DETECTOR, None, 2)
            conn.commit()
        finally:
            conn.close()

    def write(self, work):
        return db.write_with_connection(self.db_path, work)

    def recorded(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return faces_detected.detected(conn, self.photo, self.DETECTOR)
        finally:
            conn.close()

    def test_removing_a_photos_faces_forgets_that_they_were_detected(self):
        self.assertTrue(self.recorded())
        self.assertEqual(2, self.write(lambda conn: faces.remove_for_photo(conn, self.photo)))
        self.assertFalse(self.recorded())

    def test_a_photo_that_loses_its_last_face_is_detected_again(self):
        self.write(lambda conn: faces.delete(conn, []))
        self.write(lambda conn: faces.delete(conn, self.faces[:1]))
        self.assertTrue(self.recorded(), "a photo that keeps a face keeps its record")
        self.write(lambda conn: faces.delete(conn, self.faces[1:]))
        self.assertFalse(self.recorded())

    def test_the_journals_delete_of_the_last_face_forgets_it_too_and_its_undo_puts_the_face_back(self):
        # The journal writes face rows of its own (a duplicate face removed, a photo's rows merged): same rule.
        journal.apply(self.db_path, "delete faces", [journal.delete("faces", (face,), {}) for face in self.faces[:1]])
        self.assertTrue(self.recorded(), "a photo that keeps a face keeps its record")
        applied = journal.apply(self.db_path, "delete faces", [journal.delete("faces", (face,), {}) for face in self.faces[1:]])
        self.assertFalse(self.recorded())
        journal.undo(self.db_path, applied.change_id)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
        finally:
            conn.close()

    def test_a_detection_that_replaces_the_faces_is_recorded(self):
        conn = db.connect(self.db_path)
        try:
            face_records.record_batch(conn, {self.photo: [{"box": [0, 0, 10, 10], "embedding": [0.0, 1.0], "prob": 0.99}]},
                                     overwrite=True, detector=self.DETECTOR, model=FACENET)
        finally:
            conn.close()
        self.assertTrue(self.recorded())


if __name__ == "__main__":
    unittest.main()

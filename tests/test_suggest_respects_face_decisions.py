"""Suggest does not name a face someone excluded, or decided was nobody.

The suggester read every face row on the photo and matched each against the known
people, so a passer-by excluded in TagTuner, or a face marked "nobody", came back as a
suggestion for whoever it most resembled -- the decision was undone one click later.
Skipping them must not make the photo look faceless, either: a photo whose every face
had been excluded would then be sent through detection again. The suggester is the one a run
is handed (model_for_run), its tree the library's, CLIP stood in for (tests/stand_in_models.py).
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stand_in_models  # noqa: E402
from face_rows import person_of  # noqa: E402

ROWAN = [1.0, 0.0, 0.0]


class CountingFaceProcessor:
    def __init__(self):
        self.calls = 0

    def ready(self):
        """The run's turn on the graphics card (tagpup.runtime._OnTheCard): nothing to wait for here."""

    def detect_and_embed_faces(self, path):
        self.calls += 1
        return [{"box": [0, 0, 100, 100], "embedding": ROWAN, "prob": 0.99}]


class SuggestRespectsFaceDecisions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_decisions_")
        self.index = PhotoIndex(os.path.join(self.dir, "lib.db"))
        self.index.load()
        self.processor = CountingFaceProcessor()
        taxonomy.add_path(self.index.conn, "People", root_has_face=1)   # the tree files Rowan: a face is named by a node
        taxonomy.add_path(self.index.conn, "People/Rowan Thackeray")
        # Rowan is known from another photo.
        self.add_face(r"D:\Pictures\known.jpg", "Rowan Thackeray", None, 0)

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_face(self, path, name, name_source, excluded):
        conn = self.index.conn
        conn.execute("INSERT OR IGNORE INTO photos (path) VALUES (?)", (path,))
        conn.execute(
            "INSERT INTO faces (photo_id, box, embedding, prob, name, tag_id, name_source, excluded)"
            " VALUES ((SELECT id FROM photos WHERE path = ?), '[0, 0, 100, 100]', ?, 0.99, ?, ?, ?, ?)",
            (path, np.asarray(ROWAN, dtype=np.float32).tobytes(), name, person_of(conn, name) if name else None,
             name_source, excluded))
        conn.commit()

    def suggested(self, path):
        run = stand_in_models.run_for(self.index, self.processor, dim=len(ROWAN))
        result = run.suggester.suggest_for_photo(path, ROWAN)
        return [t["tag"] for t in result["suggested_tags"]]

    def test_an_unnamed_face_is_still_matched(self):
        self.add_face(r"D:\Pictures\open.jpg", None, None, 0)
        self.assertIn("People/Rowan Thackeray", self.suggested(r"D:\Pictures\open.jpg"))

    def test_an_excluded_face_is_not_suggested(self):
        self.add_face(r"D:\Pictures\crowd.jpg", None, "manual", 1)
        self.assertNotIn("People/Rowan Thackeray", self.suggested(r"D:\Pictures\crowd.jpg"))

    def test_a_face_decided_to_be_nobody_is_not_suggested(self):
        self.add_face(r"D:\Pictures\stranger.jpg", None, "manual", 0)
        self.assertNotIn("People/Rowan Thackeray", self.suggested(r"D:\Pictures\stranger.jpg"))

    def test_a_photo_with_no_face_rows_is_detected_with_the_model_it_is_given(self):
        # The other tests only show detection is skipped; this one needs the model (found in review of 6.5).
        self.assertIn("People/Rowan Thackeray", self.suggested(r"D:\Pictures\new.jpg"))
        self.assertEqual(1, self.processor.calls)

    def test_a_photo_whose_faces_are_all_decided_is_not_detected_again(self):
        self.add_face(r"D:\Pictures\crowd.jpg", None, "manual", 1)
        self.suggested(r"D:\Pictures\crowd.jpg")
        self.assertEqual(0, self.processor.calls)


if __name__ == "__main__":
    unittest.main()

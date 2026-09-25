"""Face resolution and the browser's photo record see one photo whatever its spelling.

Face resolution looked up each face's photo_path in a dict of photos.path. The two
columns need not share a spelling, and where they did not the face lost its photo's
people tags -- the anchors and votes resolution runs on -- without any error. Faces
point at their photo by id since migration 4; a face recorded under another spelling
must still land on the photo's own row.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core import paths
from tagpup.core.dates import record_year
from tagpup.services.photos import page_record
from tests.test_face_clustering_rules import FaceClusteringTestBase, identity_vector
from tagpup.store import db, faces as store_faces


@unittest.skipIf(os.sep == "/", "one separator, so one spelling")
class TestFaceFindsItsPhotoUnderAnotherSpelling(FaceClusteringTestBase):
    def test_a_face_recorded_under_another_spelling_still_anchors(self):
        # A face points at its photo by id now (migration 4), so it cannot be spelled
        # apart from it. What is left to pin: recording a face under another spelling
        # finds the photo's row rather than making a second one without its people.
        vec = identity_vector(3)
        typed = self.add_photo("picnic.jpg", people=["Mira Castellane"])  # forward slashes
        conn = db.connect(self.db_path)
        try:
            # The row as the indexer writes it (paths.stored); the face as typed.
            conn.execute("UPDATE photos SET path = ? WHERE path = ?", (paths.stored(typed), typed))
            face = store_faces.insert(conn, typed, [0, 0, 100, 100], vec.tobytes(), prob=0.99)
            conn.commit()
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
                             "the face made a second row for its photo")
        finally:
            conn.close()

        self.resolve()
        self.assertEqual(self.name_of(face), "Mira Castellane")


class TestPhotoRecordSpelling(unittest.TestCase):
    def test_the_record_carries_the_stored_spelling(self):
        typed = os.path.join(os.path.abspath(os.sep), "Library", "Harbour", "boats.jpg")
        rec = page_record(typed.replace(os.sep, "/"), {})
        self.assertEqual(rec["path"], paths.stored(typed))
        self.assertEqual(rec["filename"], "boats.jpg")

    def test_year_is_read_from_folders_under_either_separator(self):
        for sep in {"/", os.sep}:
            path = sep.join(["", "Library", "Trips 2011", "Coast", "boats.jpg"])
            self.assertEqual(record_year({"path": path}), 2011, path)

    def test_the_filename_year_wins_over_the_folder(self):
        path = os.path.join(os.sep, "Library", "Trips 2011", "boats 2013.jpg")
        self.assertEqual(record_year({"path": path}), 2013)


if __name__ == "__main__":
    unittest.main()

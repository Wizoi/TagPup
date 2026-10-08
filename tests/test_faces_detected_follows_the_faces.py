"""A photo whose face rows are deleted is detected again (docs/findings.md, #779).

faces_detected says detection ran on a photo. Deleting every face row of the photo -- a photo's faces removed, or
the last of them deleted by id -- left the record, so Suggest believed the photo's faces were known and detected
nothing until it was marked to detect again. The delete in the faces store forgets the record of a photo it leaves
with no face. A photo that keeps a face keeps its record; and a detection recorded as a photo's faces are replaced
(record_batch, overwrite) is still recorded.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, faces, faces_detected  # noqa: E402

DETECTOR = faces_detected.detector({"min_face_size": 20})


class Case(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="faces_detected_")
        self.db_path = home.library("harbour.db")
        library_actions.create(self.db_path)
        self.photo = os.path.join(home.root, "Regatta", "a.jpg")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, self.photo, {})
            self.faces = [add_face(conn, self.photo, [0, 0, 10 + n, 10]) for n in range(2)]
            faces_detected.record(conn, self.photo, DETECTOR, None, 2)
            conn.commit()
        finally:
            conn.close()

    def write(self, work):
        return db.write_with_connection(self.db_path, work)

    def recorded(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return faces_detected.detected(conn, self.photo, DETECTOR)
        finally:
            conn.close()

    def test_removing_a_photos_faces_forgets_that_they_were_detected(self):
        self.assertTrue(self.recorded())
        self.assertEqual(2, self.write(lambda conn: faces.remove_for_photo(conn, self.photo)))
        self.assertFalse(self.recorded())

    def test_deleting_the_last_face_forgets_it(self):
        self.write(lambda conn: faces.delete(conn, self.faces[:1]))
        self.assertTrue(self.recorded(), "a photo that keeps a face keeps its record")
        self.write(lambda conn: faces.delete(conn, self.faces[1:]))
        self.assertFalse(self.recorded())

    def test_a_removal_that_removed_nothing_forgets_nothing(self):
        self.write(lambda conn: faces.delete(conn, []))
        self.assertTrue(self.recorded())

    def test_a_detection_that_replaces_the_faces_is_recorded(self):
        found = [{"box": [0, 0, 10, 10], "embedding": [0.0, 1.0], "prob": 0.99}]

        conn = db.connect(self.db_path)
        try:
            face_records.record_batch(conn, {self.photo: found}, overwrite=True, detector=DETECTOR)
        finally:
            conn.close()
        self.assertTrue(self.recorded())


if __name__ == "__main__":
    unittest.main()

"""A photo marked as having faces still to detect (tagpup.store.faces_pending) is marked no
longer once detection has run on it -- wherever it ran, and whether or not it found a face
(docs/findings.md, #407).

The indexer's record_batch clears it; so does Suggest's record_detected, which detects the
faces of a photo it looks at, and replace_detected. A photo in which Suggest found no face
stayed marked, and the next index of its folder detected it all over again for nothing.
The rows are made as the indexer makes them (tests/photo_rows).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import faces as face_records  # noqa: E402
from tagpup.store import db, faces_pending  # noqa: E402

FACE = {"box": [1, 1, 20, 20], "embedding": [0.5] * 512, "prob": 0.99, "crop_image": None}


class Case(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.lib.hold(self.lib.photos)
        self.photo = self.lib.photo("jetty.jpg")
        db.write_with_connection(self.lib.library.path, lambda conn: photo_rows.add_read(conn, self.photo, {}))
        db.write_with_connection(self.lib.library.path, lambda conn: faces_pending.mark(conn, self.photo))
        self.assertEqual(1, self.marked())

    def marked(self):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return faces_pending.count(conn)
        finally:
            conn.close()


class Suggest(Case):
    def test_recording_the_faces_it_found_clears_the_mark(self):
        self.assertEqual(1, face_records.record_detected(self.lib.library.path, self.photo, [FACE]))
        self.assertEqual(0, self.marked())

    def test_finding_no_face_clears_it_too(self):
        self.assertEqual(0, face_records.record_detected(self.lib.library.path, self.photo, []))
        self.assertEqual(0, self.marked(), "detection ran and found no face, and the photo is still marked")


class Replacing(Case):
    def test_replacing_the_faces_clears_the_mark(self):
        conn = db.connect(self.lib.library.path)
        try:
            face_records.replace_detected(conn, self.photo, [FACE])
        finally:
            conn.close()
        self.assertEqual(0, self.marked())


if __name__ == "__main__":
    unittest.main()

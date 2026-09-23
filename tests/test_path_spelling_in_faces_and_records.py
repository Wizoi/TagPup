"""Face resolution and the browser's photo record see one photo whatever its spelling.

Face resolution looked up each face's photo_path in a dict of photos.path. The two
columns need not share a spelling, and where they did not the face lost its photo's
people tags -- the anchors and votes resolution runs on -- without any error.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import paths
from metadata import build_photo_ui_record, parse_year_from_metadata
from tests.test_face_clustering_rules import FaceClusteringTestBase, identity_vector


@unittest.skipIf(os.sep == "/", "one separator, so one spelling")
class TestFaceFindsItsPhotoUnderAnotherSpelling(FaceClusteringTestBase):
    def test_a_face_spelled_differently_from_its_photo_still_anchors(self):
        vec = identity_vector(3)
        photo = self.add_photo("picnic.jpg", people=["Mira Castellane"])  # forward slashes
        face = self.add_face(paths.stored(photo), vec)                    # backslashes

        self.resolve()
        self.assertEqual(self.name_of(face), "Mira Castellane")


class TestPhotoRecordSpelling(unittest.TestCase):
    def test_the_record_carries_the_stored_spelling(self):
        typed = os.path.join(os.path.abspath(os.sep), "Library", "Harbour", "boats.jpg")
        rec = build_photo_ui_record(typed.replace(os.sep, "/"), {})
        self.assertEqual(rec["path"], paths.stored(typed))
        self.assertEqual(rec["filename"], "boats.jpg")

    def test_year_is_read_from_folders_under_either_separator(self):
        for sep in {"/", os.sep}:
            path = sep.join(["", "Library", "Trips 2011", "Coast", "boats.jpg"])
            self.assertEqual(parse_year_from_metadata({"path": path}), 2011, path)

    def test_the_filename_year_wins_over_the_folder(self):
        path = os.path.join(os.sep, "Library", "Trips 2011", "boats 2013.jpg")
        self.assertEqual(parse_year_from_metadata({"path": path}), 2013)


if __name__ == "__main__":
    unittest.main()

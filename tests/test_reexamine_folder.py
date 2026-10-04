"""Re-examine this folder: TagTuner names a folder's unnamed faces that now clearly match
somebody already named (tagpup.services.faces.automatch_folder, POST /api/folder/automatch).

The owner adds a folder, tags people in TagPup, and asks TagTuner to name the faces left.
The page asks first -- "Name 3 faces in 3 photos (Rowan Thackeray 2, ...)?" -- from a
dry run that writes nothing, and the apply decides again. What these pin:

  - a dry run says how many faces, in how many photos, and whose, and writes nothing;
  - applying names what the dry run said when nothing changed in between, and says
    which photos it named faces in;
  - a face named or excluded between the two is not named again, and the counts say so;
  - a person renamed after the named faces were read is not written under the old
    name: that would bring back somebody who no longer exists;
  - a face whose embedding is not the matrix's width is compared with nobody, rather
    than failing the whole folder.
"""
import os
import sys
import unittest

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_face_exclusion import ExclusionTestBase  # noqa: E402
from tests.test_face_clustering_rules import identity_vector, near  # noqa: E402
from tests.test_service_faces import FacesCase, vector  # noqa: E402

from tagpup.services import faces  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import people as store_people  # noqa: E402

ROWAN = "Rowan Thackeray"
WREN = "Wren Halloway"


class ReexamineThroughTheRoute(ExclusionTestBase):
    DB_NAME = "test_reexamine_folder.db"   # in a home of the class's own

    def setUp(self):
        super().setUp()
        rowan, wren = identity_vector(7), identity_vector(8)
        known = self.add_photo("known.jpg", people=[ROWAN, WREN])
        self.add_face(known, rowan, name=ROWAN)
        self.add_face(known, wren, name=WREN)
        self.first, self.second, self.third = (self.add_photo(n) for n in ("one.jpg", "two.jpg", "three.jpg"))
        self.rowan_one = self.add_face(self.first, near(rowan, 1))
        self.rowan_two = self.add_face(self.second, near(rowan, 2))
        self.wren_one = self.add_face(self.third, near(wren, 3))
        self.stranger = self.add_face(self.third, identity_vector(9))

    def reexamine(self, dry_run):
        body = {"folder_path": self.tmpdir}
        if dry_run:
            body["dry_run"] = True
        status, answer = self.post("/api/folder/automatch", body)
        self.assertEqual(status, 200, answer)
        return answer

    def names(self):
        return [self.row(f)["name"] for f in (self.rowan_one, self.rowan_two, self.wren_one, self.stranger)]

    def test_a_dry_run_says_whom_it_would_name_and_writes_nothing(self):
        answer = self.reexamine(dry_run=True)
        self.assertTrue(answer["dry_run"])
        self.assertEqual((answer["faces"], answer["photos"], answer["matched_count"]), (3, 3, 0))
        self.assertEqual(answer["people"], {ROWAN: 2, WREN: 1})
        self.assertEqual(self.names(), [None, None, None, None], "a dry run named a face")
        self.assertNotIn(ROWAN, self.photo_people(self.first))

    def test_a_dry_run_that_is_not_plainly_false_writes_nothing(self):
        # A flag spelled as text is still a rehearsal: the safe side of a mistake.
        status, answer = self.post("/api/folder/automatch", {"folder_path": self.tmpdir, "dry_run": "false"})
        self.assertEqual(status, 200, answer)
        self.assertEqual(self.names(), [None, None, None, None])

    def test_applying_names_what_the_dry_run_said(self):
        said = self.reexamine(dry_run=True)
        done = self.reexamine(dry_run=False)
        self.assertFalse(done["dry_run"])
        self.assertEqual((done["matched_count"], done["faces"], done["photos"], done["people"]),
                         (said["faces"], said["faces"], said["photos"], said["people"]))
        self.assertEqual(self.names(), [ROWAN, ROWAN, WREN, None])
        self.assertEqual(sorted(done["photos_named"]), sorted([self.first, self.second, self.third]))
        self.assertEqual(done["remaining_counts"], {self.third: 1})
        self.assertIn(ROWAN, self.photo_people(self.first))

    def test_a_face_named_in_between_is_not_named_again_and_the_counts_say_so(self):
        self.assertEqual(self.reexamine(dry_run=True)["faces"], 3)
        status, body = self.post("/api/face/match", {"face_id": self.rowan_two, "person_name": "Kit Morrow"})
        self.assertEqual(status, 200, body)
        done = self.reexamine(dry_run=False)
        self.assertEqual((done["matched_count"], done["people"]), (2, {ROWAN: 1, WREN: 1}))
        self.assertEqual(self.row(self.rowan_two)["name"], "Kit Morrow")

    def test_an_excluded_face_is_in_neither_the_dry_run_nor_the_apply(self):
        status, body = self.post("/api/faces/exclude", {"face_ids": [self.wren_one]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.reexamine(dry_run=True)["people"], {ROWAN: 2})
        self.assertEqual(self.reexamine(dry_run=False)["matched_count"], 2)
        self.assertIsNone(self.row(self.wren_one)["name"])


class ReexamineInTheService(FacesCase):
    def setUp(self):
        super().setUp()
        self.rowan = vector(1)
        known = self.photo("known.jpg")
        self.known = self.face(known, name=ROWAN, embedding=self.rowan)
        self.unnamed_photo = self.photo("a.jpg")
        self.lookalike = self.face(self.unnamed_photo, embedding=self.rowan)

    def matrix(self):
        return [self.known], [ROWAN], np.stack([self.rowan])

    def test_a_person_renamed_after_the_named_faces_were_read_is_not_written_by_the_old_name(self):
        def renamed_meanwhile():
            read = self.matrix()
            # The owner renames the person while the faces are being compared.
            db.write_with_connection(self.lib.library.path,
                                     lambda conn: store_people.rename(conn, ROWAN, "Rowan Thackeray-Vale"))
            return read

        result = faces.automatch_folder(self.lib.library, self.folder, renamed_meanwhile)
        self.assertEqual(result.changed, 0)
        self.assertEqual(result.details["renamed"], 1)
        self.assertIsNone(self.face_row(self.lookalike)[0],
                          "a face was named after somebody who no longer exists")
        self.assertNotIn(ROWAN, self.people(self.unnamed_photo))

    def test_a_rehearsal_writes_nothing(self):
        result = faces.automatch_folder(self.lib.library, self.folder, self.matrix, rehearse=True)
        self.assertEqual((result.changed, result.details["faces"], result.details["people"]), (0, 1, {ROWAN: 1}))
        self.assertIsNone(self.face_row(self.lookalike)[0])

    def test_a_face_of_another_width_is_compared_with_nobody(self):
        odd = self.face(self.photo("b.jpg"), embedding=vector(2, dim=4))
        result = faces.automatch_folder(self.lib.library, self.folder, self.matrix)
        self.assertEqual(result.changed, 1)
        self.assertIsNone(self.face_row(odd)[0])
        self.assertEqual(self.face_row(self.lookalike)[0], ROWAN)


if __name__ == "__main__":
    unittest.main()

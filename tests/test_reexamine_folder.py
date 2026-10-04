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
import sqlite3
import sys
import unittest
from unittest import mock

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
from tagpup.store import faces as store_faces  # noqa: E402
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


    def test_unmatch_all_marks_only_the_faces_it_unnames(self):
        """docs/findings.md, #656: Unmatch All marked every face in the photo "nobody",
        the nameless ones too, and Re-examine never named those again."""
        status, body = self.post("/api/face/match", {"face_id": self.wren_one, "person_name": ROWAN})
        self.assertEqual(status, 200, body)
        status, body = self.post("/api/photo/unmatch-all", {"photo_path": self.third})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.row(self.wren_one)["source"], "manual", "the face it unnamed is a decision")
        self.assertIsNone(self.row(self.stranger)["source"], "a face that never had a name was marked nobody")
        self.assertEqual(self.reexamine(dry_run=True)["people"], {ROWAN: 2},
                         "the face unnamed by hand stays nobody; the others are still candidates")


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

    def test_a_rename_committed_elsewhere_while_it_decides_cannot_land_before_its_write(self):
        """docs/findings.md, #645: the guard and the writes are one transaction. A rename
        from another process, tried while automatch decides, either waits for it or --
        had it landed -- would leave the old name unwritten."""
        real = store_faces.names_in_photo
        attempts = []

        def names_in_photo(conn, path):
            found = real(conn, path)
            other = db.connect(self.lib.library.path, timeout=0.1)
            other.execute("PRAGMA busy_timeout=100")   # connect sets the app's 30 s
            try:
                store_people.rename(other, ROWAN, "Rowan Thackeray-Vale")
                other.commit()
                attempts.append("committed")
            except sqlite3.OperationalError:
                attempts.append("held back")
            finally:
                other.close()
            return found

        with mock.patch.object(store_faces, "names_in_photo", side_effect=names_in_photo):
            faces.automatch_folder(self.lib.library, self.folder, self.matrix)
        self.assertEqual(attempts, ["held back"], "the rename landed between the guard and the write")
        self.assertEqual(self.face_row(self.lookalike)[0], ROWAN)

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


class AFolderIsReadFromItsPhotos(FacesCase):
    """docs/findings.md, #644: the folder's queries started from every unnamed face in the
    library and kept those under the folder -- 190,364 for a folder of 2,407. They start
    from the folder's photos, by the path index, and take each one's faces by photo."""

    def plans(self, ask):
        asked = []
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)

        class Recording:
            def __getattr__(self, attr):
                return getattr(conn, attr)

            def execute(self, sql, params=()):
                asked.append((sql, params))
                return conn.execute(sql, params)
        try:
            ask(Recording())
            return [" | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
                    for sql, params in asked if "faces" in sql]
        finally:
            conn.close()

    def test_the_photos_come_first_and_their_faces_by_photo(self):
        photo = self.photo("a.jpg")
        self.face(photo, embedding=vector(1))
        self.face(photo, name=ROWAN, embedding=vector(2))
        for ask in (lambda conn: store_faces.unnamed(conn, folder=self.folder),
                    lambda conn: store_faces.unnamed_counts(conn, self.folder)):
            for plan in self.plans(ask):
                self.assertTrue(plan.startswith("SEARCH p USING COVERING INDEX idx_photos_path_nocase"), plan)
                self.assertIn("idx_faces_photo_id", plan)
                self.assertNotIn("idx_faces_identify", plan)
                self.assertNotIn("idx_faces_name", plan)


class AnExcludedFaceIsNotWaitingForAName(FacesCase):
    """docs/findings.md, #642: Folder Matches and Re-examine's remaining counts took an
    excluded face for an unmatched one: 769 photos in one library were listed only for
    faces ruled out, and nothing could take them off the list."""

    def setUp(self):
        super().setUp()
        self.ruled_out_photo = self.photo("ruled_out.jpg")
        faces.exclude(self.lib.library, [self.face(self.ruled_out_photo, embedding=vector(5))], None)
        self.waiting_photo = self.photo("waiting.jpg")
        self.face(self.waiting_photo, embedding=vector(6))

    def test_folder_matches_does_not_list_a_photo_for_an_excluded_face(self):
        from tagpup.services import identify
        listed = {p["path"]: p["unmatched_count"] for p in identify.photos_waiting(self.lib.library)}
        self.assertEqual(listed, {self.waiting_photo: 1})

    def test_the_remaining_counts_leave_it_out(self):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            self.assertEqual(store_faces.unnamed_counts(conn, self.folder), {self.waiting_photo: 1})
        finally:
            conn.close()


class AFaceUnmatchedByHandStaysNobody(FacesCase):
    """docs/findings.md, #643: automatch took a face the owner had unmatched -- "this is
    nobody", recorded as name_source 'manual' -- for one waiting, and named it."""

    def setUp(self):
        super().setUp()
        self.rowan = vector(1)
        self.face(self.photo("known.jpg"), name=ROWAN, embedding=self.rowan)
        self.photo_path = self.photo("a.jpg")
        self.unmatched = self.face(self.photo_path, embedding=self.rowan)
        faces.name_face(self.lib.library, self.unmatched, ROWAN)
        faces.unname_faces(self.lib.library, [self.unmatched])   # Unmatch Face: the real path

    def named(self):
        return [1], [ROWAN], np.stack([self.rowan])

    def test_re_examining_the_folder_neither_counts_nor_names_it(self):
        plan = faces.automatch_folder(self.lib.library, self.folder, self.named, rehearse=True)
        self.assertEqual(plan.details["faces"], 0)
        self.assertEqual(faces.automatch_folder(self.lib.library, self.folder, self.named).changed, 0)
        self.assertEqual(self.face_row(self.unmatched), (None, "manual", 0))

    def test_automatch_on_its_photo_leaves_it(self):
        self.assertEqual(faces.automatch_photo(self.lib.library, self.photo_path, self.named).changed, 0)
        self.assertIsNone(self.face_row(self.unmatched)[0])

    def test_a_guess_written_directly_leaves_it(self):
        conn = db.connect(self.lib.library.path)
        try:
            self.assertEqual(store_faces.name_if_unnamed(conn, self.unmatched, ROWAN), 0)
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

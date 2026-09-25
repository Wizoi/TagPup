"""An excluded face is never named, and every write says what it actually changed.

Excluding a face clears its name: it has been ruled out of identity work. Nothing on
the naming side checked for it, so a page acting on a stale list of faces -- a cluster's
buttons wired to the faces it was first drawn with -- could name a face that had just
been excluded. The real library holds faces in exactly that state: excluded as an
"ignored cluster", and carrying a manual name.

The page is fixed to act on the faces on screen. These pin the server's half, so a
stale or hand-made request cannot produce the state either:

  - naming one face refuses an excluded one;
  - naming in bulk leaves excluded faces alone and says which it skipped;
  - automatch, per photo and per folder, never names an excluded face, and does not
    take one as the reference for anybody else;
  - exclude and restore report the rows they changed, not the ids they were sent;
  - restore touches only faces that are excluded, so it cannot unpin a manual name.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tests.test_face_exclusion import ExclusionTestBase  # noqa: E402
from tests.test_face_clustering_rules import identity_vector, near  # noqa: E402

import db as tagpup_db  # noqa: E402

NAMED = "Rowan Thackeray"


class ExcludedFaceBase(ExclusionTestBase):
    DB_NAME = "test_excluded_faces_stay_unnamed.db"   # in a home of the class's own

    def exclude(self, *face_ids):
        status, body = self.post("/api/faces/exclude",
                                 {"face_ids": list(face_ids), "reason": "ignored cluster"})
        self.assertEqual(status, 200, body)
        return body

    def set_source(self, face_id, source):
        conn = tagpup_db.connect(self.TEST_DB)
        conn.execute("UPDATE faces SET name_source = ? WHERE id = ?", (source, face_id))
        conn.commit()
        conn.close()


class TestNamingRefusesAnExcludedFace(ExcludedFaceBase):
    def test_match_refuses_an_excluded_face(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.exclude(face)

        status, _ = self.post("/api/face/match", {"face_id": face, "person_name": NAMED})

        self.assertEqual(status, 409)
        row = self.row(face)
        self.assertIsNone(row["name"], "an excluded face was named")
        self.assertEqual(row["excluded"], 1)
        self.assertNotIn(NAMED, self.photo_people(photo))

    def test_match_still_names_a_face_that_is_not_excluded(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        status, body = self.post("/api/face/match", {"face_id": face, "person_name": NAMED})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.row(face)["name"], NAMED)

    def test_match_bulk_skips_excluded_faces_and_says_so(self):
        kept_photo = self.add_photo("kept.jpg")
        ruled_out_photo = self.add_photo("ruled_out.jpg")
        kept = self.add_face(kept_photo, identity_vector(1))
        ruled_out = self.add_face(ruled_out_photo, identity_vector(2))
        self.exclude(ruled_out)

        status, body = self.post("/api/faces/match-bulk",
                                 {"face_ids": [kept, ruled_out], "person_name": NAMED})

        self.assertEqual(status, 200, body)
        self.assertIsNone(self.row(ruled_out)["name"], "an excluded face was named")
        self.assertEqual(self.row(kept)["name"], NAMED)
        self.assertEqual(body.get("matched"), 1, body)
        self.assertEqual(body.get("matched_ids"), [kept], body)
        self.assertEqual(body.get("skipped_excluded"), [ruled_out], body)
        self.assertNotIn(NAMED, self.photo_people(ruled_out_photo),
                         "the photo was said to show somebody on a face ruled out")

    def test_match_bulk_of_only_excluded_faces_changes_nothing(self):
        photos = [self.add_photo("a%d.jpg" % i) for i in range(1, 3)]
        faces = [self.add_face(p, identity_vector(i)) for i, p in enumerate(photos, 1)]
        self.exclude(*faces)

        status, body = self.post("/api/faces/match-bulk",
                                 {"face_ids": faces, "person_name": NAMED})

        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("matched"), 0, body)
        self.assertTrue(all(self.row(f)["name"] is None for f in faces))
        for photo in photos:
            self.assertNotIn(NAMED, self.photo_people(photo))


class TestAutomatchLeavesExcludedFacesAlone(ExcludedFaceBase):
    def _reference_and_excluded_lookalike(self):
        ref_photo = self.add_photo("reference.jpg", people=[NAMED])
        centre = identity_vector(7)
        self.add_face(ref_photo, centre, name=NAMED)
        photo = self.add_photo("crowd.jpg")
        lookalike = self.add_face(photo, near(centre, 1))
        self.exclude(lookalike)
        return photo, lookalike

    def test_photo_automatch_does_not_name_an_excluded_face(self):
        photo, lookalike = self._reference_and_excluded_lookalike()
        status, body = self.post("/api/photo/automatch", {"photo_path": photo})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.row(lookalike)["name"], "automatch named an excluded face")
        self.assertEqual(body.get("matched_count"), 0, body)

    def test_folder_automatch_does_not_name_an_excluded_face(self):
        _photo, lookalike = self._reference_and_excluded_lookalike()
        status, body = self.post("/api/folder/automatch", {"folder_path": self.tmpdir})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.row(lookalike)["name"], "automatch named an excluded face")
        self.assertEqual(body.get("matched_count"), 0, body)

    def test_an_excluded_face_is_not_a_reference_for_automatch(self):
        # A face excluded while carrying a name -- the state the real library holds.
        ref_photo = self.add_photo("ruled_out.jpg")
        centre = identity_vector(9)
        ruled_out = self.add_face(ref_photo, centre)
        self.exclude(ruled_out)
        conn = tagpup_db.connect(self.TEST_DB)
        conn.execute("UPDATE faces SET name = ? WHERE id = ?", (NAMED, ruled_out))
        conn.commit()
        conn.close()

        photo = self.add_photo("next.jpg")
        target = self.add_face(photo, near(centre, 2))
        status, body = self.post("/api/photo/automatch", {"photo_path": photo})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.row(target)["name"],
                          "automatch named a face after one that had been ruled out")


class TestExcludeAndRestoreReportWhatChanged(ExcludedFaceBase):
    def test_exclude_counts_rows_changed_not_ids_sent(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        body = self.exclude(face, 987654)
        self.assertEqual(body.get("excluded"), 1, body)

    def test_restore_counts_rows_changed_not_ids_sent(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.exclude(face)
        status, body = self.post("/api/faces/restore", {"face_ids": [face, 987654]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("restored"), 1, body)

    def test_restore_leaves_a_face_that_was_never_excluded_alone(self):
        photo = self.add_photo("a.jpg", people=[NAMED])
        face = self.add_face(photo, identity_vector(1), name=NAMED)
        self.set_source(face, "manual")

        status, body = self.post("/api/faces/restore", {"face_ids": [face]})

        self.assertEqual(status, 200, body)
        self.assertEqual(self.row(face)["source"], "manual",
                         "restore unpinned a manual name on a face that was never excluded")
        self.assertEqual(body.get("restored"), 0, body)


if __name__ == "__main__":
    unittest.main()

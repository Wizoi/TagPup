"""Grouping keeps a name its photo's own keywords confirm, even below 0.80 (docs/findings.md, #855; the owner, 2026-10-08).

Grouping (tagpup.services.identities.resolve) kept a name it gave only while the face reached 0.80 of the person
(clustering.names_unasked), so the 0.70 to 0.80 names the photos' tags had given -- faces-from-tags writes them -- were among
those it took away: 2,639 fewer named faces than before it ran, on a copy of photo_index. A name the photo's keywords
bear out is the tag's own evidence; it stays whatever the likeness, as is every name given by hand.

The likeness of the faces of one photo is stood in for (0.75, between OFFER_A_NAME and 0.80), since with the synthetic
vectors of the other clustering tests a face of a person's cluster is always far above it. The rest is the real rule.
"""
import os
import sqlite3
import sys
import unittest

import numpy as np
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector, near  # noqa: E402

from tagpup.core import clustering  # noqa: E402

REAL = clustering.KnownFaces.likeness


def like_by(value, photo_name):
    """KnownFaces.likeness, but `value` for the faces of the photo called `photo_name`."""
    def likeness(self, name, embedding, year=None, photo=None):
        if photo and str(photo).replace("\\", "/").endswith(photo_name):
            return value
        return REAL(self, name, embedding, year, photo)
    return likeness


class GroupingAndTheTagsOfAPhoto(FaceClusteringTestBase):
    def two_people_in_one_photo(self):
        jane, bob = identity_vector(1), identity_vector(2)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)
        crowd = self.add_photo("crowd.jpg", people=["Jane Doe", "Bob Roe"])
        return (self.add_face(crowd, near(jane, 7), box=(0, 0, 400, 400)),
                self.add_face(crowd, near(bob, 8), box=(0, 0, 400, 400)))

    def test_a_name_the_photos_keywords_confirm_is_kept_between_070_and_080(self):
        self.assertGreaterEqual(0.75, clustering.OFFER_A_NAME)
        self.assertFalse(clustering.names_unasked(0.75))
        jane_face, bob_face = self.two_people_in_one_photo()
        with mock.patch.object(clustering.KnownFaces, "likeness", like_by(0.75, "crowd.jpg")):
            self.resolve()
        self.assertEqual(("Jane Doe", "Bob Roe"), (self.name_of(jane_face), self.name_of(bob_face)))

    def test_it_is_kept_below_070_too(self):
        jane_face, bob_face = self.two_people_in_one_photo()
        with mock.patch.object(clustering.KnownFaces, "likeness", like_by(0.40, "crowd.jpg")):
            self.resolve()
        self.assertEqual(("Jane Doe", "Bob Roe"), (self.name_of(jane_face), self.name_of(bob_face)))

    def test_the_names_of_a_photo_the_keywords_do_not_bear_out_are_not_given(self):
        # The same faces in a photo tagged with neither: nothing confirms a name, and grouping gives none.
        jane, bob = identity_vector(1), identity_vector(2)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)
        untagged = self.add_photo("untagged.jpg")
        faces = [self.add_face(untagged, near(jane, 7)), self.add_face(untagged, near(bob, 8))]
        self.resolve()
        self.assertEqual([None, None], [self.name_of(face) for face in faces])

    def test_a_name_given_by_hand_still_holds(self):
        jane, bob = identity_vector(1), identity_vector(2)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)
        crowd = self.add_photo("crowd.jpg", people=["Jane Doe", "Bob Roe"])
        by_hand = self.add_face(crowd, near(jane, 7), manual_name="Jane Doe")
        self.add_face(crowd, near(bob, 8))
        with mock.patch.object(clustering.KnownFaces, "likeness", like_by(0.75, "crowd.jpg")):
            self.resolve()
        self.assertEqual("Jane Doe", self.name_of(by_hand))


def like(reference, cosine, seed):
    """A unit vector whose cosine with `reference` is `cosine`."""
    other = identity_vector(seed)
    other = other - np.dot(other, reference) * reference
    other /= np.linalg.norm(other)
    out = cosine * reference + float(np.sqrt(1 - cosine ** 2)) * other
    return (out / np.linalg.norm(out)).astype(np.float32)


class TheFinalMatchingPass(FaceClusteringTestBase):
    """docs/findings.md, #902: a face whose cluster has no anchor is not named by the loop and reaches the final matching pass
    unnamed. That pass named it only from 0.80 (names_unasked) even when its photo's keywords name the person; confirmed on a
    copy of kr-track, which lost names at 0.7331 and 0.7784. Nothing is stood in for: the vectors are real, the likeness is the
    store's own, and the rule is the real final pass."""

    def a_face_like_jane(self, cosine):
        jane = identity_vector(1)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        # Its cluster is three photos, one tagged with Jane: Jane is on a third of them, short of the majority vote, and
        # a photo of two faces is no anchor. So nothing names it before the final pass.
        wanted = like(jane, cosine, 5)
        crowd = self.add_photo("crowd.jpg", people=["Jane Doe"])
        face = self.add_face(crowd, wanted, box=(0, 0, 400, 400))
        stranger = identity_vector(9)       # the other face's cluster is as short of a majority
        self.add_face(crowd, stranger, box=(0, 0, 400, 400))
        for number in range(1, 3):
            self.add_face(self.add_photo("plain_%d.jpg" % number), near(wanted, 10 + number, 0.03))
            self.add_face(self.add_photo("other_%d.jpg" % number), near(stranger, 20 + number, 0.03))
        return face

    def test_a_face_at_0_73_of_a_person_its_photos_keywords_name_is_named(self):
        face = self.a_face_like_jane(0.7331)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))
        self.assertIn("final_matching_tagged_photo", self.traces()[face]["resolution_method"])

    def test_and_at_0_78(self):
        face = self.a_face_like_jane(0.7784)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))

    def test_below_0_70_it_is_not_offered_and_stays_unnamed(self):
        face = self.a_face_like_jane(0.60)
        self.resolve()
        self.assertIsNone(self.name_of(face))

    def test_a_person_the_photos_keywords_do_not_name_is_not_given(self):
        jane = identity_vector(1)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        photo = self.add_photo("other.jpg", people=["Bob Roe"])
        face = self.add_face(photo, like(jane, 0.75, 5), box=(0, 0, 400, 400))
        self.add_face(photo, identity_vector(9), box=(0, 0, 400, 400))
        self.resolve()
        self.assertNotEqual("Jane Doe", self.name_of(face))

    def test_a_name_given_by_hand_is_not_changed_by_it(self):
        face = self.a_face_like_jane(0.7331)
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE faces SET name = 'Ada Marchetti', name_source = 'manual' WHERE id = ?", (face,))
        conn.commit()
        conn.close()
        self.resolve()
        self.assertEqual("Ada Marchetti", self.name_of(face))


if __name__ == "__main__":
    unittest.main()

"""Grouping keeps a name its photo's own keywords confirm, even below 0.80 (docs/findings.md, #855; the owner, 2026-10-08).

Grouping (tagpup.services.identities.resolve) kept a name it gave only while the face reached 0.80 of the person
(clustering.names_unasked), so the 0.70 to 0.80 names the photos' tags had given -- faces-from-tags writes them -- were among
those it took away: 2,639 fewer named faces than before it ran, on a copy of photo_index. A name the photo's keywords
bear out is the tag's own evidence; it stays whatever the likeness, as is every name given by hand.

The likeness of the faces of one photo is stood in for (0.75, between OFFER_A_NAME and 0.80), since with the synthetic
vectors of the other clustering tests a face of a person's cluster is always far above it. The rest is the real rule.
"""
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()

"""Clustering keeps a name its photo's own keywords confirm, even below naming unasked (docs/findings.md, #855; the owner, 2026-10-08).

Clustering (tagpup.services.identities.resolve, which the CLI's index runs after an index that recorded a photo; the Group button it
was first written for is gone) kept a name it gave only while the face reached 0.80 of the person
(clustering.names_unasked), so the 0.70 to 0.80 names the photos' tags had given -- faces-from-tags writes them -- were among
those it took away: 2,639 fewer named faces than before it ran, on a copy of photo_index. A name the photo's keywords
bear out is the tag's own evidence; it stays whatever the likeness, as is every name given by hand.

The numbers of 2026-10-08 were FaceNet's (0.70 to offer a name, 0.80 to name unasked); a library is now on AdaFace, whose
cuts (CUTS: clustering.cuts_of the library's face model) are 0.310 and 0.520, and the likenesses here are read under them.

Nothing is stood in for: the face is a real vector at BETWEEN (between offering a name and naming unasked) or UNDER (below
offering) of the person's one named face, joined to her cluster by a chain of faces in untagged photos (DBSCAN groups by
single links, at the model's radius), so her cluster names it; the likeness a round would ask is the one gallery's
(FaceRows.likeness_of), and the rule keeps the name without it. Between the two the final matching pass would give the name
back too (TheFinalMatchingPass, below); below offering the kept rule alone holds it (the UNDER test fails with the rule
taken out, checked 2026-10-22 on FaceNet's numbers).
"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import CUTS, FaceClusteringTestBase, identity_vector, like, near  # noqa: E402

from tagpup.store import db  # noqa: E402

#: A likeness a name is offered at but not given unasked, and one below offering, under the library's model's cuts.
BETWEEN = 0.42
UNDER = 0.15


class GroupingAndTheTagsOfAPhoto(FaceClusteringTestBase):
    def two_people_in_one_photo(self, cosine):
        """Jane's face in a photo of her own, Bob's in his; then a photo of the two of them whose keywords name both, its
        first face a real vector at `cosine` of Jane's, its second Bob's. Faces in untagged photos lie between Jane's and
        the first, each a short step from the last: the first is in Jane's cluster, which names it, at `cosine` of her
        one named face. Returns (that face, Bob's face)."""
        jane, bob = identity_vector(1), identity_vector(2)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)
        far = like(jane, cosine, 5)
        steps = between(jane, far, CUTS.group_distance)
        for number, step in enumerate(steps):
            self.add_face(self.add_photo("step_%d.jpg" % number), step)
        crowd = self.add_photo("crowd.jpg", people=["Jane Doe", "Bob Roe"])
        jane_face = self.add_face(crowd, far, box=(0, 0, 400, 400))
        return jane_face, self.add_face(crowd, near(bob, 8), box=(0, 0, 400, 400))

    def test_a_name_the_photos_keywords_confirm_is_kept_between_offering_and_naming_unasked(self):
        self.assertTrue(CUTS.is_offered(BETWEEN, math.inf))
        self.assertFalse(CUTS.names_unasked(BETWEEN, math.inf))
        jane_face, bob_face = self.two_people_in_one_photo(BETWEEN)
        self.resolve()
        self.assertEqual(("Jane Doe", "Bob Roe"), (self.name_of(jane_face), self.name_of(bob_face)))

    def test_it_is_kept_below_offering_too(self):
        self.assertFalse(CUTS.is_offered(UNDER, math.inf))
        jane_face, bob_face = self.two_people_in_one_photo(UNDER)
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
        by_hand = self.add_face(crowd, like(jane, BETWEEN, 5), manual_name="Jane Doe")
        self.add_face(crowd, near(bob, 8))
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(by_hand))


def between(start, end, reach):
    """Unit vectors on the arc from `start` to `end` (both unit), neither end included, each within half of `reach` (a
    distance between unit vectors) of the one before: a chain DBSCAN joins at `reach`."""
    angle = float(np.arccos(np.clip(np.dot(start, end), -1.0, 1.0)))
    count = int(np.ceil(angle / (2 * np.arcsin(reach / 4)))) + 1
    found = []
    for n in range(1, count):
        t = n / count
        out = (np.sin((1 - t) * angle) * start + np.sin(t * angle) * end) / np.sin(angle)
        found.append((out / np.linalg.norm(out)).astype(np.float32))
    return found


class TheFinalMatchingPass(FaceClusteringTestBase):
    """docs/findings.md, #902: a face whose cluster has no anchor is not named by the loop and reaches the final matching pass
    unnamed. That pass named it only from naming unasked (names_unasked) even when its photo's keywords name the person;
    confirmed on a copy of kr-track, which lost names at FaceNet's 0.7331 and 0.7784 (here AdaFace's 0.38 and 0.48, as far
    between its two cuts). Nothing is stood in for: the vectors are real, the likeness is the store's own, and the rule is the
    real final pass."""

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

    def test_a_face_offered_a_person_its_photos_keywords_name_is_named(self):
        self.assertFalse(CUTS.names_unasked(0.38, math.inf))
        face = self.a_face_like_jane(0.38)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))
        self.assertIn("final_matching_tagged_photo", self.traces()[face]["resolution_method"])

    def test_and_nearer_naming_unasked(self):
        self.assertFalse(CUTS.names_unasked(0.48, math.inf))
        face = self.a_face_like_jane(0.48)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))

    def test_below_offering_it_is_not_offered_and_stays_unnamed(self):
        self.assertFalse(CUTS.is_offered(0.25, math.inf))
        face = self.a_face_like_jane(0.25)
        self.resolve()
        self.assertIsNone(self.name_of(face))

    def test_a_person_the_photos_keywords_do_not_name_is_not_given(self):
        jane = identity_vector(1)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        photo = self.add_photo("other.jpg", people=["Bob Roe"])
        face = self.add_face(photo, like(jane, BETWEEN, 5), box=(0, 0, 400, 400))
        self.add_face(photo, identity_vector(9), box=(0, 0, 400, 400))
        self.resolve()
        self.assertNotEqual("Jane Doe", self.name_of(face))

    def test_a_name_given_by_hand_is_not_changed_by_it(self):
        face = self.a_face_like_jane(0.38)
        conn = db.connect(self.db_path)
        conn.execute("UPDATE faces SET name = 'Ada Marchetti', name_source = 'manual' WHERE id = ?", (face,))
        conn.commit()
        conn.close()
        self.resolve()
        self.assertEqual("Ada Marchetti", self.name_of(face))


if __name__ == "__main__":
    unittest.main()

"""Clustering keeps a name its photo's own keywords confirm, even below the likeness that names unasked (docs/findings.md,
#855; the owner, 2026-10-08).

Clustering (tagpup.services.identities.resolve, which the CLI's index runs after an index that recorded a photo; the Group button it
was first written for is gone) kept a name it gave only while the face reached the likeness that names unasked
(clustering.names_unasked), so the names between offering and naming unasked the photos' tags had given -- faces-from-tags
writes them -- were among those it took away: 2,639 fewer named faces than before it ran, on a copy of photo_index (FaceNet's
vectors then). A name the photo's keywords bear out is the tag's own evidence; it stays whatever the likeness, as is every
name given by hand.

Nothing is stood in for: the face is a real vector, of the face model a new library is on (SCRFD and AdaFace's cuts:
offered from 0.31, named unasked from 0.52), at 0.40 (between the two) or 0.20 (below both) of the person's one named face,
joined to her cluster by a chain of faces in untagged photos (DBSCAN groups by single links), so her cluster names it; the
likeness a round would ask is the one gallery's (FaceRows.likeness_of), and the rule keeps the name without it. Between the
two the final matching pass would give the name back too (TheFinalMatchingPass, below); below the offer the kept rule alone
holds it (the 0.20 test fails with the rule taken out, checked 2026-10-26).
"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector, near  # noqa: E402
from face_rows import FACE_MODEL  # noqa: E402

from tagpup.core import clustering  # noqa: E402
from tagpup.store import db  # noqa: E402

#: The cuts of the face model a new library is on, which the faces' vectors are filed under.
CUTS = clustering.cuts_of(FACE_MODEL)
#: Between the likeness a name is offered at and the one it is given unasked at.
BETWEEN = 0.40
#: Below the likeness a name is offered at.
BELOW = 0.20


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

    def test_it_is_kept_below_the_offer_too(self):
        self.assertFalse(CUTS.is_offered(BELOW, math.inf))
        jane_face, bob_face = self.two_people_in_one_photo(BELOW)
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


def like(reference, cosine, seed):
    """A unit vector whose cosine with `reference` is `cosine`."""
    other = identity_vector(seed)
    other = other - np.dot(other, reference) * reference
    other /= np.linalg.norm(other)
    out = cosine * reference + float(np.sqrt(1 - cosine ** 2)) * other
    return (out / np.linalg.norm(out)).astype(np.float32)


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
    unnamed. That pass named it only from the likeness that names unasked (names_unasked) even when its photo's keywords name
    the person; confirmed on a copy of kr-track (FaceNet's vectors then). Nothing is stood in for: the vectors are real, the
    likeness is the store's own, and the rule is the real final pass."""

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

    def test_a_face_between_offering_and_naming_unasked_of_a_person_its_photos_keywords_name_is_named(self):
        face = self.a_face_like_jane(BETWEEN)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))
        self.assertIn("final_matching_tagged_photo", self.traces()[face]["resolution_method"])

    def test_and_just_short_of_naming_unasked(self):
        face = self.a_face_like_jane(CUTS.name_without_asking - 0.01)
        self.resolve()
        self.assertEqual("Jane Doe", self.name_of(face))

    def test_below_the_offer_it_is_not_offered_and_stays_unnamed(self):
        face = self.a_face_like_jane(BELOW)
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
        face = self.a_face_like_jane(BETWEEN)
        conn = db.connect(self.db_path)
        conn.execute("UPDATE faces SET name = 'Ada Marchetti', name_source = 'manual' WHERE id = ?", (face,))
        conn.commit()
        conn.close()
        self.resolve()
        self.assertEqual("Ada Marchetti", self.name_of(face))


if __name__ == "__main__":
    unittest.main()

"""cluster-faces after an index, on a library shaped like the owner's: a group photo of twenty faces tagged with twenty people,
a run of photos that names one more person each round until the rounds run out, a face named by hand among them, and a last
pass that compares the faces left over with everyone at once.

It took 34 minutes on photo_index (59,519 photos, 187,784 faces), 32 of them in the last pass, which asked how like each
person every face left unnamed was, one face and one person at a time, untagged photos included (docs/findings.md, #1530).
The names must be what they were; only the time changes.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector  # noqa: E402

from tagpup.core import clustering  # noqa: E402
from tagpup.services import identities  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402

PEOPLE = ["Tamsin Okafor", "Bram Lindqvist", "Ines Whitcombe", "Oskar Penhaligon", "Noor Castellan", "Ezra Dunmore",
          "Mira Achterberg", "Silas Varga", "Juno Prideaux", "Cato Ellingham", "Wren Abernathy", "Felix Ostrowski",
          "Ada Kincaid", "Rafe Mallory", "Lise Montague", "Hugo Brannigan", "Talia Voss", "Milo Ferreira",
          "Greta Holloway", "Idris Calloway"]


def like(reference, cosine, seed):
    """A unit vector whose cosine with `reference` is `cosine`: 0.83 names unasked (0.80) but does not group (0.885)."""
    other = identity_vector(seed)
    other = other - np.dot(other, reference) * reference
    other /= np.linalg.norm(other)
    out = cosine * reference + float(np.sqrt(1 - cosine ** 2)) * other
    return (out / np.linalg.norm(out)).astype(np.float32)


class ClusterFacesOnACrowdedLibrary(FaceClusteringTestBase):
    def run_cluster_faces(self):
        """cluster-faces as the CLI's index runs it after an index; the steps it reported, in order."""
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        steps = []
        identities.resolve(self.index, on_step=lambda stage, done=0, total=1: steps.append((stage, done)))
        return steps

    def rounds(self, steps):
        return sorted({done for stage, done in steps if stage == "propagating"})

    def a_chain(self, length, by_hand=False):
        """Photo 0 is the first person alone; photo k holds person k-1 and person k, each tag known only once the round
        before named the face of photo k-1: each round names one more photo. Returns the faces of each person, in order."""
        first = identity_vector(100)
        faces = [self.add_face(self.add_photo("chain_0.jpg", people=[PEOPLE[0]]), first)]
        known = first
        for k in range(1, length + 1):
            photo = self.add_photo("chain_%d.jpg" % k, people=[PEOPLE[k - 1], PEOPLE[k]])
            self.add_face(photo, like(known, 0.83, 200 + k), box=(0, 0, 300, 300))
            known = identity_vector(100 + k)
            faces.append(self.add_face(photo, known, box=(400, 0, 700, 300),
                                       manual_name=PEOPLE[k] if by_hand and k == 2 else None))
        return faces

    def test_twenty_faces_tagged_with_twenty_people_are_each_named_once(self):
        anchors = [identity_vector(10 + n) for n in range(20)]
        for n, vector in enumerate(anchors):
            self.add_face(self.add_photo("alone_%d.jpg" % n, people=[PEOPLE[n]]), vector)
        group = self.add_photo("group.jpg", people=list(PEOPLE))
        faces = [self.add_face(group, like(vector, 0.83, 50 + n), box=(n * 100, 0, n * 100 + 90, 90))
                 for n, vector in enumerate(anchors)]
        self.run_cluster_faces()
        self.assertEqual(PEOPLE, [self.name_of(face) for face in faces])

    def test_the_rounds_stop_when_one_names_nobody_new(self):
        faces = self.a_chain(2)
        steps = self.run_cluster_faces()
        self.assertEqual([0, 1, 2], self.rounds(steps), "a third round named nobody, and the loop stopped after it")
        self.assertEqual(PEOPLE[:3], [self.name_of(face) for face in faces])

    def test_the_rounds_stop_at_the_most_allowed_when_every_one_names_somebody(self):
        faces = self.a_chain(identities.MAX_ITERATIONS + 2)
        steps = self.run_cluster_faces()
        self.assertEqual(list(range(identities.MAX_ITERATIONS)), self.rounds(steps))
        last = identities.MAX_ITERATIONS
        self.assertEqual(PEOPLE[:last + 1], [self.name_of(face) for face in faces[:last + 1]])
        self.assertEqual([None, None], [self.name_of(face) for face in faces[last + 1:]],
                         "a face the rounds never reached was named")

    def test_a_face_named_by_hand_keeps_its_name_through_every_round(self):
        faces = self.a_chain(identities.MAX_ITERATIONS + 2, by_hand=True)
        self.run_cluster_faces()
        self.assertEqual(PEOPLE[2], self.name_of(faces[2]))
        self.assertEqual("manual_override_preserved", self.traces()[faces[2]]["resolution_method"])

    def test_the_last_pass_compares_the_faces_left_with_everyone_at_once(self):
        """The faces the rounds leave reach the last pass: it names the one its photo's tags bear out, leaves the untagged
        photo's alone, and asks no likeness one face and one person at a time (#1530)."""
        anchors = [identity_vector(10 + n) for n in range(6)]
        for n, vector in enumerate(anchors):
            self.add_face(self.add_photo("alone_%d.jpg" % n, people=[PEOPLE[n]]), vector)
        tagged = self.add_photo("tagged.jpg", people=[PEOPLE[0], PEOPLE[7]])
        left = [self.add_face(tagged, like(anchors[0], 0.75, 60), box=(0, 0, 300, 300)),
                self.add_face(tagged, identity_vector(61), box=(400, 0, 700, 300)),
                self.add_face(tagged, identity_vector(62), box=(800, 0, 1100, 300))]
        untagged = self.add_face(self.add_photo("untagged.jpg"), like(anchors[1], 0.95, 63))
        asked, stage = [], []
        whole, one = clustering.FaceRows.likeness, clustering.FaceRows.likeness_of

        def counted(real, kind):
            def ask(known, *args, **kwargs):
                asked.append((kind, stage[-1] if stage else None))
                return real(known, *args, **kwargs)
            return ask

        def heard(name, done=0, total=1):
            stage.append(name)
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        with mock.patch.object(clustering.FaceRows, "likeness", counted(whole, "everyone")), \
                mock.patch.object(clustering.FaceRows, "likeness_of", counted(one, "one person")):
            identities.resolve(self.index, on_step=heard)
        self.assertEqual([PEOPLE[0], None, None], [self.name_of(face) for face in left])
        self.assertIsNone(self.name_of(untagged))
        self.assertEqual(0, asked.count(("one person", "matching")),
                         "the last pass asked likeness face by face and person by person")
        self.assertIn(("everyone", "matching"), asked, "the last pass asks the faces left against everyone at once")


if __name__ == "__main__":
    unittest.main()

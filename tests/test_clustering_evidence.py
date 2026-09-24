"""Clustering's evidence is what people wrote, never what clustering guessed last time.

A photo's `people` holds its keyword people and the names on its faces -- including
names clustering assigned itself (see metadata.photo_people). Clustering read that
list as evidence: a lone face it had once named became a "direct anchor" for the same
name on the next run, and a wrong guess voted for itself for ever. `--reset` rebuilt
the list first and was safe; an ordinary recluster was not.

Evidence is now the keyword people, and names somebody gave a face by hand.
"""
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector, near  # noqa: E402

from tagpup.store import db, taxonomy  # noqa: E402

GUESS = "Rowan Thackeray"


class ClusteringDoesNotTrustItsOwnGuesses(FaceClusteringTestBase):
    def setUp(self):
        super().setUp()
        # People holds faces because the library's tree says so, as a real library's
        # does: no root holds faces for its name (docs/findings.md, #66).
        db.write_with_connection(
            self.db_path, lambda conn: taxonomy.add_path(conn, "People", root_has_face=1))

    def add_guessed_face(self, photo_path, embedding, name):
        """A face clustering named, as it leaves it: a name, no manual source."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
            (photo_path, json.dumps([0, 0, 100, 100]), embedding.tobytes(), name, 0.99))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def set_tags(self, photo_path, tags):
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE photos SET tags = ?, raw_metadata = ? WHERE path = ?",
                     (json.dumps(tags), json.dumps({"XMP:HierarchicalSubject": tags}), photo_path))
        conn.commit()
        conn.close()

    def test_a_guess_is_not_its_own_anchor(self):
        face_vec = identity_vector(3)
        # people holds the guess only because the face carries it.
        photo = self.add_photo("lone.jpg", people=[GUESS])
        face = self.add_guessed_face(photo, face_vec, GUESS)

        self.resolve()

        self.assertIsNone(self.name_of(face),
                          "a name clustering guessed was kept on no evidence but itself")

    def test_the_same_name_from_a_keyword_still_anchors(self):
        face_vec = identity_vector(3)
        photo = self.add_photo("tagged.jpg", people=[GUESS])
        self.set_tags(photo, ["People/" + GUESS])
        face = self.add_guessed_face(photo, face_vec, GUESS)

        self.resolve()

        self.assertEqual(GUESS, self.name_of(face))

    def test_a_name_given_by_hand_still_anchors_its_cluster(self):
        face_vec = identity_vector(4)
        named = self.add_photo("named.jpg", people=[GUESS])
        self.add_face(named, face_vec, manual_name=GUESS)
        # The same person in a photo that names them, with a second face beside them.
        other = self.add_photo("pair.jpg", people=[GUESS])
        self.set_tags(other, ["People/" + GUESS])
        second = self.add_face(other, near(face_vec, 1))
        self.add_face(other, identity_vector(9))

        self.resolve()

        self.assertEqual(GUESS, self.name_of(second))


if __name__ == "__main__":
    unittest.main()

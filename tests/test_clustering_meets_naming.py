"""A hand-run cluster-faces reads the faces at its start and writes their names at its end (53 minutes on the
68,000-photo library). A person who names a face in TagTuner in between keeps that name: the clustering's guess does not
overwrite it, the photo's keyword and the face stay in step, and the run says how many faces it left (docs/findings.md, #832).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector, near  # noqa: E402

from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402


class ClusteringMeetsNaming(FaceClusteringTestBase):
    def run_while(self, meanwhile):
        """cluster-faces, with `meanwhile(conn)` written by another connection just before it writes its names;
        returns the faces it said it left."""
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        heard = []

        def on_step(stage, done=0, total=1):
            if stage == "saving":
                db.write_with_connection(self.db_path, meanwhile, label="a person names a face")
            if stage == "left":
                heard.append(done)
        from tagpup.services import identities
        identities.resolve(self.index, on_step=on_step)
        return heard

    def row(self, face_id):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT name, name_source FROM faces WHERE id = ?", (face_id,)).fetchone()
        finally:
            conn.close()

    def two_faces_of_jane(self):
        jane = identity_vector(1)
        first = self.add_face(self.add_photo("one.jpg", people=["Jane Doe"]), jane)
        second = self.add_face(self.add_photo("two.jpg", people=["Jane Doe"]), near(jane, 2))
        return first, second

    def test_a_face_named_by_hand_while_clustering_runs_keeps_the_owners_name(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: store_faces.name(conn, [second], "Bob Roe"))

        self.assertEqual(("Bob Roe", "manual"), self.row(second), "the owner's naming was overwritten by the guess")
        self.assertEqual(("Jane Doe", None), self.row(first), "the face nobody touched is named as before")
        self.assertEqual([1], heard, "the run says how many faces it left")

    def test_a_face_another_run_named_meanwhile_is_not_written_over(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: store_faces.name_if_unnamed(conn, second, "Bob Roe"))

        self.assertEqual("Bob Roe", self.row(second)[0], "a face that carries another person than the one read is left")
        self.assertEqual([1], heard)

    def test_a_face_nobody_touched_is_all_written_and_nothing_is_said_of_leaving(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: None)

        self.assertEqual([self.row(first)[0], self.row(second)[0]], ["Jane Doe", "Jane Doe"])
        self.assertEqual([], heard)


if __name__ == "__main__":
    unittest.main()

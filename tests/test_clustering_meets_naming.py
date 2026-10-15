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
from tagpup.store import taxonomy  # noqa: E402


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


    def id_of(self, face_id):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT tag_id FROM faces WHERE id = ?", (face_id,)).fetchone()[0]
        finally:
            conn.close()

    def test_a_person_renamed_while_clustering_runs_keeps_the_faces_guessed_for_them(self):
        """#1271: the guess is for the person (their id), not for the spelling the run read."""
        first, second = self.two_faces_of_jane()

        def rename(conn):
            tag = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Jane Doe'").fetchone()[0]
            taxonomy.move_branch(conn, tag, tag[:-len("Jane Doe")] + "Jane Roe")

        self.run_while(rename)

        self.assertEqual(("Jane Roe", None), self.row(first), "the guess was lost to the rename")
        self.assertEqual(("Jane Roe", None), self.row(second))
        self.assertEqual(1, len({self.id_of(first), self.id_of(second)}))
        self.assertIsNotNone(self.id_of(first))

    def test_a_person_named_by_hand_on_one_face_is_not_guessed_on_another_of_the_photo(self):
        """#1272: the owner names face B of a photo while the run guesses the same person for face A of it."""
        jane = identity_vector(1)
        self.add_face(self.add_photo("one.jpg", people=["Jane Doe"]), jane)
        photo = self.add_photo("two.jpg", people=["Jane Doe"])
        guessed = self.add_face(photo, near(jane, 2), box=(0, 0, 50, 50))
        chosen = self.add_face(photo, identity_vector(5), box=(60, 0, 110, 50))

        heard = self.run_while(lambda conn: store_faces.name(conn, [chosen], "Jane Doe"))

        self.assertEqual(("Jane Doe", "manual"), self.row(chosen))
        self.assertEqual((None, None), self.row(guessed), "the photo ended with two faces of one person")
        self.assertEqual([2], heard, "the run says it left both: the one the owner named, and the one that would double the person")

    def test_a_person_merged_away_while_clustering_runs_is_nobody_to_give_and_the_run_finishes(self):
        first, second = self.two_faces_of_jane()
        self.add_face(self.add_photo("three.jpg", people=["Bob Roe"]), identity_vector(7))

        def merge(conn):
            jane = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Jane Doe'").fetchone()[0]
            bob = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Bob Roe'").fetchone()[0]
            taxonomy.move_branch(conn, jane, bob)

        heard = self.run_while(merge)

        self.assertEqual((None, None), self.row(first), "a person who is gone was given to a face")
        self.assertEqual((None, None), self.row(second))
        self.assertEqual([2], heard)


if __name__ == "__main__":
    unittest.main()

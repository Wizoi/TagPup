"""A hand-run cluster-faces reads the faces at its start and writes their names at its end (53 minutes on the
68,000-photo library). A person who names a face in TagTuner in between keeps that name: the clustering's guess does not
overwrite it, the photo's keyword and the face stay in step, and the run says how many faces it left (docs/findings.md, #832).
A photo whose keywords name two people nobody's face is known for keeps its two faces unnamed, and the run says how many it
left so (#1374).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
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
            if stage == "unpaired":
                self.unpaired.append(done)
        self.unpaired = []
        from tagpup.services import identities
        self.stats = identities.resolve(self.index, on_step=on_step)
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

    def test_a_guess_moved_from_one_face_of_a_photo_to_another_is_written_and_counted(self):
        """A re-run that moves a person from face 2 of a photo to face 1: face 1 is not refused for the person face 2 carried a
        moment before, and the photo does not end with nobody."""
        photo = self.add_photo("pair.jpg", people=["Jane Doe"])
        low = self.add_face(photo, identity_vector(1), box=(0, 0, 50, 50))
        high = self.add_face(photo, identity_vector(5), box=(60, 0, 110, 50))
        from tagpup.store import person_ids

        def guessed(conn):
            conn.execute("UPDATE faces SET name = 'Jane Doe', tag_id = ? WHERE id = ?", (face_rows.person_of(conn, "Jane Doe"), high))

        db.write_with_connection(self.db_path, guessed, label="an earlier run guessed")
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            jane = person_ids.read(conn).person("Jane Doe").id
        finally:
            conn.close()

        written, left = db.write_with_connection(
            self.db_path, lambda conn: store_faces.set_names(conn, {low: jane, high: None}, {low: None, high: jane}),
            label="a run moves the guess")

        self.assertEqual([low, high], sorted(written))
        self.assertEqual([], left)
        self.assertEqual(jane, self.id_of(low))
        self.assertIsNone(self.id_of(high))

    def test_the_summary_counts_the_faces_written_not_the_faces_meant(self):
        first, second = self.two_faces_of_jane()

        self.run_while(lambda conn: store_faces.name(conn, [second], "Bob Roe"))

        self.assertEqual({"Jane Doe": 1}, self.stats, "a face the owner named meanwhile was counted as linked")

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
        self.assertEqual([1], heard, "the run says it left the one face that would double the person")

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

    def photo_tagged(self, name, people):
        """A photo whose keywords are People/<person> for each of `people`, as the index records a file it read: the tree's nodes,
        the keywords, the photo's people."""
        tags = ["People/" + person for person in people]
        db.write_with_connection(self.db_path, lambda conn: [taxonomy.add_path(conn, "People", root_has_face=1)]
                                 + [taxonomy.add_path(conn, tag) for tag in tags])
        photo = self.add_photo(name, people=people)
        db.write_with_connection(self.db_path, lambda conn: conn.execute(
            "UPDATE photos SET tags = ?, raw_metadata = ? WHERE path = ?",
            (json.dumps(tags), json.dumps({"XMP:HierarchicalSubject": tags}), photo)))
        return photo

    def test_two_people_no_face_is_known_for_leave_the_two_faces_of_their_photo_unnamed(self):
        """#1374: the owner tags a photo with two people and the indexer finds two faces in it; neither person has a face known
        anywhere. Nothing tells which face is whose, so both stay unnamed -- they were paired by the order of the people's ids, a
        coin toss written as a name -- and the run says how many faces it left for that reason."""
        photo = self.photo_tagged("pair.jpg", ["Mara Quill", "Tobin Ash"])
        left_face = self.add_face(photo, identity_vector(21), box=(0, 0, 200, 200))
        right_face = self.add_face(photo, identity_vector(22), box=(300, 0, 500, 200))

        heard = self.run_while(lambda conn: None)

        self.assertEqual([(None, None), (None, None)], [self.row(left_face), self.row(right_face)],
                         "a face was given one of two people with nothing to tell them apart")
        self.assertEqual({}, self.stats, "the summary counted a face as named")
        self.assertEqual([2], self.unpaired, "the run did not say it left the two faces unnamed")
        self.assertEqual([], heard)

    def test_a_pairing_an_earlier_run_guessed_for_two_unknown_people_is_taken_back(self):
        """The owner's library after an earlier run paired them: the two guesses are unnamed, and counted as left so."""
        photo = self.photo_tagged("pair.jpg", ["Mara Quill", "Tobin Ash"])
        conn = db.connect(self.db_path)
        try:
            guessed = [face_rows.add_face(conn, photo, box=(0, 0, 200, 200), embedding=identity_vector(21).tobytes(),
                                          name="Mara Quill", prob=0.99),
                       face_rows.add_face(conn, photo, box=(300, 0, 500, 200), embedding=identity_vector(22).tobytes(),
                                          name="Tobin Ash", prob=0.99)]
            conn.commit()
        finally:
            conn.close()

        self.run_while(lambda conn: None)

        self.assertEqual([(None, None), (None, None)], [self.row(face) for face in guessed],
                         "a coin-toss pairing of an earlier run was kept")
        self.assertEqual([2], self.unpaired)

    def test_one_face_left_and_one_person_left_on_a_photo_is_still_named(self):
        """The case #1374 keeps: every other face of the photo is somebody known, so the one face left is the one person left."""
        jane = identity_vector(1)
        self.add_face(self.photo_tagged("one.jpg", ["Jane Doe"]), jane)
        photo = self.photo_tagged("pair.jpg", ["Jane Doe", "Mara Quill"])
        known = self.add_face(photo, near(jane, 2), box=(0, 0, 200, 200))
        other = self.add_face(photo, identity_vector(21), box=(300, 0, 500, 200))

        self.run_while(lambda conn: None)

        self.assertEqual([("Jane Doe", None), ("Mara Quill", None)], [self.row(known), self.row(other)])
        self.assertEqual([], self.unpaired)


if __name__ == "__main__":
    unittest.main()

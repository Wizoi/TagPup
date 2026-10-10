"""docs/findings.md, #640: automatch compares a face only with faces a person decided.

A decided face is one named by hand (name_source 'manual') or on a photo whose keywords name
the same person. A name automatch or clustering gave, that no keyword bears out, is a guess;
taken for a reference, the next Re-examine names more faces after it, and a wrong name
spreads one step at a time. Run through the matrix the routes pass (tagpup.jobs.identify
.decided_faces), built from rows the real writers make.
"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.jobs import identify as identify_jobs  # noqa: E402
from tagpup.services import faces  # noqa: E402
from tagpup.services import identify  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import people as store_people  # noqa: E402


def at(degrees):
    """A unit vector `degrees` round a circle: two are as alike as the cosine of the gap."""
    angle = math.radians(degrees)
    v = np.zeros(8, dtype="float32")
    v[0], v[1] = math.cos(angle), math.sin(angle)
    return v


class Case(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.cache = identify_jobs.GridCache()

    def photo(self, name, keywords=()):
        path = self.lib.photo(name)
        self.lib.add_row(path, tags=["People/" + person for person in keywords])
        self.rebuild(path)
        return path

    def rebuild(self, photo):
        db.write_with_connection(self.lib.library.path,
                                 lambda conn: store_people.rebuild_photos(conn, [photo]))

    def face(self, photo, embedding, name=None, source=None):
        def add(conn):
            face_id = add_face(conn, photo, name=name, name_source=source, embedding=embedding.tobytes())
            store_people.rebuild_photos(conn, [photo])
            return face_id
        return db.write_with_connection(self.lib.library.path, add)

    def name_of(self, face_id):
        return self.lib.rows("SELECT name FROM faces WHERE id = ?", (face_id,))[0][0]

    def matrix(self):
        return lambda: identify_jobs.decided_faces(self.lib.library, self.cache)

    def automatch(self, photo):
        return faces.automatch_photo(self.lib.library, photo, self.matrix())

    def references(self):
        """The names of the people of the decided faces (the matrix holds each as a Ref, the node's id and the name)."""
        return [person.name for person in identify_jobs.decided_faces(self.lib.library, self.cache)[1]]


class WhatIsAReference(Case):
    def test_a_name_automatch_gave_is_not_taken_as_a_reference(self):
        anchor = self.photo("a.jpg")
        self.face(anchor, at(0), name="Wren Halloway", source="manual")
        second, third = self.photo("b.jpg"), self.photo("c.jpg")
        guessed, further = self.face(second, at(30)), self.face(third, at(60))

        self.assertEqual(self.automatch(second).changed, 1)
        self.assertEqual(self.name_of(guessed), "Wren Halloway")
        # 60 degrees is alike to the guess (0.87), not to the face it came from (0.5).
        self.assertEqual(self.automatch(third).changed, 0)
        self.assertIsNone(self.name_of(further))
        self.assertEqual(self.references(), ["Wren Halloway"])

    def test_a_manual_face_is_a_reference(self):
        self.face(self.photo("a.jpg"), at(0), name="Wren Halloway", source="manual")
        self.assertEqual(self.references(), ["Wren Halloway"])

    def test_a_face_whose_photo_carries_its_persons_keyword_is_a_reference(self):
        self.face(self.photo("a.jpg", keywords=["Wren Halloway"]), at(0), name="Wren Halloway")
        self.assertEqual(self.references(), ["Wren Halloway"])

    def test_the_keyword_is_compared_as_a_person_not_as_spelled(self):
        self.face(self.photo("a.jpg", keywords=["wren halloway"]), at(0), name="Wren Halloway")
        self.assertEqual(self.references(), ["Wren Halloway"])

    def test_a_keyword_for_somebody_else_does_not_make_a_guess_a_reference(self):
        self.face(self.photo("a.jpg", keywords=["Ada Pembrook"]), at(0), name="Wren Halloway")
        self.assertEqual(self.references(), [])

    def test_a_keyword_confirmed_face_names_its_lookalike(self):
        confirmed = self.photo("a.jpg", keywords=["Wren Halloway"])
        self.face(confirmed, at(0), name="Wren Halloway")
        other = self.photo("b.jpg")
        lookalike = self.face(other, at(10))
        self.assertEqual(self.automatch(other).changed, 1)
        self.assertEqual(self.name_of(lookalike), "Wren Halloway")

    def test_the_matrix_the_other_screens_use_still_holds_every_named_face(self):
        self.face(self.photo("a.jpg"), at(0), name="Wren Halloway")
        _stamp, (_ids, people, _matrix) = identify.named_faces(self.lib.library)
        self.assertEqual([each.name for each in people], ["Wren Halloway"])


class TheMatrixFollowsTheKeywords(Case):
    def test_a_keyword_written_makes_a_guess_a_reference_without_a_face_changing(self):
        photo = self.photo("a.jpg")
        self.face(photo, at(0), name="Wren Halloway")
        self.assertEqual(self.references(), [])

        self.lib.execute("UPDATE photos SET tags = ? WHERE path = ?", ('["People/Wren Halloway"]', photo))
        self.rebuild(photo)
        self.assertEqual(self.references(), ["Wren Halloway"])

        self.lib.execute("UPDATE photos SET tags = '[]' WHERE path = ?", (photo,))
        self.rebuild(photo)
        self.assertEqual(self.references(), [])


class WhatItCosts(Case):
    def test_the_photo_people_rows_are_found_by_their_photo(self):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            plan = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT photo_id, name FROM photo_people WHERE source = 'keyword' AND photo_id IN"
                " (SELECT photo_id FROM faces WHERE name IS NOT NULL AND excluded = 0)"))
        finally:
            conn.close()
        self.assertNotIn("SCAN photo_people", plan)


if __name__ == "__main__":
    unittest.main()

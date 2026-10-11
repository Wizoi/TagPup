"""docs/findings.md, #790: the faces a hover shows of a suggested person (/api/people-face-samples).

Up to four faces of each person, the faces a person decided first (named by hand, or borne out by
the photo's keyword: #640), the one nearest the mean of the person's faces first; their other named
faces only to fill the places left; never an excluded face; read again when a name, a keyword or an
exclusion moves the decided stamp. Both apps answer it, from the one cache of named faces.
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
from tagpup.store import db  # noqa: E402
from tagpup.store import people as store_people  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402

WREN = "Wren Halloway"
INES = "Ines Okafor"


def at(degrees):
    angle = math.radians(degrees)
    v = np.zeros(8, dtype="float32")
    v[0], v[1] = math.cos(angle), math.sin(angle)
    return v


class Case(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.cache = identify_jobs.GridCache()
        self.n = 0

    def face(self, degrees, name=None, source="manual", keyword=False, excluded=0):
        self.n += 1
        photo = self.lib.photo("p%d.jpg" % self.n)
        self.lib.add_row(photo, tags=["People/" + name] if keyword and name else [])

        def add(conn):
            face_id = add_face(conn, photo, name=name, name_source=source if name else None,
                               embedding=at(degrees).tobytes(), excluded=excluded)
            store_people.rebuild_photos(conn, [photo])
            return face_id
        return db.write_with_connection(self.lib.library.path, add)

    def samples(self):
        return identify_jobs.face_samples(self.lib.library, self.cache)


class Choosing(Case):
    def test_at_most_four_the_most_like_the_person_first(self):
        ids = [self.face(d, WREN) for d in (0, 10, 20, 30, 40, 90)]
        got = self.samples()[WREN]
        self.assertEqual(4, len(got))
        self.assertEqual([ids[3], ids[4], ids[2], ids[1]], got, "nearest the mean first, then by closeness")

    def test_the_faces_a_person_decided_come_before_a_guess(self):
        decided = self.face(0, WREN)
        guesses = [self.face(d, WREN, source="automatch") for d in (50, 51, 52, 53)]
        got = self.samples()[WREN]
        self.assertEqual(decided, got[0])
        self.assertEqual(4, len(got), "the places left are filled with the person's other named faces")
        self.assertTrue(set(got[1:]) <= set(guesses))

    def test_a_keyword_bears_a_face_out(self):
        borne = self.face(0, WREN, source="automatch", keyword=True)
        self.face(80, WREN, source="automatch")
        self.assertEqual(borne, self.samples()[WREN][0])

    def test_a_person_with_two_faces_gets_two(self):
        self.face(0, INES)
        self.face(10, INES)
        self.assertEqual(2, len(self.samples()[INES]))

    def test_an_excluded_face_is_never_shown(self):
        self.face(0, WREN, excluded=1)
        kept = self.face(30, WREN)
        self.assertEqual([kept], self.samples()[WREN])
        self.face(5, "Only Excluded", excluded=1)
        self.assertNotIn("Only Excluded", self.samples())

    def test_a_library_with_no_faces_answers_nothing(self):
        self.assertEqual({}, self.samples())

    def test_a_nameless_face_belongs_to_no_one(self):
        self.face(0)
        self.assertEqual({}, self.samples())

    def test_each_person_is_shown_by_their_own_faces(self):
        wren = {self.face(d, WREN) for d in (0, 5, 10)}
        ines = {self.face(d + 90, INES) for d in (0, 5, 10)}
        got = self.samples()
        self.assertEqual(wren, set(got[WREN]))
        self.assertEqual(ines, set(got[INES]))


class WhenTheFacesChange(Case):
    def test_a_face_named_meanwhile_is_seen(self):
        first = self.face(0, WREN)
        second = self.face(40, WREN)
        self.assertEqual({first, second}, set(self.samples()[WREN]))
        faces.name_faces(self.lib.library, [first], INES)
        got = self.samples()
        self.assertEqual([second], got[WREN])
        self.assertEqual([first], got[INES])

    def test_an_exclusion_is_seen(self):
        first = self.face(0, WREN)
        second = self.face(40, WREN)
        faces.exclude(self.lib.library, [first])
        self.assertEqual([second], self.samples()[WREN])

    def test_it_is_not_recomputed_while_nothing_moves(self):
        self.face(0, WREN)
        first = self.samples()
        self.assertIs(self.samples(), first)


class TheRoute(unittest.TestCase):
    def test_both_apps_answer_it(self):
        import web_client
        for kind in ("tuner", "tagpup"):
            with self.subTest(kind):
                app, home = web_client.app_for(self, kind)
                client = app.test_client()
                self.assertEqual({}, client.get("/library/api/people-face-samples").get_json())

                def seed(conn):
                    with store_people.tree_edit(conn):
                        taxonomy.add_path(conn, "People", root_has_face=1)
                        taxonomy.add_path(conn, "People/" + WREN)
                    node = conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", ("People/" + WREN,)).fetchone()[0]
                    named = add_face(conn, "a.jpg", name=WREN, name_source="manual", embedding=at(0).tobytes())
                    add_face(conn, "b.jpg", name="Loki", name_source="auto", embedding=at(5).tobytes(), tag_id=None)
                    return node, named
                node, face = db.write_with_connection(home.library("library.db"), seed)
                # By the id of the node alone; a name with no node is under no key.
                self.assertEqual({"id:%d" % node: [face]}, client.get("/library/api/people-face-samples").get_json())

    def test_the_crops_are_served_by_the_face_crop_route_of_each_app(self):
        # The popup's crops are /api/face-crop?id=, which both apps already serve.
        import web_client
        for kind in ("tuner", "tagpup"):
            app, _home = web_client.app_for(self, kind)
            self.assertEqual(404, app.test_client().get("/library/api/face-crop?id=1").status_code)


if __name__ == "__main__":
    unittest.main()

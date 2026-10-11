"""TagTuner's people list by face: the one face most like each person (/api/people-faces).

The decided face (named by hand, or borne out by its photo's keyword: #640) nearest the mean
of the person's decided faces; a guess only when the person has nothing decided; never an
excluded face; recomputed when a rename, a merge or an exclusion moves the matrix's stamp.
Rows are made as the writers make them (tests/face_rows.py, the store's own writes).
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
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import people as store_people  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402


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

    def face(self, embedding, name=None, source="manual", keyword=False, excluded=0):
        self.n += 1
        photo = self.lib.photo("p%d.jpg" % self.n)
        self.lib.add_row(photo, tags=["People/" + name] if keyword and name else [])

        def add(conn):
            face_id = add_face(conn, photo, name=name, name_source=source if name else None,
                               embedding=embedding.tobytes(), excluded=excluded)
            store_people.rebuild_photos(conn, [photo])
            return face_id
        return db.write_with_connection(self.lib.library.path, add)

    def chosen(self):
        return identify_jobs.representative_faces(self.lib.library, self.cache)


class Choosing(Case):
    def test_the_decided_face_nearest_the_mean_is_chosen(self):
        a, b, c = (self.face(at(d), "Wren Halloway") for d in (0, 10, 40))
        self.assertEqual(self.chosen(), {"Wren Halloway": b})
        self.assertNotIn(a, self.chosen().values())

    def test_a_guess_is_not_chosen_while_a_decided_face_exists(self):
        decided = self.face(at(0), "Wren Halloway")
        for d in (50, 51, 52):
            self.face(at(d), "Wren Halloway", source="automatch")
        self.assertEqual(self.chosen(), {"Wren Halloway": decided})

    def test_a_face_a_keyword_bears_out_is_decided(self):
        borne = self.face(at(0), "Wren Halloway", source="automatch", keyword=True)
        self.face(at(80), "Wren Halloway", source="automatch")
        self.assertEqual(self.chosen(), {"Wren Halloway": borne})

    def test_a_person_with_nothing_decided_gets_a_named_face(self):
        a, b, c = (self.face(at(d), "Ines Okafor", source="automatch") for d in (0, 10, 40))
        self.assertEqual(self.chosen(), {"Ines Okafor": b})

    def test_an_excluded_face_is_never_chosen(self):
        self.face(at(0), "Wren Halloway", excluded=1)
        kept = self.face(at(30), "Wren Halloway")
        self.assertEqual(self.chosen(), {"Wren Halloway": kept})
        self.face(at(5), "Only Excluded", excluded=1)
        self.assertNotIn("Only Excluded", self.chosen())

    def test_a_library_with_no_faces_answers_nothing(self):
        self.assertEqual(self.chosen(), {})

    def test_a_nameless_face_belongs_to_no_one(self):
        self.face(at(0))
        self.assertEqual(self.chosen(), {})

    def test_every_person_is_chosen_from_their_own_faces(self):
        wren = [self.face(at(d), "Wren Halloway") for d in (0, 5, 10)]
        ines = [self.face(at(d + 90), "Ines Okafor") for d in (0, 5, 10)]
        got = self.chosen()
        self.assertIn(got["Wren Halloway"], wren)
        self.assertIn(got["Ines Okafor"], ines)


class WhenTheFacesChange(Case):
    def test_a_rename_is_seen(self):
        face = self.face(at(0), "Wren Halloway")
        self.assertEqual(self.chosen(), {"Wren Halloway": face})
        db.write_with_connection(self.lib.library.path, lambda conn: store_faces.set_names(
            conn, {face: "Wren Hallowell"}))
        self.assertEqual(self.chosen(), {"Wren Hallowell": face})

    def test_a_representative_named_for_someone_else_meanwhile_is_replaced(self):
        first = self.face(at(0), "Wren Halloway")
        second = self.face(at(40), "Wren Halloway")
        self.assertEqual(self.chosen()["Wren Halloway"], first)
        faces.name_faces(self.lib.library, [first], "Ines Okafor")
        got = self.chosen()
        self.assertEqual(got["Wren Halloway"], second)
        self.assertEqual(got["Ines Okafor"], first)

    def test_a_representative_unnamed_meanwhile_is_replaced(self):
        first = self.face(at(0), "Wren Halloway")
        second = self.face(at(40), "Wren Halloway")
        faces.unname_faces(self.lib.library, [first])
        self.assertEqual(self.chosen(), {"Wren Halloway": second})

    def test_an_exclusion_is_seen(self):
        first = self.face(at(0), "Wren Halloway")
        second = self.face(at(40), "Wren Halloway")
        faces.exclude(self.lib.library, [first])
        self.assertEqual(self.chosen(), {"Wren Halloway": second})

    def test_a_merge_pools_the_faces(self):
        a = self.face(at(0), "Wren Halloway")
        b = self.face(at(10), "Wren Halloway")
        c = self.face(at(20), "Wren Hallowell")
        self.assertEqual(self.chosen(), {"Wren Halloway": a, "Wren Hallowell": c})
        faces.name_faces(self.lib.library, [c], "Wren Halloway")
        self.assertEqual(self.chosen(), {"Wren Halloway": b})

    def test_it_is_not_recomputed_while_nothing_moves(self):
        self.face(at(0), "Wren Halloway")
        first = self.chosen()
        self.assertIs(self.chosen(), first)


class TheRoute(unittest.TestCase):
    def test_it_answers_id_to_face_and_nothing_for_an_empty_library(self):
        import web_client
        app, home = web_client.app_for(self, "tuner")
        client = app.test_client()
        self.assertEqual(client.get("/library/api/people-faces").get_json(), {})

        def seed(conn):
            with store_people.tree_edit(conn):
                taxonomy.add_path(conn, "People", root_has_face=1)
                taxonomy.add_path(conn, "People/Wren Halloway")
            node = conn.execute("SELECT id FROM tag_taxonomy WHERE tag = 'People/Wren Halloway'").fetchone()[0]
            named = add_face(conn, "a.jpg", name="Wren Halloway", name_source="manual", embedding=at(0).tobytes())
            # An automatic guess from before ids: a name and no id. It is nobody's, under no key.
            add_face(conn, "b.jpg", name="Loki", name_source="auto", embedding=at(5).tobytes(), tag_id=None)
            return node, named
        node, face = db.write_with_connection(home.library("library.db"), seed)
        self.assertEqual(client.get("/library/api/people-faces").get_json(), {"id:%d" % node: face})


if __name__ == "__main__":
    unittest.main()

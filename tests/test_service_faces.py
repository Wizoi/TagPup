"""tagpup.services.faces: naming faces, and taking them out of identity work.

Called directly on a temporary library. What the routes add -- parsing, and taking the
faces a write removed off TagTuner's cached grids -- is in test_tuner_server_api.py and
test_identify_ignore_assign_restore_undo.py.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402
from face_rows import add_face, people_of  # noqa: E402

from tagpup.core.result import Conflict, NotFound  # noqa: E402
from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.store import db, derived  # noqa: E402
from tagpup.services import faces  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import people as store_people  # noqa: E402


def vector(seed, dim=8):
    v = np.random.default_rng(seed).standard_normal(dim).astype("float32")
    return v / np.linalg.norm(v)


class FacesCase(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.folder = self.lib.photos

    def photo(self, name, people=()):
        """A photo whose keywords name `people`, its people rebuilt from them."""
        path = self.lib.photo(name)
        named = ["People/" + person for person in people]
        self.lib.add_row(path, tags=named)
        # The nodes of the people its keywords name, as the index makes them when it reads the file (every tag has an id).
        db.write_with_connection(self.lib.library.path, lambda conn: derived.make_nodes(conn, [named]), label="test: nodes")
        self.rebuild(path)
        return path

    def rebuild(self, photo):
        conn = db.connect(self.lib.library.path)
        try:
            store_people.rebuild_photos(conn, [photo])
            conn.commit()
        finally:
            conn.close()

    def face(self, photo, name=None, excluded=0, embedding=None, source=None):
        conn = db.connect(self.lib.library.path)
        try:
            face_id = add_face(conn, photo, box="[0,0,10,10]", name=name, excluded=excluded,
                               embedding=embedding.tobytes() if embedding is not None else None,
                               name_source=source)
            store_people.rebuild_photos(conn, [photo])   # as the store does, inserting a named face
            conn.commit()
            return face_id
        finally:
            conn.close()

    def people(self, photo):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return people_of(conn, photo)
        finally:
            conn.close()

    def face_row(self, face_id):
        return self.lib.rows("SELECT name, name_source, excluded FROM faces WHERE id = ?", (face_id,))[0]

    def person(self, name):
        """The id of the node a face named `name` was given (every name has one)."""
        return self.lib.rows("SELECT id FROM tag_taxonomy WHERE name = ?", (name,))[0][0]

    def ref(self, name):
        """The person a face named `name` is, as a matrix of named faces holds them (identify.decided_faces): a Ref."""
        return Ref(self.person(name), name)


class NamingAFace(FacesCase):
    def test_the_fingerprints_either_side_say_what_the_grids_must_drop(self):
        face = self.face(self.photo("a.jpg"))
        wren = db.write_with_connection(self.lib.library.path, lambda conn: face_rows.person_of(conn, "Wren Halloway"))
        result = faces.name_face(self.lib.library, face, wren)
        before, after = result.details["fingerprints"]
        self.assertNotEqual(before, after)
        self.assertEqual(result.details["face_ids"], [face])

    def test_a_name_on_another_face_in_the_photo_is_refused(self):
        photo = self.photo("a.jpg")
        self.face(photo, name="Wren Halloway")
        face = self.face(photo)
        self.assertIn("already tagged", faces.name_face(self.lib.library, face, "Wren Halloway").refused)
        self.assertIsNone(self.face_row(face)[0])

    def test_an_excluded_face_is_a_conflict(self):
        face = self.face(self.photo("a.jpg"), excluded=1)
        with self.assertRaises(Conflict):
            faces.name_face(self.lib.library, face, "Wren Halloway")

    def test_the_name_it_has_whatever_its_case_changes_nothing(self):
        face = self.face(self.photo("a.jpg"), name="Wren Halloway", source="manual")
        result = faces.name_face(self.lib.library, face, "wren halloway")
        self.assertEqual((result.changed, self.face_row(face)[0]), (0, "Wren Halloway"))

    def test_an_automatic_name_a_person_confirms_becomes_their_decision_in_its_own_spelling(self):
        """#791: choosing the person a tag or clustering named, from a face's box, is a decision."""
        face = self.face(self.photo("a.jpg"), name="Wren Halloway")
        result = faces.name_face(self.lib.library, face, "wren halloway")
        self.assertEqual((result.changed, self.face_row(face)[0]), (1, "Wren Halloway"))
        self.assertEqual(self.lib.rows("SELECT name_source FROM faces WHERE id = ?", (face,))[0][0], "manual")
        self.assertEqual(faces.name_face(self.lib.library, face, "Wren Halloway").changed, 0)

    def test_a_face_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            faces.name_face(self.lib.library, 999, "Wren Halloway")


class NamingFacesInBulk(FacesCase):
    """What a bulk assignment decides before it writes (nameable), and what naming does to a photo's people."""

    def test_an_excluded_face_is_skipped_and_said_to_be(self):
        photo = self.photo("a.jpg")
        named, excluded = self.face(photo), self.face(self.photo("b.jpg"), excluded=1)
        result = faces.nameable(self.lib.library, [named, excluded], "Wren Halloway")
        self.assertEqual((result.details["matched_ids"], result.details["skipped_excluded"]),
                         ([named], [excluded]))
        self.assertIsNone(self.face_row(excluded)[0])

    def test_two_faces_in_one_photo_are_refused(self):
        photo = self.photo("a.jpg")
        ids = [self.face(photo), self.face(photo)]
        self.assertIn("Multiple selected faces", faces.nameable(self.lib.library, ids, "Wren Halloway").refused)
        self.assertEqual([self.face_row(i)[0] for i in ids], [None, None])

    def test_faces_already_this_person_are_nothing_to_do(self):
        face = self.face(self.photo("a.jpg"), name="Wren Halloway")
        result = faces.nameable(self.lib.library, [face], "Wren Halloway")
        self.assertEqual(result.details["matched_ids"], [])


class Automatching(FacesCase):
    def setUp(self):
        super().setUp()
        self.wren, self.ada = vector(1), vector(2)
        self.asked = []
        # The matrix TagTuner keeps is made of named faces; the names it offers are on
        # faces in the library, as here. Automatch names nobody whose name no face has.
        known = self.photo("known.jpg")
        self.face(known, name="Wren Halloway", embedding=self.wren)
        self.face(known, name="Ada Pembrook", embedding=self.ada)

    def named(self):
        self.asked.append(True)
        return [1, 2], [self.ref("Wren Halloway"), self.ref("Ada Pembrook")], np.stack([self.wren, self.ada])

    def test_a_face_like_a_named_one_is_given_its_name(self):
        photo = self.photo("a.jpg")
        like_wren, unlike = self.face(photo, embedding=self.wren), self.face(photo, embedding=vector(3))
        result = faces.automatch_photo(self.lib.library, photo, self.named)
        self.assertEqual(result.changed, 1)
        self.assertEqual(self.face_row(like_wren), ("Wren Halloway", None, 0))
        self.assertIsNone(self.face_row(unlike)[0])
        self.assertEqual(self.people(photo), ["Wren Halloway"])

    def test_a_name_proposed_twice_in_a_photo_or_already_there_is_left(self):
        crowded, taken = self.photo("a.jpg"), self.photo("b.jpg")
        self.face(crowded, embedding=self.wren)
        self.face(crowded, embedding=self.wren)
        self.face(taken, name="Wren Halloway")
        self.face(taken, embedding=self.wren)
        self.assertEqual(faces.automatch_folder(self.lib.library, self.folder, self.named).changed, 0)

    def test_the_named_faces_are_not_asked_for_when_nothing_is_unnamed(self):
        faces.automatch_photo(self.lib.library, self.photo("a.jpg"), self.named)
        self.assertEqual(self.asked, [])

    def test_a_folder_says_what_is_left_unnamed(self):
        photo = self.photo("a.jpg")
        self.face(photo, embedding=self.wren)
        self.face(photo, embedding=vector(3))
        result = faces.automatch_folder(self.lib.library, self.folder, self.named)
        self.assertEqual(result.details["remaining_counts"], {photo: 1})


class TheWriteLockIsNotHeldThroughAScan(FacesCase):
    """docs/findings.md, #45: folder automatch read every named face under the folder,
    a scan of the whole table, while holding the write lock."""

    def test_the_names_on_a_photo_are_found_by_an_index(self):
        # The query the store runs, planned: faces find their photo by id since
        # migration 4, so neither table may be scanned to answer for one photo.
        asked = []

        class Recording:
            def execute(self, sql, params=()):
                asked.append((sql, params))
                return []

        store_faces.people_in_photo(Recording(), self.photo("a.jpg"))
        self.assertEqual(1, len(asked))
        sql, params = asked[0]
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            plan = " ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        finally:
            conn.close()
        self.assertIn("USING INDEX", plan)
        self.assertNotIn("SCAN faces", plan)
        self.assertNotIn("SCAN photos", plan)

    def test_automatching_a_folder_reads_names_only_of_the_photos_it_may_change(self):
        photo = self.photo("a.jpg")
        self.face(photo, embedding=vector(1))
        for other in range(3):
            self.face(self.photo("other%d.jpg" % other), name="Kit Morrow")
        asked = []
        real = store_faces.people_in_photo

        def people_in_photo(conn, path):
            asked.append(path)
            return real(conn, path)

        with mock.patch.object(store_faces, "people_in_photo", side_effect=people_in_photo):
            faces.automatch_folder(self.lib.library, self.folder,
                                   lambda: ([1], [self.ref("Kit Morrow")], np.stack([vector(1)])))
        self.assertEqual(asked, [photo])
        self.assertEqual(self.face_row(self.lib.rows("SELECT id FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)",
                                                     (photo,))[0][0])[0], "Kit Morrow")


class RemovingAFolder(FacesCase):
    def test_its_photos_and_faces_go_and_what_that_cost_is_said(self):
        photo = self.photo("a.jpg")
        self.face(photo, name="Wren Halloway", source="manual")
        self.face(photo, excluded=1)
        result = faces.remove_folder(self.lib.library, self.folder)
        self.assertEqual({k: result.details[k] for k in ("photos_removed", "faces_removed", "manual_lost",
                                                          "excluded_lost")},
                         {"photos_removed": 1, "faces_removed": 2, "manual_lost": 1, "excluded_lost": 1})


class AnAccountedWrite(FacesCase):
    def test_it_rolls_back_when_the_block_raises(self):
        face = self.face(self.photo("a.jpg"))
        with self.assertRaises(RuntimeError):
            with store_faces.accounted_write(self.lib.library.path) as write:
                write.conn.execute("UPDATE faces SET name = 'Wren Halloway' WHERE id = ?", (face,))
                raise RuntimeError("stop")
        self.assertIsNone(self.face_row(face)[0])


if __name__ == "__main__":
    unittest.main()

"""tagpup.services.faces: naming faces, and taking them out of identity work.

Called directly on a temporary library. What the routes add -- parsing, and taking the
faces a write removed off TagTuner's cached grids -- is in test_tuner_server_api.py and
test_identify_faces_hot_paths.py.
"""
import json
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.result import Conflict, NotFound  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.services import faces  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402


def vector(seed, dim=8):
    v = np.random.default_rng(seed).standard_normal(dim).astype("float32")
    return v / np.linalg.norm(v)


class FacesCase(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.folder = self.lib.photos

    def photo(self, name, people=()):
        path = self.lib.photo(name)
        self.lib.add_row(path)
        self.lib.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(list(people)), path))
        return path

    def face(self, photo, name=None, excluded=0, embedding=None, source=None):
        return self.lib.execute(
            "INSERT INTO faces (photo_path, box, name, excluded, embedding, name_source)"
            " VALUES (?, '[0,0,10,10]', ?, ?, ?, ?)",
            (photo, name, excluded, embedding.tobytes() if embedding is not None else None, source))

    def people(self, photo):
        return json.loads(self.lib.rows("SELECT people FROM photos WHERE path = ?", (photo,))[0][0])

    def face_row(self, face_id):
        return self.lib.rows("SELECT name, name_source, excluded FROM faces WHERE id = ?", (face_id,))[0]


class NamingAFace(FacesCase):
    def test_it_is_named_by_hand_and_listed_on_its_photo(self):
        photo = self.photo("a.jpg", people=["Ada Pembrook"])
        face = self.face(photo)
        result = faces.name_face(self.lib.library, face, " Wren Halloway ")
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertEqual(self.face_row(face), ("Wren Halloway", "manual", 0))
        self.assertEqual(self.people(photo), ["Ada Pembrook", "Wren Halloway"])

    def test_the_fingerprints_either_side_say_what_the_grids_must_drop(self):
        face = self.face(self.photo("a.jpg"))
        result = faces.name_face(self.lib.library, face, "Wren Halloway")
        before, after = result.details["fingerprints"]
        self.assertNotEqual(before, after)
        self.assertEqual(result.details["face_ids"], [face])

    def test_the_old_name_leaves_the_photo_unless_another_face_there_has_it(self):
        photo = self.photo("a.jpg", people=["Ada Pembrook", "Milo Garrick"])
        ada = self.face(photo, name="Ada Pembrook")
        milo = self.face(photo, name="Milo Garrick")
        self.face(photo, name="Milo Garrick")
        faces.name_face(self.lib.library, ada, "Wren Halloway")
        faces.name_face(self.lib.library, milo, "Jude Ferris")
        self.assertEqual(self.people(photo), ["Milo Garrick", "Wren Halloway", "Jude Ferris"])

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
        face = self.face(self.photo("a.jpg"), name="Wren Halloway")
        result = faces.name_face(self.lib.library, face, "wren halloway")
        self.assertEqual((result.changed, self.face_row(face)[0]), (0, "Wren Halloway"))

    def test_a_face_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            faces.name_face(self.lib.library, 999, "Wren Halloway")


class NamingFacesInBulk(FacesCase):
    def test_each_photo_lists_the_person(self):
        """docs/findings.md, #42."""
        first, second = self.photo("a.jpg", people=["Ada Pembrook"]), self.photo("b.jpg")
        ids = [self.face(first), self.face(second)]
        result = faces.name_faces(self.lib.library, ids, "Wren Halloway")
        self.assertEqual((result.details["matched"], result.details["matched_ids"]), (2, ids))
        self.assertEqual(self.people(first), ["Ada Pembrook", "Wren Halloway"])
        self.assertEqual(self.people(second), ["Wren Halloway"])

    def test_an_excluded_face_is_skipped_and_said_to_be(self):
        photo = self.photo("a.jpg")
        named, excluded = self.face(photo), self.face(self.photo("b.jpg"), excluded=1)
        result = faces.name_faces(self.lib.library, [named, excluded], "Wren Halloway")
        self.assertEqual((result.details["matched_ids"], result.details["skipped_excluded"]),
                         ([named], [excluded]))
        self.assertIsNone(self.face_row(excluded)[0])

    def test_two_faces_in_one_photo_are_refused(self):
        photo = self.photo("a.jpg")
        ids = [self.face(photo), self.face(photo)]
        self.assertIn("Multiple selected faces", faces.name_faces(self.lib.library, ids, "Wren Halloway").refused)
        self.assertEqual([self.face_row(i)[0] for i in ids], [None, None])

    def test_faces_already_this_person_are_nothing_to_do(self):
        face = self.face(self.photo("a.jpg"), name="Wren Halloway")
        result = faces.name_faces(self.lib.library, [face], "Wren Halloway")
        self.assertEqual((result.changed, result.details["matched"]), (0, 0))
        self.assertNotIn("fingerprints", result.details)


class TakingNamesOff(FacesCase):
    def test_one_face_the_person_goes_from_the_photo(self):
        photo = self.photo("a.jpg", people=["Wren Halloway"])
        face = self.face(photo, name="Wren Halloway")
        self.assertEqual(faces.unname_face(self.lib.library, face).changed, 1)
        self.assertEqual((self.face_row(face), self.people(photo)), ((None, "manual", 0), []))

    def test_undo_leaves_the_faces_unreviewed(self):
        photo = self.photo("a.jpg", people=["Wren Halloway"])
        face = self.face(photo, name="Wren Halloway", source="manual")
        faces.unname_faces(self.lib.library, [face], undo=True)
        self.assertEqual(self.face_row(face), (None, None, 0))

    def test_every_face_in_a_photo(self):
        photo = self.photo("a.jpg", people=["Wren Halloway", "Ada Pembrook", "Kit Morrow"])
        self.face(photo, name="Wren Halloway")
        self.face(photo, name="Ada Pembrook")
        self.assertEqual(faces.unname_photo(self.lib.library, photo).changed, 2)
        self.assertEqual(self.people(photo), ["Kit Morrow"])


class ExcludingAndRestoring(FacesCase):
    def test_an_excluded_face_loses_its_name_and_leaves_the_photos_people(self):
        photo = self.photo("a.jpg", people=["Wren Halloway"])
        face = self.face(photo, name="Wren Halloway")
        result = faces.exclude(self.lib.library, [face, 999], "stranger")
        self.assertEqual((result.changed, result.details["face_ids"]), (1, [face, 999]))
        self.assertEqual((self.face_row(face), self.people(photo)), ((None, "manual", 1), []))

    def reason(self, face_id):
        return self.lib.rows("SELECT excluded_reason FROM faces WHERE id = ?", (face_id,))[0][0]

    def test_a_reason_the_page_does_not_offer_is_refused(self):
        # Free text got in before, and needed a script to fold (docs/findings.md, #53).
        face = self.face(self.photo("a.jpg"))
        result = faces.exclude(self.lib.library, [face], "fuzzy")
        self.assertIn("fuzzy", result.refused)
        self.assertEqual((result.changed, self.face_row(face)[2]), (0, 0))

    def test_a_reason_is_kept_as_the_page_spells_it(self):
        face = self.face(self.photo("a.jpg"))
        faces.exclude(self.lib.library, [face], "  Bad Crop ")
        self.assertEqual(self.reason(face), "bad crop")

    def test_no_reason_is_not_a_person_and_an_ignored_cluster_says_so(self):
        first, second = self.face(self.photo("a.jpg")), self.face(self.photo("b.jpg"))
        faces.exclude(self.lib.library, [first], None)
        faces.exclude(self.lib.library, [second], "ignored cluster")
        self.assertEqual((self.reason(first), self.reason(second)), ("not a person", "ignored cluster"))

    def test_restoring_brings_back_only_what_was_excluded(self):
        photo = self.photo("a.jpg")
        excluded, named = self.face(photo, excluded=1, source="manual"), self.face(photo, name="Ada Pembrook",
                                                                               source="manual")
        self.assertEqual(faces.restore(self.lib.library, [excluded, named]).changed, 1)
        self.assertEqual(self.face_row(excluded), (None, None, 0))
        self.assertEqual(self.face_row(named), ("Ada Pembrook", "manual", 0))


class Automatching(FacesCase):
    def setUp(self):
        super().setUp()
        self.wren, self.ada = vector(1), vector(2)
        self.asked = []

    def named(self):
        self.asked.append(True)
        return [1, 2], ["Wren Halloway", "Ada Pembrook"], np.stack([self.wren, self.ada])

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
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            where, params = paths.sql_equals("photo_path", self.photo("a.jpg"))
            plan = " ".join(row[-1] for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT name FROM faces WHERE " + where + " AND name IS NOT NULL", params))
        finally:
            conn.close()
        self.assertIn("USING INDEX", plan)
        self.assertNotIn("SCAN faces", plan)

    def test_automatching_a_folder_reads_names_only_of_the_photos_it_may_change(self):
        photo = self.photo("a.jpg")
        self.face(photo, embedding=vector(1))
        for other in range(3):
            self.face(self.photo("other%d.jpg" % other), name="Kit Morrow")
        asked = []
        real = store_faces.names_in_photo

        def names_in_photo(conn, path):
            asked.append(path)
            return real(conn, path)

        with mock.patch.object(store_faces, "names_in_photo", side_effect=names_in_photo):
            faces.automatch_folder(self.lib.library, self.folder,
                                   lambda: ([1], ["Wren Halloway"], np.stack([vector(1)])))
        self.assertEqual(asked, [photo])
        self.assertEqual(self.face_row(self.lib.rows("SELECT id FROM faces WHERE photo_path = ?",
                                                     (photo,))[0][0])[0], "Wren Halloway")


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

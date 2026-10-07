"""docs/findings.md, #788: a photo's person tag names its face.

Tagging a photo with a person did nothing for its faces. Now, when a photo is saved, indexed or has
its faces recorded and it has exactly one face still to be named and exactly one keyword person no
face of it carries, that face is that person, as an automatic name (name_source NULL: clustering may
revise it, and the photo's own keyword makes it a decided reference, #640). Several faces or people
are left alone by a save; the backfill (`faces-from-tags`) names them by comparison only when exactly
one person's decided faces are alike enough.

Rows are made as the code that makes them makes them: the photos by the indexer's record
(tests/photo_rows.py), the tree by tagpup.store.taxonomy, faces by tagpup.store.faces and by
tagpup.services.faces.record_detected -- what detection records.
"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import faces_from_tags, identify, journal as journal_service  # noqa: E402
from tagpup.store import db, faces, people, photos, schema, taxonomy  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"
TAMSIN = "Tamsin Vey"
CREW = "People/Crew"          # a branch: Tamsin is filed under it


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def write(path, operation):
    return db.write_with_connection(path, operation)


def at(degrees):
    """A unit vector `degrees` round a circle: two are as alike as the cosine of the gap."""
    angle = math.radians(degrees)
    vector = np.zeros(8, dtype="float32")
    vector[0], vector[1] = math.cos(angle), math.sin(angle)
    return vector.tobytes()


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        self.library = Library(self.path)
        self.folder = os.path.join(os.path.dirname(self.path), "Pictures")

        def tree(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People", root_has_face=1)
                for tag in ("People/" + WREN, "People/" + ODA, CREW, CREW + "/" + TAMSIN):
                    taxonomy.add_path(conn, tag)

        write(self.path, tree)

    def photo(self, name, keywords=()):
        """An indexed photo whose keywords name `keywords` (full tags). Returns its path."""
        photo_path = os.path.join(self.folder, name)
        write(self.path, lambda conn: photo_rows.add_read(conn, photo_path, {"XMP:Subject": list(keywords)}))
        return photo_path

    def face(self, photo_path, embedding=None, **columns):
        """A face as detection records it (a row with its box), then given `columns`."""
        def add(conn):
            box = [10 * len(faces.in_photo(conn, photo_path)), 0, 10, 10]
            face_id = faces.insert(conn, photo_path, box, embedding or at(0))
            sets = ", ".join("%s = ?" % column for column in columns)
            if sets:
                conn.execute("UPDATE faces SET %s WHERE id = ?" % sets, list(columns.values()) + [face_id])
                people.rebuild_photos(conn, [photo_path])
            return face_id
        return write(self.path, add)

    def row(self, face_id):
        return look(self.path, "SELECT name, name_source, excluded, tag_id FROM faces WHERE id = ?", (face_id,))[0]

    def name(self, face_id):
        return self.row(face_id)[0]

    def tag(self, photo_path, *tags):
        """Save the photo with these keywords, as a keyword write records it."""
        return photos.record_tags(self.path, photo_path, list(tags))

    def node(self, tag):
        return look(self.path, "SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))[0][0]


class OneFaceOnePerson(Case):
    def test_a_save_names_the_only_face_as_the_only_person(self):
        photo = self.photo("regatta_001.jpg")
        face = self.face(photo)
        self.assertIsNone(self.name(face))
        self.tag(photo, "People/" + WREN)
        name, source, excluded, tag_id = self.row(face)
        self.assertEqual(WREN, name)
        self.assertIsNone(source, "an automatic name, which clustering may revise, not a decision")
        self.assertEqual(self.node("People/" + WREN), tag_id, "the face carries the person's id beside the name")
        self.assertEqual([(WREN, "keyword")], look(self.path, "SELECT name, source FROM photo_people"))

    def test_the_index_of_a_photo_already_tagged_names_it_when_its_faces_come(self):
        photo = self.photo("regatta_002.jpg", ["People/" + WREN])
        detected = [{"box": [1, 1, 5, 5], "embedding": [0.5] * 8, "prob": 0.99, "crop_image": None}]
        self.assertEqual(1, face_records.record_detected(self.path, photo, detected))
        self.assertEqual([(WREN,)], look(self.path, "SELECT name FROM faces"))

    def test_a_batch_of_detections_names_the_photos_that_have_one_face_and_one_person(self):
        one = self.photo("regatta_003.jpg", ["People/" + WREN])
        two = self.photo("regatta_004.jpg", ["People/" + WREN])
        face = {"box": [1, 1, 5, 5], "embedding": [0.5] * 8, "prob": 0.99, "crop_image": None}
        conn = db.connect(self.path)
        try:
            face_records.record_batch(conn, {one: [face], two: [face, dict(face, box=[6, 6, 9, 9])]})
        finally:
            conn.close()
        self.assertEqual({(one, WREN)}, {(path, name) for path, name in look(
            self.path, "SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE f.name IS NOT NULL")})

    def test_a_photo_read_again_names_the_face_its_new_keyword_names(self):
        photo = self.photo("regatta_005.jpg")
        face = self.face(photo)
        self.photo("regatta_005.jpg", ["People/" + ODA])       # the indexer reads the changed file
        self.assertEqual(ODA, self.name(face))

    def test_doing_it_again_changes_nothing(self):
        photo = self.photo("regatta_006.jpg")
        face = self.face(photo)
        self.tag(photo, "People/" + WREN)
        before = look(self.path, "SELECT value FROM generations WHERE name = 'faces'")
        self.tag(photo, "People/" + WREN)
        self.assertEqual(WREN, self.name(face))
        self.assertEqual([(WREN,)], look(self.path, "SELECT name FROM faces"))
        self.assertEqual(before, look(self.path, "SELECT value FROM generations WHERE name = 'faces'"),
                         "a second save wrote a face that was already named")

    def test_the_face_is_a_decided_reference_because_its_photo_names_the_person(self):
        photo = self.photo("regatta_007.jpg")
        self.face(photo)
        self.tag(photo, "People/" + WREN)
        _stamp, (ids, names, _matrix) = identify.decided_faces(self.library)
        self.assertEqual([WREN], names)


class WhatIsNotNamed(Case):
    def test_several_faces_are_left_to_identify_faces(self):
        photo = self.photo("start_001.jpg")
        first, second = self.face(photo), self.face(photo)
        self.tag(photo, "People/" + WREN)
        self.assertEqual([None, None], [self.name(first), self.name(second)])

    def test_several_people_are_left_to_identify_faces(self):
        photo = self.photo("start_002.jpg")
        face = self.face(photo)
        self.tag(photo, "People/" + WREN, "People/" + ODA)
        self.assertIsNone(self.name(face))

    def test_a_person_already_on_another_face_leaves_the_other_person_for_the_face_to_take(self):
        photo = self.photo("start_003.jpg")
        named = self.face(photo, name=WREN, name_source="manual")
        unnamed = self.face(photo)
        self.tag(photo, "People/" + WREN)
        self.assertIsNone(self.name(unnamed), "the only person is already on a face of the photo")
        self.tag(photo, "People/" + WREN, "People/" + ODA)
        self.assertEqual(ODA, self.name(unnamed))
        self.assertEqual((WREN, "manual"), self.row(named)[:2])

    def test_a_face_the_owner_called_nobody_is_not_named(self):
        photo = self.photo("start_004.jpg")
        face = self.face(photo, name_source="manual")
        self.tag(photo, "People/" + WREN)
        self.assertEqual((None, "manual"), self.row(face)[:2])

    def test_an_excluded_face_is_not_named(self):
        photo = self.photo("start_005.jpg")
        face = self.face(photo, excluded=1, name_source="manual")
        self.tag(photo, "People/" + WREN)
        self.assertEqual((None, "manual", 1), self.row(face)[:3])

    def test_a_branch_tag_is_never_a_person(self):
        photo = self.photo("start_006.jpg")
        face = self.face(photo)
        self.tag(photo, CREW)
        self.assertIsNone(self.name(face))

    def test_a_manual_name_survives_the_photo_being_read_again(self):
        photo = self.photo("start_007.jpg")
        face = self.face(photo, name=ODA, name_source="manual")
        self.photo("start_007.jpg", ["People/" + WREN])
        self.assertEqual((ODA, "manual"), self.row(face)[:2])

    def test_a_photo_with_no_row_is_nothing_to_do(self):
        self.tag(os.path.join(self.folder, "never_indexed.jpg"), "People/" + WREN)
        self.assertEqual([], look(self.path, "SELECT id FROM faces"))


class Backfill(Case):
    def seed(self):
        """Faces there before the rule: one photo for each case, never saved since."""
        self.single = self.photo("old_001.jpg", ["People/" + WREN])
        self.single_face = self.face(self.single)
        self.crowd = self.photo("old_002.jpg", ["People/" + WREN])
        self.crowd_faces = [self.face(self.crowd, at(-10)), self.face(self.crowd, at(180))]
        self.left = self.photo("old_003.jpg", ["People/" + WREN, "People/" + ODA])
        self.left_face = self.face(self.left, at(30))
        # Wren's decided face and Oda's, 60 degrees apart: a face between them is alike both.
        elsewhere = self.photo("old_004.jpg", ["People/" + WREN])
        self.face(elsewhere, at(0), name=WREN, name_source="manual")
        also = self.photo("old_005.jpg", ["People/" + ODA])
        self.face(also, at(60), name=ODA, name_source="manual")

    def run_it(self, apply=False):
        return faces_from_tags.faces_from_tags(self.library, apply=apply)

    def test_a_dry_run_counts_and_writes_nothing(self):
        self.seed()
        before = look(self.path, "SELECT id, name, name_source FROM faces ORDER BY id")
        result = self.run_it()
        counts = result.details["counts"]
        self.assertTrue(result.details["dry_run"])
        self.assertEqual(0, result.changed)
        self.assertEqual(1, counts["named_by_the_tag_alone"])
        self.assertEqual(1, counts["photos_named_by_comparison"])
        self.assertEqual(1, counts["photos_left_for_identify_faces"])
        self.assertEqual(2, counts["faces"])
        self.assertEqual(before, look(self.path, "SELECT id, name, name_source FROM faces ORDER BY id"))

    def test_apply_names_the_faces_one_person_leaves_no_doubt_about(self):
        self.seed()
        result = self.run_it(apply=True)
        self.assertEqual(2, result.changed)
        self.assertEqual(WREN, self.name(self.single_face))
        self.assertEqual([WREN, None], [self.name(face) for face in self.crowd_faces],
                         "the face alike Wren's, and not the one that is not")
        self.assertIsNone(self.name(self.left_face), "Wren's and Oda's faces are both alike this one")
        self.assertIsNone(self.row(self.single_face)[1], "automatic, not a decision")
        self.assertEqual(self.node("People/" + WREN), self.row(self.single_face)[3])
        self.assertEqual(0, self.run_it().details["counts"]["faces"], "a second run has nothing left to name")

    def test_undo_takes_them_back(self):
        self.seed()
        change = self.run_it(apply=True).details["change"]
        self.assertIsNotNone(change)
        undone = journal_service.undo(self.library, change, apply=True)
        self.assertFalse(undone.refused, undone.refused)
        self.assertEqual([None, None], [self.name(self.single_face), self.name(self.crowd_faces[0])])
        self.assertIsNone(self.row(self.single_face)[3], "the person's id went with the name")
        self.assertEqual([WREN], [name for (name,) in look(
            self.path, "SELECT pp.name FROM photo_people pp JOIN photos p ON p.id = pp.photo_id"
            " WHERE p.path = ?", (self.single,))])

    def test_a_face_the_owner_marked_nobody_meanwhile_is_not_written(self):
        self.seed()
        planned = faces_from_tags._plan(self.library)
        write(self.path, lambda conn: faces.unname(conn, [self.single_face]))   # "nobody", by hand
        from tagpup.store import journal
        with self.assertRaises(journal.Refusal):
            journal.apply(self.path, "faces_from_tags", faces_from_tags._edits(planned), {})
        self.assertEqual((None, "manual"), self.row(self.single_face)[:2])

    def test_counts_never_carry_a_name(self):
        self.seed()
        details = self.run_it().details
        self.assertNotIn(WREN, str(details["counts"]) + str(details["ids"]))


if __name__ == "__main__":
    unittest.main()

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
from tagpup.store import db, derived, face_tags, faces, people, photos, schema, taxonomy  # noqa: E402
from unittest import mock  # noqa: E402

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

    def face(self, photo_path, embedding=None, box=None, **columns):
        """A face as detection records it (a row with its box), then given `columns`."""
        def add(conn):
            left = 200 * len(faces.in_photo(conn, photo_path))
            shape = box or [left, 0, left + 100, 100]
            face_id = faces.insert(conn, photo_path, shape, embedding or at(0))
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
        detected = [{"box": [10, 10, 110, 110], "embedding": [0.5] * 8, "prob": 0.99, "crop_image": None}]
        self.assertEqual(1, face_records.record_detected(self.path, photo, detected))
        self.assertEqual([(WREN,)], look(self.path, "SELECT name FROM faces"))

    def test_a_batch_of_detections_names_the_photos_that_have_one_face_and_one_person(self):
        one = self.photo("regatta_003.jpg", ["People/" + WREN])
        two = self.photo("regatta_004.jpg", ["People/" + ODA])
        face = {"box": [10, 10, 110, 110], "embedding": [0.5] * 8, "prob": 0.99, "crop_image": None}
        conn = db.connect(self.path)
        try:
            face_records.record_batch(conn, {one: [face], two: [face, dict(face, box=[200, 10, 300, 110])]})
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


class TheTagAloneIsTrustedAsFarAsTheFaceLooksLikeThem(Case):
    """#833: the detector often finds the other person in frame, not the tagged one."""

    def decided(self, degrees=0, name=WREN):
        """A face of `name` a person decided, on another photo."""
        return self.face(self.photo("known_%d_%s.jpg" % (degrees, name[:3])), at(degrees), name=name, name_source="manual")

    def test_a_face_unlike_the_person_is_left_for_identify_faces(self):
        self.decided(0)
        photo = self.photo("frame_001.jpg")
        stranger = self.face(photo, at(120))
        self.tag(photo, "People/" + WREN)
        self.assertIsNone(self.name(stranger))

    def test_a_face_like_the_person_is_named(self):
        self.decided(0)
        photo = self.photo("frame_002.jpg")
        face = self.face(photo, at(30))             # cosine 0.87
        self.tag(photo, "People/" + WREN)
        self.assertEqual(WREN, self.name(face))

    def test_the_gate_is_the_offer_value_not_the_naming_value(self):
        self.decided(0)
        middling, unlike = self.photo("frame_003.jpg"), self.photo("frame_004.jpg")
        in_between, below = self.face(middling, at(40)), self.face(unlike, at(50))     # cosine 0.77 and 0.64
        self.tag(unlike, "People/" + WREN)
        self.assertIsNone(self.name(below), "0.64 is under the value a name is offered at")
        self.tag(middling, "People/" + WREN)
        self.assertEqual(WREN, self.name(in_between), "0.77 is offered, though automatch would not name it")

    def test_a_person_with_no_decided_face_is_named_by_the_tag_alone(self):
        # The owner's example: a new person on one photo. Nothing to compare.
        photo = self.photo("frame_005.jpg")
        face = self.face(photo, at(77))
        self.tag(photo, "People/" + ODA)
        self.assertEqual(ODA, self.name(face))

    def test_a_face_the_rule_left_is_no_reference_for_a_stranger_to_seed(self):
        self.decided(0)
        photo = self.photo("frame_006.jpg")
        stranger = self.face(photo, at(120))
        self.tag(photo, "People/" + WREN)
        _stamp, (ids, _names, _matrix) = identify.decided_faces(self.library)
        self.assertNotIn(stranger, ids)

    def test_a_face_a_person_decided_of_another_is_not_their_reference(self):
        self.decided(0, ODA)                        # Oda's face does not stand in for Wren's
        photo = self.photo("frame_007.jpg")
        face = self.face(photo, at(120))
        self.tag(photo, "People/" + WREN)
        self.assertEqual(WREN, self.name(face), "Wren has no decided face of her own")

    def test_a_background_sized_face_is_never_named_by_the_tag(self):
        photo = self.photo("frame_008.jpg")
        speck = self.face(photo, at(0), box=[0, 0, 40, 40])
        self.tag(photo, "People/" + WREN)
        self.assertIsNone(self.name(speck))
        counts = faces_from_tags.faces_from_tags(self.library).details["counts"]
        self.assertEqual(0, counts["faces"])

    def test_a_second_run_names_nothing_the_first_would_not(self):
        self.decided(0)
        both = self.photo("frame_009.jpg", ["People/" + WREN, "People/" + ODA])
        first, second = self.face(both, at(0)), self.face(both, at(150))
        self.decided(150, ODA)
        run = faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(2, run.changed)
        self.assertEqual((WREN, ODA), (self.name(first), self.name(second)))
        self.assertEqual(0, faces_from_tags.faces_from_tags(self.library).details["counts"]["faces"])

    def test_naming_one_face_does_not_let_the_tag_name_the_other_without_a_look(self):
        # Two faces, two people: the comparison names one; the other would then be "one face, one person".
        self.decided(0)
        photo = self.photo("frame_010.jpg", ["People/" + WREN, "People/" + ODA])
        known, stranger = self.face(photo, at(0)), self.face(photo, at(120))
        self.decided(60, ODA)
        faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(WREN, self.name(known))
        # Oda's decided face is at 60 degrees; the stranger at 120 is 0.5 from it: not named.
        self.assertIsNone(self.name(stranger))
        self.assertEqual(0, faces_from_tags.faces_from_tags(self.library).details["counts"]["faces"])

    def one_pass_leaves_one_more(self):
        """Two faces, two people: A is Wren's alone, B is as like Wren as Oda (no comparison names it), but once A
        is Wren's, B is "one face, one person" and passes the gate against Oda's face."""
        self.decided(0)
        self.decided(60, ODA)
        both = self.photo("frame_011.jpg", ["People/" + WREN, "People/" + ODA])
        return self.face(both, at(0)), self.face(both, at(30))

    def test_what_is_still_to_be_named_after_the_write_is_counted_after_it(self):
        first, second = self.one_pass_leaves_one_more()
        run = faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(1, run.changed)
        self.assertEqual(WREN, self.name(first))
        self.assertEqual({"faces": 1}, run.details["remaining"], "the plan said 1 and nothing was recounted")
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, apply=True, again=True).changed)
        self.assertEqual(ODA, self.name(second))
        self.assertEqual(0, faces_from_tags.faces_from_tags(self.library, apply=True, again=True).changed,
                         "a third run has nothing to do")

    def test_the_command_says_what_is_left_after_the_write(self):
        from click.testing import CliRunner

        import tagpup_cli
        self.one_pass_leaves_one_more()
        said = CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags", "--apply"])
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Still to be named by the rule now: 1 face(s).", said.output)
        dry = CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags"])
        self.assertIn("the face is like them, at 0.80 or more", dry.output)
        self.assertIn("not like them (under 0.70)", dry.output)

    def test_a_second_apply_needs_again_and_says_why(self):
        self.one_pass_leaves_one_more()
        first = faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(1, first.changed)
        refused = faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(faces_from_tags.AGAIN, refused.refused)
        self.assertEqual(0, refused.changed)
        self.assertEqual(1, refused.details["counts"]["faces"], "the counts it would have named are shown")
        again = faces_from_tags.faces_from_tags(self.library, apply=True, again=True)
        self.assertEqual(1, again.changed)

    def test_the_dry_run_says_it_was_applied_before_and_an_undo_forgives(self):
        self.one_pass_leaves_one_more()
        self.assertFalse(faces_from_tags.faces_from_tags(self.library).details["earlier_apply"])
        change = faces_from_tags.faces_from_tags(self.library, apply=True).details["change"]
        self.assertTrue(faces_from_tags.faces_from_tags(self.library).details["earlier_apply"])
        journal_service.undo(self.library, change, apply=True)
        self.assertFalse(faces_from_tags.faces_from_tags(self.library).details["earlier_apply"])
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, apply=True).changed)

    def test_the_command_refuses_a_second_apply_without_again(self):
        from click.testing import CliRunner

        import tagpup_cli
        self.one_pass_leaves_one_more()
        runner = CliRunner()
        self.assertEqual(0, runner.invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags", "--apply"]).exit_code)
        said = runner.invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags", "--apply"])
        self.assertEqual(1, said.exit_code)
        self.assertIn("--again", said.output)
        self.assertIn("Would name 1 face(s). Nothing changed.", said.output)
        dry = runner.invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags"])
        self.assertIn("Applied before", dry.output)
        self.assertEqual(0, runner.invoke(tagpup_cli.cli, ["--db", self.path, "faces-from-tags", "--apply", "--again"]).exit_code)

    def test_the_dry_run_counts_the_bands(self):
        self.decided(0)
        for name, degrees in (("a", 10), ("b", 40), ("c", 120)):        # 0.98, 0.77, -0.5
            photo = self.photo("band_%s.jpg" % name, ["People/" + WREN])
            self.face(photo, at(degrees))
        fresh = self.photo("band_d.jpg", ["People/" + ODA])
        self.face(fresh, at(5))
        counts = faces_from_tags.faces_from_tags(self.library).details["counts"]
        self.assertEqual((4, 1, 1, 1, 1), (counts["one_face_one_person"], counts["like_them_from_0.80"],
                                           counts["like_them_from_0.70_to_0.80"], counts["not_like_them"],
                                           counts["person_has_no_decided_face"]))
        self.assertEqual(3, counts["faces"])


class APersonWithNoDecidedFace(Case):
    """#839: named by the tag alone only when exactly one photo of theirs waits; the library's count, so the
    same for the backfill and the live rule, for one at a time and for a batch, in any order."""

    def several(self, count=3):
        """`count` photos of Oda, each with one unnamed face, tagged. Returns the photos' paths."""
        made = []
        for n in range(count):
            photo = self.photo("many_%d.jpg" % n, ["People/" + ODA])
            self.face(photo, at(30 * n))
            made.append(photo)
        return made

    def named(self):
        return look(self.path, "SELECT name FROM faces WHERE name IS NOT NULL")

    def ids_of(self, photo_paths):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return face_tags.photo_ids_of(conn, photo_paths)
        finally:
            conn.close()

    def test_one_photo_is_named_by_the_tag_alone(self):
        (only,) = self.several(1)
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, apply=True).changed)
        self.assertEqual([(ODA,)], self.named())
        self.assertIsNotNone(only)

    def test_several_photos_are_all_left_and_counted(self):
        self.several(3)
        run = faces_from_tags.faces_from_tags(self.library, apply=True)
        self.assertEqual(0, run.changed)
        counts = run.details["counts"]
        self.assertEqual((3, 0), (counts["not_decidable_yet"], counts["person_has_no_decided_face"]))
        self.assertEqual([], self.named())

    def test_a_batch_and_one_at_a_time_in_either_order_name_the_same_faces(self):
        photos_ = self.several(3)
        ids = self.ids_of(photos_)

        def run(order):
            for photo_id in order:
                write(self.path, lambda conn, photo_id=photo_id: face_tags.name_photos(conn, [photo_id]))

        run(ids)
        self.assertEqual([], self.named())
        run(list(reversed(ids)))
        self.assertEqual([], self.named())
        write(self.path, lambda conn: face_tags.name_photos(conn, ids))
        self.assertEqual([], self.named(), "a batch named what one at a time did not")

    def test_the_other_photos_of_the_person_need_a_face_to_be_named_to_count(self):
        # A photo whose face is a speck, or already named, or ruled out, is not one that waits.
        (first,) = self.several(1)
        other = self.photo("not_waiting.jpg", ["People/" + ODA])
        self.face(other, at(90), box=[0, 0, 30, 30])
        self.assertEqual(1, faces_from_tags.faces_from_tags(self.library, apply=True).changed)
        self.assertEqual([(ODA,)], self.named())
        self.assertIsNotNone(first)


class AIndexPassReadsTheTreeOnce(Case):
    """#838: faces named while photos are indexed rebuild their photos' people with the tree the pass read."""

    def test_a_batch_that_names_faces_reads_the_tree_no_more_than_the_pass_did(self):
        photo_paths = []
        for n in range(5):
            photo_paths.append(self.photo("pass_%d.jpg" % n))
            self.face(photo_paths[-1])
        reads = []
        real = taxonomy.read_people_vocabulary

        def counted(conn):
            reads.append(1)
            return real(conn)

        def indexed(conn):
            vocabulary = real(conn)                       # the pass reads the tree once
            for photo_path in photo_paths:
                row = photo_rows.as_read(photo_path, {"XMP:Subject": ["People/" + WREN]})
                photos.record_indexed(conn, photo_path, row, known=vocabulary)

        with mock.patch.object(taxonomy, "read_people_vocabulary", counted):
            write(self.path, indexed)
        self.assertEqual(5, len(look(self.path, "SELECT name FROM faces WHERE name IS NOT NULL")), "the faces were not named")
        self.assertEqual([], reads, "the tree was read again for a photo that named a face")


class APassReadsAPersonsDecidedFacesOnce(Case):
    """#841: the gate reads a person's decided faces once for a batch, not once for each photo."""

    def test_fifty_photos_of_one_person_read_their_decided_faces_once(self):
        known = self.photo("known.jpg")
        self.face(known, at(0), name=WREN, name_source="manual")
        photo_paths = []
        for n in range(50):
            photo_paths.append(self.photo("fifty_%d.jpg" % n))
            self.face(photo_paths[-1], at(n % 10))
        reads = []
        real = face_tags._decided_of

        def counted(conn, name):
            reads.append(name)
            return real(conn, name)

        def indexed(conn):
            batch = derived.Batch(conn)
            for photo_path in photo_paths:
                row = photo_rows.as_read(photo_path, {"XMP:Subject": ["People/" + WREN]})
                photos.record_indexed(conn, photo_path, row, batch=batch)

        with mock.patch.object(face_tags, "_decided_of", counted):
            write(self.path, indexed)
        self.assertEqual(50, len(look(self.path, "SELECT name FROM faces WHERE name = ? AND name_source IS NULL", (WREN,))))
        self.assertEqual(1, len(reads), "the person's decided faces were read again for a photo of the pass")

    def test_a_save_on_its_own_still_reads_them_fresh(self):
        known = self.photo("known_2.jpg")
        self.face(known, at(0), name=WREN, name_source="manual")
        photo = self.photo("alone.jpg")
        face = self.face(photo, at(5))
        self.tag(photo, "People/" + WREN)
        self.assertEqual(WREN, self.name(face))


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

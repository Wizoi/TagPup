"""Every tag has an id (owner, 2026-10-10): a keyword the index reads, and a name a face or a photo's person field holds, that has
no node in the tag tree is given one when it is first seen -- a keyword its own path, a name a node directly under the tree's
root marked as a person -- so no row is left holding a name and no id. taxonomy.ensure_node is the one place; photos.record_indexed
(the read) and faces' writers (a name) reach it. The owner merges the nodes into the right branch with the tag merge.

Nothing is left holding a name and no id (owner, 2026-10-11: only the id): a name that two people have, or that is a group's
leaf, is a different path, so it gets a node directly under the root; a bare keyword is a path of its own, never a person's
name. Only a name that is the path of a group at the top is given none, and is not listed as a person.
"""
import os
import sys
import unicodedata
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402
import people_by_id  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.services import tags as tag_service  # noqa: E402
from tagpup.store import db, derived, faces, person_ids, taxonomy  # noqa: E402

NEW = "Trips/Harbour Walk/Pier"
ZED = "Zed Quill"


class Seen(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.later = os.path.join(os.path.dirname(self.photo), "regatta_003.jpg")

    def read_photo(self, subject=(), people=None, path=None):
        fields = {"XMP:Subject": list(subject)}
        if people:
            fields["XMP:PersonInImage"] = fields["XMP:RegionName"] = list(people)
        return people_by_id.write(self.path, lambda conn: photo_rows.add_read(conn, path or self.later, fields))

    def tags(self):
        return [tag for (tag,) in people_by_id.look(self.path, "SELECT tag FROM tag_taxonomy ORDER BY id")]

    def count(self, tag):
        return sum(1 for each in self.tags() if each.lower() == tag.lower())

    def rows_of(self, photo_id):
        return people_by_id.look(self.path, "SELECT t.tag FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id"
                                 " WHERE pt.photo_id = ? ORDER BY t.tag", (photo_id,))

    # ---- a keyword

    def test_a_keyword_with_no_node_gets_its_path_and_its_photo_the_row(self):
        photo_id = self.read_photo([NEW])
        for level in ("Trips", "Trips/Harbour Walk", NEW):
            self.assertEqual(1, self.count(level), level)
        self.assertIn((NEW,), self.rows_of(photo_id))

    def test_a_keyword_is_never_made_twice_by_case_form_or_separator(self):
        self.read_photo([NEW])
        accent = chr(233)   # e acute, as one character and (NFD) as e and a combining accent
        nfd = unicodedata.normalize("NFD", "Places/Caf" + accent)
        self.read_photo([NEW.upper(), NEW.replace("/", chr(92)), NEW + "/ ", " " + NEW, nfd], path=self.photo)
        self.read_photo([unicodedata.normalize("NFC", nfd), ("places/CAF" + accent).lower()], path=self.other)
        cafes = [tag for tag in self.tags() if tag.lower().startswith("places/caf")]
        self.assertEqual(1, len(cafes), cafes)
        self.assertEqual(1, self.count(NEW))
        self.assertEqual(1, self.count("Trips"))

    def test_reading_again_makes_nothing(self):
        self.read_photo([NEW, "Beach"])
        before = self.tags()
        self.read_photo([NEW, "Beach"])
        self.assertEqual(before, self.tags())

    def test_a_keyword_a_node_already_has_gets_no_second_node(self):
        before = self.tags()
        self.read_photo(["pets/max", "FAMILY/THACKERAY"])
        self.assertEqual(before, self.tags())

    def test_a_keyword_equal_to_a_groups_name_is_the_group_and_stays_one(self):
        self.read_photo(["Family/Thackeray"])
        self.assertEqual(1, self.count("Family/Thackeray"))
        self.assertEqual(0, self.count("Thackeray"))

    def test_a_bare_keyword_that_is_a_persons_name_is_a_path_of_its_own_and_names_nobody(self):
        photo_id = self.read_photo(["Wren Halloway"])
        self.assertEqual(1, self.count("Wren Halloway"))
        self.assertEqual([(0,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'Wren Halloway'"))
        self.assertIn(("Wren Halloway",), self.rows_of(photo_id))
        self.assertEqual([], self.listed(self.later))

    def test_a_bare_keyword_is_a_top_level_word_tag_not_a_person(self):
        self.read_photo(["Regatta"])
        self.assertEqual([(0,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'Regatta'"))

    def test_a_keyword_under_a_face_root_makes_a_person_the_faces_of_that_name_follow(self):
        def name(conn):
            return faces.insert(conn, self.photo, [40, 0, 50, 10], b"\x07" * 8, name=ZED)
        face = people_by_id.write(self.path, name)
        # The name came first (a top-level node), then a file says the person is filed in a branch.
        self.assertEqual(1, self.count(ZED))
        node = self.node(ZED)
        self.assertEqual((node, ZED), self.face(face)[:2])
        self.read_photo(["People/Zed Quill Jr"], path=self.other)
        self.assertEqual(1, self.count("People/Zed Quill Jr"))
        self.assertEqual((1,), people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'People/Zed Quill Jr'")[0])
        listed = self.listed(self.other)
        self.assertEqual([(self.node("People/Zed Quill Jr"), "Zed Quill Jr", "keyword")], listed)

    # ---- a name

    def test_a_face_named_with_a_name_no_node_has_gets_a_node_at_the_top_and_the_id(self):
        face_id = self.faces[0]
        people_by_id.write(self.path, lambda conn: faces.name(conn, [face_id], ZED))
        node = self.node(ZED)
        self.assertIsNotNone(node)
        self.assertEqual([(None,)], people_by_id.look(self.path, "SELECT parent_id FROM tag_taxonomy WHERE id = ?", (node,)))
        self.assertEqual([(1,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE id = ?", (node,)))
        self.assertEqual((node, ZED), self.face(face_id)[:2])
        self.assertEqual([(node, ZED, "face")], self.listed(self.photo)[-1:])

    def test_a_face_is_named_by_a_person_not_a_path(self):
        """A path no person has once made nodes `a` and `a/b` (has_face 0) and named the face for a node that is no person; a
        person's own path still names them. Lifted: test_a_face_is_named_by_a_person_not_a_path.py. Mutation: faces._target without
        its refusal of a name with a separator fails the first assertion."""
        before = len(self.tags())
        with self.assertRaises(ValueError):
            people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], "Vessels/Gull"))
        self.assertEqual(before, len(self.tags()))
        self.assertEqual((None, None), self.face(self.faces[0])[:2])
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[1]], people_by_id.WREN))
        self.assertEqual((self.node(people_by_id.WREN), "Wren Halloway"), self.face(self.faces[1])[:2])

    def test_naming_two_faces_with_the_same_new_name_makes_one_node(self):
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], ZED))
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[1], self.faces[2]], ZED.upper()))
        self.assertEqual(1, self.count(ZED))
        self.assertEqual({self.node(ZED)}, {self.face(each)[0] for each in self.faces})

    def test_a_person_field_names_nobody_and_makes_no_node(self):
        """A file that names people in XMP PersonInImage and RegionName (0 photos in the three libraries, 2026-10-15) gets no
        node and lists nobody, whether a person is called the name or not: a bare name is never linked to a person by its leaf
        (owner, DECISIONS 2026-10-15). Mutation: the old reader made Zed Quill a node and listed Wren Halloway by name."""
        before = self.tags()
        self.read_photo(people=[ZED, "Wren Halloway", "Sam", "Family"])
        self.assertEqual(before, self.tags())
        self.assertEqual([], self.listed(self.later))

    def test_a_name_an_existing_person_has_makes_no_duplicate(self):
        before = self.tags()
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], "wren halloway"))
        self.assertEqual(before, self.tags())
        self.assertEqual((self.node(people_by_id.WREN), "Wren Halloway"), self.face(self.faces[0])[:2])

    def test_a_childless_word_tag_of_the_name_at_the_top_becomes_the_person(self):
        self.read_photo(["Kentridge"])
        self.assertEqual([(0,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'Kentridge'"))
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], "Kentridge"))
        self.assertEqual(1, self.count("Kentridge"))
        self.assertEqual([(1,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'Kentridge'"))
        self.assertEqual(self.node("Kentridge"), self.face(self.faces[0])[0])

    def test_a_person_made_at_the_top_is_filed_by_their_node_and_is_no_people_root(self):
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], ZED))
        filed, roots = taxonomy.people_filing(self.path)
        self.assertEqual([ZED], filed[ZED.lower()])
        self.assertNotIn(ZED, roots, "a person is not a root that people are filed under")
        self.assertNotIn(ZED, taxonomy.face_roots(self.path))

    def test_the_first_node_of_an_empty_tree_keeps_people_the_face_root(self):
        def empty(conn):
            conn.execute("DELETE FROM tag_taxonomy")
            return faces.name(conn, [self.faces[0]], ZED)
        people_by_id.write(self.path, empty)
        self.assertEqual([("People",)], people_by_id.look(self.path, "SELECT tag FROM tag_taxonomy WHERE has_face = 1 AND parent_id IS NULL AND tag = 'People'"))

    def test_rapid_clicks_creating_the_same_new_person_twice_make_one_node(self):
        """The New Person dialog creates the tag, then names the faces: a second click finds the node, and a face named
        meanwhile by the name finds it too."""
        first = tag_service.create(self.library, ZED, has_face=1)
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], ZED))
        second = tag_service.create(self.library, ZED, has_face=1)
        self.assertEqual((1, 0), (first.changed, second.changed))
        self.assertEqual(first.details["id"], second.details["id"])
        self.assertEqual(1, self.count(ZED))
        self.assertEqual(first.details["id"], self.face(self.faces[0])[0])

    # ---- a file never turns a person into a group

    def used_sam(self):
        """Sam of the Thackerays: a photo carries them and a face is named so."""
        node = self.node(people_by_id.SAM_T)
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], node))
        return node

    def test_a_keyword_under_a_person_faces_or_photos_use_gets_no_node_and_they_stay_a_person(self):
        node = self.used_sam()
        listed, before = self.listed(self.photo), self.tags()
        self.read_photo([people_by_id.SAM_T + "/Swim Team"])
        self.assertEqual(before, self.tags())
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(node, person_ids.resolve(conn, node).id)
        finally:
            conn.close()
        self.assertEqual(listed, self.listed(self.photo))
        self.assertEqual(node, self.face(self.faces[0])[0])

    def test_the_doctor_counts_such_keywords(self):
        self.used_sam()
        self.read_photo([people_by_id.SAM_T + "/Swim Team"])
        lines = []
        doctor.report(self.path, out=lines.append)
        self.assertTrue([line for line in lines if line.startswith("file keywords under a person: 1 ")], lines)

    def test_a_keyword_under_a_person_nothing_uses_is_made(self):
        self.read_photo([people_by_id.WREN + "/Extra"])
        self.assertEqual(1, self.count(people_by_id.WREN + "/Extra"))

    # ---- a batch makes its nodes in one tree edit

    def test_a_batch_makes_all_its_nodes_in_one_tree_edit(self):
        lists = [["Trips/A%d" % n, "Trips/B%d" % n, "Activity/C%d" % n] for n in range(20)]
        with mock.patch.object(person_ids, "follow_tree", wraps=person_ids.follow_tree) as followed:
            made = people_by_id.write(self.path, lambda conn: derived.make_nodes(conn, lists, derived.Batch(conn)))
        self.assertEqual(60 + 2, made)
        self.assertEqual(1, followed.call_count, "one scan of the library for the batch, not one for each node")

    def test_two_spellings_of_a_keyword_in_one_batch_are_one_node(self):
        made = people_by_id.write(self.path, lambda conn: derived.make_nodes(conn, [["Trips/Coast"], ["trips/COAST"]], derived.Batch(conn)))
        self.assertEqual(2, made)
        self.assertEqual(1, self.count("Trips/Coast"))

    def test_a_batch_that_would_make_more_than_the_cap_makes_none_and_counts_them(self):
        self.assertEqual(500, derived.MAX_NEW_NODES_PER_BATCH)
        lists = [["Zz%d/x" % n] for n in range(260)]   # 520 levels
        before, flagged = self.tags(), []

        def work(conn):
            batch = derived.Batch(conn)
            made = derived.make_nodes(conn, lists, batch)
            flagged.append(batch.flagged)
            return made
        with mock.patch.object(person_ids, "follow_tree", wraps=person_ids.follow_tree) as followed:
            made = people_by_id.write(self.path, work)
        self.assertEqual((0, [520], 0), (made, flagged, followed.call_count))
        self.assertEqual(before, self.tags())
        exactly = [["Yy%d/x" % n] for n in range(250)]   # 500 levels: made
        self.assertEqual(500, people_by_id.write(self.path, lambda conn: derived.make_nodes(conn, exactly, derived.Batch(conn))))

    def test_ensure_node_run_twice_is_one_node(self):
        def twice(conn):
            first = taxonomy.ensure_node(conn, ZED, person=True)
            return first, taxonomy.ensure_node(conn, ZED, person=True)
        (first, made), (second, again) = people_by_id.write(self.path, twice)
        self.assertEqual((first, True, False), (second, made, again))


if __name__ == "__main__":
    unittest.main()

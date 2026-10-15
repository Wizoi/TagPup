"""A group tag -- a tag with tags under it, such as Family/Marlowe beside Family/Marlowe/Tamsin Marlowe -- on a
photo is ordinary tagging and names nobody (owner, 2026-10-09; docs/findings.md, #986).

On photo_index 4 such tags sat on 65 photos and each made a `photo_people` row, so the photo's
people listed a family, and tags-from-faces, faces-from-tags and the names to review took the
family for a person. The one rule is tagpup.core.vocabulary.extract_people / people_in_photo,
fed the tree's groups by tagpup.store.taxonomy.read_people_vocabulary; everything that lists
people reads `photo_people`, which `people.rebuild` writes by that rule, and the doctor's
people_out_of_date asks the same rule.

The same rule from the other side: a person is a node marked as a person with no node under it, wherever it sits (owner,
2026-10-10, "every tag has an id"): one directly under the tree's root is as much a person as People/Wren Halloway. A node with
nodes under it is a group (never a person), and the root of a branch that holds people is that group.
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import people_of  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import checks, db, faces, people, person_ids, taxonomy  # noqa: E402

CORA = "Tamsin Marlowe"
FAMILY = "Family/Marlowe"          # a group: Cora is filed under it
PERSON = FAMILY + "/" + CORA


class TheRule(unittest.TestCase):
    def known(self, group_tags=(FAMILY,)):
        return vocabulary.PeopleVocabulary.from_rows(
            ["Family"], [("Family", "Family"), (FAMILY, "Marlowe"), (PERSON, CORA)], group_tags)

    def test_a_group_tag_names_nobody(self):
        self.assertEqual([], vocabulary.extract_people({}, [FAMILY], self.known()))

    def test_the_group_beside_its_person_names_only_the_person(self):
        self.assertEqual([CORA], vocabulary.extract_people({}, [FAMILY, PERSON], self.known()))

    def test_a_flat_keyword_spelled_like_the_group_names_nobody(self):
        self.assertEqual([], vocabulary.extract_people({}, ["Marlowe"], self.known()))
        self.assertEqual([], vocabulary.extract_people({}, ["marlowe"], self.known()))

    def test_a_group_is_read_by_the_spelling_a_file_gives_it(self):
        self.assertEqual([], vocabulary.extract_people({}, ["family\\marlowe"], self.known()))

    def test_a_person_of_the_groups_name_elsewhere_is_named_by_their_own_path(self):
        known = vocabulary.PeopleVocabulary.from_rows(
            ["Family", "Friends"], [(FAMILY, "Marlowe"), (PERSON, CORA), ("Friends/Marlowe", "Marlowe")], [FAMILY])
        self.assertEqual([], vocabulary.extract_people({}, ["Marlowe", FAMILY], known), "a bare name is a path of its own")
        self.assertEqual(["Marlowe"], vocabulary.extract_people({}, ["Friends/Marlowe", FAMILY], known))

    def test_a_face_named_so_is_a_name_and_stays_listed(self):
        # Faces carry names, not tags: a name no person node has is the owner's to settle (#985), not dropped here.
        self.assertEqual(["Marlowe"], vocabulary.people_in_photo({}, [FAMILY], ["Marlowe"], self.known()))

    def test_a_person_field_names_who_it_names(self):
        self.assertEqual(["Marlowe"], vocabulary.extract_people({"XMP:PersonInImage": ["Marlowe"]}, [FAMILY], self.known()))


class InTheLibrary(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.write(lambda conn: taxonomy.add_path(conn, FAMILY, root_has_face=1))
        self.alone = self.photo("alone.jpg", [FAMILY])
        self.both = self.photo("both.jpg", [FAMILY, PERSON])
        self.person = self.photo("person.jpg", [PERSON])
        # Only `alone` is listed: a keyword with no node names nobody (the indexer gives it a node when it reads the file, and
        # these rows are written by hand), so the two photos that carry Cora's path list her once the node is there.
        self.write(lambda conn: people.rebuild_photos(conn, [self.alone]))

    def photo(self, name, tags):
        path = self.lib.photo(name)
        self.lib.add_row(path, tags=tags)
        return path

    def write(self, operation):
        return db.write_with_connection(self.lib.library.path, operation)

    def read(self, operation):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return operation(conn)
        finally:
            conn.close()

    def listed(self, path):
        return self.read(lambda conn: people_of(conn, path))

    def test_before_it_has_a_person_under_it_the_tag_is_a_person(self):
        self.assertEqual(["Marlowe"], self.listed(self.alone))   # the tree files Marlowe as a leaf: the leaf rule

    def test_a_person_given_a_tag_under_them_leaves_their_photos_lists(self):
        self.write(lambda conn: taxonomy.add_node(conn, PERSON))
        self.assertEqual([], self.listed(self.alone))
        self.assertEqual([CORA], self.listed(self.both))
        self.assertEqual([CORA], self.listed(self.person))

    def test_the_doctor_counts_the_photos_a_writer_that_did_not_follow_left(self):
        # add_path writes the tree and rebuilds nothing, as an older writer: the lists are as they were. Three photos are
        # out of date: the one that loses the person who is a group now, and the two that name Cora by a path that has a node
        # now.
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        self.assertEqual(3, len(self.read(lambda conn: people.stale(conn))))
        stale = self.read(lambda conn: checks.people_out_of_date(conn))
        self.assertEqual(3, stale.count)

    def test_the_rebuild_the_doctor_runs_mends_them_and_the_doctor_then_agrees(self):
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        self.assertEqual(3, self.write(lambda conn: people.rebuild(conn)))
        self.assertEqual([], self.listed(self.alone))
        self.assertEqual([CORA], self.listed(self.both))
        self.assertEqual(0, self.read(lambda conn: checks.people_out_of_date(conn)).count)

    def test_the_doctors_rebuild_says_then_mends_and_counts_what_it_wrote(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(1, doctor.rebuild_derived(self.lib.library.path), "a dry run")
        self.assertIn("3 photo(s) list people the rule makes otherwise", said.getvalue())
        self.assertEqual(["Marlowe"], self.listed(self.alone), "a dry run writes nothing")
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(0, doctor.rebuild_derived(self.lib.library.path, apply=True))
        self.assertIn("photos' people rebuilt: 3 photo(s) changed", said.getvalue())
        self.assertEqual([], self.listed(self.alone))
        self.assertEqual([CORA], self.listed(self.both))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, doctor.rebuild_derived(self.lib.library.path))

    def test_a_group_is_not_filed_or_resolved_as_a_person(self):
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        filed, _roots = taxonomy.people_filing(self.lib.library.path)
        self.assertNotIn("marlowe", filed)
        self.assertEqual([PERSON], filed["tamsin marlowe"])

    def test_a_group_emptied_is_a_person_again(self):
        self.write(lambda conn: taxonomy.add_node(conn, PERSON))
        self.assertEqual([], self.listed(self.alone))
        self.write(lambda conn: taxonomy.remove_node(conn, PERSON))
        self.assertEqual(["Marlowe"], self.listed(self.alone))



SOLO = "Ines Calloway"


class TopLevelPerson(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.solo_id = people_by_id.write(self.path, self.make_solo)

    def make_solo(self, conn):
        with people.tree_edit(conn):
            return taxonomy.add_path(conn, SOLO, root_has_face=1)

    def read(self, operation):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return operation(conn)
        finally:
            conn.close()

    def test_a_top_level_node_that_holds_faces_and_has_none_under_it_is_a_person(self):
        found = self.read(lambda conn: person_ids.resolve(conn, SOLO))
        self.assertEqual((self.solo_id, SOLO, SOLO), tuple(found))
        self.assertEqual(found, self.read(lambda conn: person_ids.resolve(conn, self.solo_id)))

    def test_the_root_of_a_branch_is_still_a_group_and_no_person(self):
        with self.assertRaises(person_ids.GroupNotPerson):
            self.read(lambda conn: person_ids.resolve(conn, "Family"))

    def test_a_new_librarys_empty_people_root_is_where_people_are_filed_and_no_person(self):
        def empty(conn):
            taxonomy.remove_node(conn, people_by_id.WREN)
        people_by_id.write(self.path, empty)
        self.assertIsNone(self.read(lambda conn: person_ids.resolve(conn, "People")))
        self.assertNotIn("people", taxonomy.people_filing(self.path)[0])
        self.assertEqual([], vocabulary.extract_refs({}, ["People"], self.read(taxonomy.read_people_vocabulary)))

    def test_a_top_level_person_becomes_a_group_when_a_node_goes_under_it(self):
        def under(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, SOLO + "/Swim Team")
        people_by_id.write(self.path, under)
        with self.assertRaises(person_ids.GroupNotPerson):
            self.read(lambda conn: person_ids.resolve(conn, SOLO))

    def test_a_keyword_that_is_a_top_level_person_names_them(self):
        known = self.read(taxonomy.read_people_vocabulary)
        refs = vocabulary.extract_refs({}, [SOLO.lower()], known)
        self.assertEqual([vocabulary.Ref(self.solo_id, SOLO)], refs)
        self.assertEqual([], vocabulary.extract_refs({}, ["Family"], known))

    def test_a_photo_carrying_the_top_level_person_lists_them(self):
        def add(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, add)
        self.assertEqual([(self.solo_id, SOLO, "keyword")], self.listed(self.other))

    def test_a_node_may_not_be_put_under_a_top_level_person_a_photo_carries(self):
        def carry(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, carry)
        with self.assertRaises(person_ids.PersonHasNoChildren):
            self.read(lambda conn: taxonomy.refuse_child_of_person(conn, SOLO + "/Swim Team"))

    def test_the_filing_of_people_lists_the_top_level_person_by_their_path(self):
        filed, _roots = taxonomy.people_filing(self.path)
        self.assertEqual([SOLO], filed[vocabulary.key(SOLO)])

    def test_the_navigator_files_them_at_the_top_level_and_not_as_unfiled(self):
        from tagpup.services import library_view
        def add(conn):
            photo_rows.add_read(conn, self.other, {"XMP:Subject": [SOLO]})
            people.rebuild(conn)
        people_by_id.write(self.path, add)
        found = library_view.navigator(self.library, "people")
        self.assertEqual(0, found["unfiled"])
        self.assertIn(("Top level", 1), [(group["tag"], group["count"]) for group in found["groups"]])
        self.assertEqual("Top level", [each for each in found["people"] if each["name"] == SOLO][0]["group"])

    def test_faces_can_name_them(self):
        def name(conn):
            return faces.name(conn, [self.faces[0]], self.solo_id)
        self.assertEqual(1, people_by_id.write(self.path, name))


if __name__ == "__main__":
    unittest.main()

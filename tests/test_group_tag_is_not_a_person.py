"""A group tag -- a tag with tags under it, such as Family/Idzi beside Family/Idzi/Cora Idzi -- on a
photo is ordinary tagging and names nobody (owner, 2026-10-09; docs/findings.md, #986).

On photo_index 4 such tags sat on 65 photos and each made a `photo_people` row, so the photo's
people listed a family, and tags-from-faces, faces-from-tags and the names to review took the
family for a person. The one rule is tagpup.core.vocabulary.extract_people / people_in_photo,
fed the tree's groups by tagpup.store.taxonomy.read_people_vocabulary; everything that lists
people reads `photo_people`, which `people.rebuild` writes by that rule, and the doctor's
people_out_of_date asks the same rule.
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import people_of  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import checks, db, people, taxonomy  # noqa: E402

CORA = "Cora Idzi"
FAMILY = "Family/Idzi"          # a group: Cora is filed under it
PERSON = FAMILY + "/" + CORA


class TheRule(unittest.TestCase):
    def known(self, group_tags=(FAMILY,)):
        return vocabulary.PeopleVocabulary.from_rows(
            ["Family"], [("Family", "Family"), (FAMILY, "Idzi"), (PERSON, CORA)], group_tags)

    def test_a_group_tag_names_nobody(self):
        self.assertEqual([], vocabulary.extract_people({}, [FAMILY], self.known()))

    def test_the_group_beside_its_person_names_only_the_person(self):
        self.assertEqual([CORA], vocabulary.extract_people({}, [FAMILY, PERSON], self.known()))

    def test_a_flat_keyword_spelled_like_the_group_names_nobody(self):
        self.assertEqual([], vocabulary.extract_people({}, ["Idzi"], self.known()))
        self.assertEqual([], vocabulary.extract_people({}, ["idzi"], self.known()))

    def test_a_group_is_read_by_the_spelling_a_file_gives_it(self):
        self.assertEqual([], vocabulary.extract_people({}, ["family\\idzi"], self.known()))

    def test_a_person_of_the_groups_name_elsewhere_is_still_a_person(self):
        known = vocabulary.PeopleVocabulary.from_rows(
            ["Family", "Friends"], [(FAMILY, "Idzi"), (PERSON, CORA), ("Friends/Idzi", "Idzi")], [FAMILY])
        self.assertEqual(["Idzi"], vocabulary.extract_people({}, ["Idzi", FAMILY], known))

    def test_without_the_tree_a_path_under_a_face_root_is_a_person_as_before(self):
        self.assertEqual(["Idzi"], vocabulary.extract_people({}, [FAMILY.replace("Family", "People")]))

    def test_a_face_named_so_is_a_name_and_stays_listed(self):
        # Faces carry names, not tags: a name no person node has is the owner's to settle (#985), not dropped here.
        self.assertEqual(["Idzi"], vocabulary.people_in_photo({}, [FAMILY], ["Idzi"], self.known()))

    def test_a_person_field_names_who_it_names(self):
        self.assertEqual(["Idzi"], vocabulary.extract_people({"XMP:PersonInImage": ["Idzi"]}, [FAMILY], self.known()))


class InTheLibrary(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.write(lambda conn: taxonomy.add_path(conn, FAMILY, root_has_face=1))
        self.alone = self.photo("alone.jpg", [FAMILY])
        self.both = self.photo("both.jpg", [FAMILY, PERSON])
        self.person = self.photo("person.jpg", [PERSON])
        self.write(lambda conn: people.rebuild(conn))

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
        self.assertEqual(["Idzi"], self.listed(self.alone))   # the tree files Idzi as a leaf: the leaf rule

    def test_a_person_given_a_tag_under_them_leaves_their_photos_lists(self):
        self.write(lambda conn: taxonomy.add_node(conn, PERSON))
        self.assertEqual([], self.listed(self.alone))
        self.assertEqual([CORA], self.listed(self.both))
        self.assertEqual([CORA], self.listed(self.person))

    def test_the_doctor_counts_the_photos_a_writer_that_did_not_follow_left(self):
        # add_path writes the tree and rebuilds nothing, as an older writer: the lists are as they were.
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        self.assertEqual(2, len(self.read(lambda conn: people.stale(conn))))
        stale = self.read(lambda conn: checks.people_out_of_date(conn))
        self.assertEqual(2, stale.count)

    def test_the_rebuild_the_doctor_runs_mends_them_and_the_doctor_then_agrees(self):
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        self.assertEqual(2, self.write(lambda conn: people.rebuild(conn)))
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
        self.assertIn("2 photo(s) list people the rule makes otherwise", said.getvalue())
        self.assertEqual(["Idzi"], self.listed(self.alone), "a dry run writes nothing")
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(0, doctor.rebuild_derived(self.lib.library.path, apply=True))
        self.assertIn("photos' people rebuilt: 2 photo(s) changed", said.getvalue())
        self.assertEqual([], self.listed(self.alone))
        self.assertEqual([CORA], self.listed(self.both))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, doctor.rebuild_derived(self.lib.library.path))

    def test_a_group_is_not_filed_or_resolved_as_a_person(self):
        self.write(lambda conn: taxonomy.add_path(conn, PERSON))
        filed, _roots = taxonomy.people_filing(self.lib.library.path)
        self.assertNotIn("idzi", filed)
        self.assertEqual([PERSON], filed["cora idzi"])
        self.assertNotIn("idzi", taxonomy.people_paths(self.lib.library.path))

    def test_a_group_emptied_is_a_person_again(self):
        self.write(lambda conn: taxonomy.add_node(conn, PERSON))
        self.assertEqual([], self.listed(self.alone))
        self.write(lambda conn: taxonomy.remove_node(conn, PERSON))
        self.assertEqual(["Idzi"], self.listed(self.alone))


if __name__ == "__main__":
    unittest.main()

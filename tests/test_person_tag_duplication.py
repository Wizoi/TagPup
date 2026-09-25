"""One person, one tag.

Reported from the running app: the Add Person list offered both "Josephine Sandoval" and
"People/Josephine Sandoval" -- the same person twice, a choice nobody can make correctly
because both do the same thing. 55 such pairs in the kr-track library.

The chain that made them: extract_people() returns leaf names, deliberately, because
`people` is the flattened view used for display and matching. Indexing then passed that
list to taxonomy.add_tags(), which treats each string as a whole tag path -- so a bare
name became a root node beside the People/<name> the hierarchical keyword had already
created. It ran on every index, so a cleanup done once was undone by the next run: the
taxonomy had been taken from 51 of these to zero before, and they came back.
"""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.store.taxonomy import TagTaxonomy

from tagpup.store import db, schema  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402


class TaxonomyTestBase(unittest.TestCase):
    def taxonomy(self, paths=(), face_roots=()):
        """A tree holding `paths`. Without `face_roots`, of a library that does not
        exist: held in memory only, with a new library's People as its face root. With
        them, of a library whose tree flags those roots as holding faces -- the only
        thing that says a root does (docs/findings.md, #66)."""
        folder = tempfile.mkdtemp(prefix="tagpup_people_")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "library.db")
        if face_roots:
            schema.ensure(path)

            def flag(conn):
                for root in face_roots:
                    store_taxonomy.add_path(conn, root, root_has_face=1)
            db.write_with_connection(path, flag)
        tax = TagTaxonomy(path)
        tax.load()
        for p in paths:
            tax.add_tag(p)
        return tax


class TestPeopleEnterUnderAPeopleRoot(TaxonomyTestBase):
    def test_a_new_person_is_filed_under_people(self):
        tax = self.taxonomy()
        tax.add_people(["Josephine Sandoval"])
        self.assertIn("People/Josephine Sandoval", tax.paths)

    def test_and_not_as_a_root_of_their_own(self):
        tax = self.taxonomy()
        tax.add_people(["Josephine Sandoval"])
        self.assertNotIn("Josephine Sandoval", tax.paths)

    def test_a_person_already_in_the_taxonomy_is_left_where_they_are(self):
        # The point is to avoid a second home, not to move the first one.
        tax = self.taxonomy(["Family/Cora Ingersoll"], face_roots=["Family"])
        tax.add_people(["Cora Ingersoll"])
        self.assertIn("Family/Cora Ingersoll", tax.paths)
        self.assertNotIn("People/Cora Ingersoll", tax.paths)
        self.assertNotIn("Cora Ingersoll", tax.paths)

    def test_a_library_using_family_does_not_grow_a_people_beside_it(self):
        tax = self.taxonomy(["Family/Someone Else"], face_roots=["Family"])
        tax.add_people(["Josephine Sandoval"])
        self.assertIn("Family/Josephine Sandoval", tax.paths)
        self.assertNotIn("People/Josephine Sandoval", tax.paths)

    def test_a_name_that_arrives_with_a_path_keeps_it(self):
        tax = self.taxonomy()
        tax.add_people(["Friends/Ada Lovelace"])
        self.assertIn("Friends/Ada Lovelace", tax.paths)

    def test_blank_names_are_ignored(self):
        tax = self.taxonomy()
        tax.add_people(["", "   ", None or ""])
        self.assertEqual(tax.paths, set())

    def test_repeating_an_index_run_adds_nothing_new(self):
        # This is the property that was missing: it ran every time, so every cleanup
        # was temporary.
        tax = self.taxonomy()
        tax.add_people(["Josephine Sandoval"])
        before = set(tax.paths)
        for _ in range(3):
            tax.add_people(["Josephine Sandoval"])
        self.assertEqual(tax.paths, before)


class TestABareNameIsNotASecondHome(TaxonomyTestBase):
    """Photo keywords carry both forms in the wild, and one of them must not stick."""

    def test_a_bare_keyword_for_a_known_person_is_folded(self):
        tax = self.taxonomy(["People/Cora Ingersoll"])
        tax.add_tag("Cora Ingersoll")
        self.assertNotIn("Cora Ingersoll", tax.paths)

    def test_the_person_is_still_there_under_their_path(self):
        tax = self.taxonomy(["People/Cora Ingersoll"])
        tax.add_tag("Cora Ingersoll")
        self.assertIn("People/Cora Ingersoll", tax.paths)

    def test_case_does_not_let_one_slip_through(self):
        tax = self.taxonomy(["People/Cora Ingersoll"])
        tax.add_tag("cora ingersoll")
        self.assertNotIn("cora ingersoll", tax.paths)

    def test_a_bare_name_nobody_claims_is_still_added(self):
        # Not every bare tag is a person; folding them all would lose real tags.
        tax = self.taxonomy(["People/Cora Ingersoll"])
        tax.add_tag("Sunset")
        self.assertIn("Sunset", tax.paths)

    def test_a_non_person_path_does_not_claim_a_bare_tag(self):
        # "Kentridge" beside "School/Kentridge" is a different question -- tidying
        # that for every tag is not what a person-tag fix should do quietly.
        tax = self.taxonomy(["School/Kentridge"])
        tax.add_tag("Kentridge")
        self.assertIn("Kentridge", tax.paths)

    def test_a_deeper_people_path_still_claims_the_name(self):
        tax = self.taxonomy(["People/Team/Cora Ingersoll"])
        tax.add_tag("Cora Ingersoll")
        self.assertNotIn("Cora Ingersoll", tax.paths)


class TestTheIndexerUsesIt(unittest.TestCase):
    def test_indexing_records_people_through_add_people(self):
        """The call site is the whole fix; a test on the method alone would pass
        while the indexer kept minting roots."""
        with open(os.path.join(WORKSPACE_DIR, "tagpup_cli.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn('taxonomy.add_people(meta["people"])', source)
        self.assertNotIn('taxonomy.add_tags(meta["people"])', source)



class TestNewPeopleKeepTheLibrarysDepth(TaxonomyTestBase):
    """A library whose people live three levels deep must not gain a shallower home.

    The main gallery files people at Family/Immediate/<name> -- 4,830 three-level tags
    and 58 at four. Filing somebody new at Family/<name> would put a second, shallower
    home for people beside the real one, which is the bare-root fault wearing different
    clothes.
    """

    def test_a_new_person_joins_the_existing_parent(self):
        tax = self.taxonomy([
            "Family/Immediate/Cora Ingersoll",
            "Family/Immediate/Delphine Ingersoll",
        ], face_roots=["Family"])
        tax.add_people(["Marisol Ingersoll"])
        self.assertIn("Family/Immediate/Marisol Ingersoll", tax.paths)
        self.assertNotIn("Family/Marisol Ingersoll", tax.paths)

    def test_the_commonest_parent_wins(self):
        tax = self.taxonomy([
            "Family/Immediate/Cora Ingersoll",
            "Family/Immediate/Delphine Ingersoll",
            "Family/Extended/Someone Else",
        ], face_roots=["Family"])
        tax.add_people(["Marisol Ingersoll"])
        self.assertIn("Family/Immediate/Marisol Ingersoll", tax.paths)

    def test_a_flat_library_still_files_people_at_the_root(self):
        tax = self.taxonomy(["People/Ada Lovelace"])
        tax.add_people(["Grace Hopper"])
        self.assertIn("People/Grace Hopper", tax.paths)

    def test_an_empty_library_falls_back_to_the_people_root(self):
        tax = self.taxonomy()
        tax.add_people(["Ada Lovelace"])
        self.assertIn("People/Ada Lovelace", tax.paths)

    def test_non_people_depth_does_not_decide_where_people_go(self):
        # Activity/Sport/Running is three deep, but it says nothing about people.
        tax = self.taxonomy(["Activity/Sport/Running", "People/Ada Lovelace"])
        tax.add_people(["Grace Hopper"])
        self.assertIn("People/Grace Hopper", tax.paths)


class TestKeywordsKeepTheirLevels(unittest.TestCase):
    """The levels are the keyword. Its fragments are not keywords."""

    def fields(self, tags):
        from tagpup.core.fields import expand_tag_fields
        return expand_tag_fields(tags)

    def test_a_deep_tag_is_written_whole(self):
        flat, hierarchical = self.fields(["Family/Immediate/Cora Ingersoll"])
        self.assertEqual(flat, ["Family/Immediate/Cora Ingersoll"])
        self.assertEqual(hierarchical, ["Family/Immediate/Cora Ingersoll"])

    def test_its_pieces_are_not_written_beside_it(self):
        flat, _ = self.fields(["Family/Immediate/Cora Ingersoll"])
        for fragment in ("Family", "Immediate", "Cora Ingersoll"):
            self.assertNotIn(fragment, flat)

    def test_a_tag_with_no_levels_is_left_as_it_is(self):
        flat, hierarchical = self.fields(["Sunset"])
        self.assertEqual(flat, ["Sunset"])
        self.assertEqual(hierarchical, [])

    def test_order_is_kept_so_a_rewrite_does_not_churn_the_file(self):
        tags = ["Family/Immediate/Cora Ingersoll", "Activity/Running", "Sunset"]
        flat, _ = self.fields(tags)
        self.assertEqual(flat, tags)

    def test_a_repeated_tag_is_written_once(self):
        flat, hierarchical = self.fields(["Activity/Running", "Activity/Running"])
        self.assertEqual(flat, ["Activity/Running"])
        self.assertEqual(hierarchical, ["Activity/Running"])

if __name__ == "__main__":
    unittest.main()

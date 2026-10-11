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

What is left here: a bare keyword a person's path already claims is not added as a node of its own
(TagTaxonomy.add_tag). The people a photo names get their nodes where the photo is recorded
(tests/test_every_tag_has_an_id.py); TagTaxonomy.add_people, which chose People/<name> by the depth the
library used, went with the owner's decision of 2026-10-10.
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

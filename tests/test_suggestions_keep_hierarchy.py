"""A suggested tag is proposed by the path it is filed under.

A keyword's hierarchy is most of its worth: `Activity/Cross Country` says where a photo
belongs in a way `Cross Country` does not. Zero-shot candidates arrive as bare words,
so a photo already tagged `Activity/Cross Country` was offered `Cross Country` as
though it were something new. The suggestion could not match what was there, and taking
it would have added a second, flatter copy of a tag the photo already had.

People are deliberately not handled here. They are resolved at the write boundary,
against the people roots specifically, and that path works.

The line this must not cross: a bare tag the taxonomy does not know stays bare. Twice
in this project a repair could have quietly promoted `Cross Country` to
`Activity/Cross Country` across a library, and twice that was the wrong thing to do
without being asked.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.services.suggester import TagSuggester
from taxonomy import TagTaxonomy


class FakeTaxonomy(TagTaxonomy):
    def __init__(self, paths):
        self.paths = set(paths)
        self.db_path = "unused.db"
        self.file_path = "unused.json"


def suggester_with(paths):
    suggester = TagSuggester.__new__(TagSuggester)
    suggester.taxonomy = FakeTaxonomy(paths)
    return suggester


def resolve(paths, items):
    return [i["tag"] for i in suggester_with(paths)._with_taxonomy_paths(items)]


class TestFindingTheOnePath(unittest.TestCase):
    def test_a_leaf_the_taxonomy_files_once_resolves(self):
        tax = FakeTaxonomy({"Activity/Cross Country", "School/Kentridge"})
        self.assertEqual(tax.find_by_leaf("Cross Country"), "Activity/Cross Country")

    def test_a_leaf_filed_twice_is_not_guessed_at(self):
        # Filing a photo under the wrong branch puts it where nobody will look.
        tax = FakeTaxonomy({"Trips/Boston MA", "School/Boston MA"})
        self.assertIsNone(tax.find_by_leaf("Boston MA"))

    def test_a_leaf_the_taxonomy_does_not_know(self):
        self.assertIsNone(FakeTaxonomy({"Activity/Cross Country"}).find_by_leaf("Regatta"))

    def test_matching_ignores_case(self):
        tax = FakeTaxonomy({"Activity/Cross Country"})
        self.assertEqual(tax.find_by_leaf("cross country"), "Activity/Cross Country")


class TestProposingByPath(unittest.TestCase):
    PATHS = {"Activity/Cross Country", "School/Kentridge", "Trips/Boston MA"}

    def test_a_bare_suggestion_becomes_its_path(self):
        # The reported case, exactly: the photo carried Activity/Cross Country and was
        # offered "Cross Country" as new.
        self.assertEqual(
            resolve(self.PATHS, [{"tag": "Cross Country", "score": 1.0}]),
            ["Activity/Cross Country"])

    def test_a_suggestion_already_pathed_is_untouched(self):
        self.assertEqual(
            resolve(self.PATHS, [{"tag": "School/Kentridge", "score": 0.9}]),
            ["School/Kentridge"])

    def test_a_tag_the_taxonomy_does_not_know_stays_bare(self):
        # A suggestion for a tag that does not exist yet is still a useful suggestion,
        # and inventing a parent for it is not this code's decision.
        self.assertEqual(
            resolve(self.PATHS, [{"tag": "Regatta", "score": 0.8}]),
            ["Regatta"])

    def test_an_ambiguous_leaf_stays_bare(self):
        ambiguous = {"Trips/Boston MA", "School/Boston MA"}
        self.assertEqual(
            resolve(ambiguous, [{"tag": "Boston MA", "score": 0.8}]),
            ["Boston MA"])

    def test_the_score_is_carried_across(self):
        out = suggester_with(self.PATHS)._with_taxonomy_paths(
            [{"tag": "Cross Country", "score": 0.77, "source_count": 3}])
        self.assertEqual(out[0]["score"], 0.77)
        self.assertEqual(out[0]["source_count"], 3)

    def test_two_suggestions_collapsing_onto_one_path_keep_the_stronger(self):
        # Resolving can merge a bare suggestion into a pathed one already present.
        out = resolve(self.PATHS, [
            {"tag": "Cross Country", "score": 0.5},
            {"tag": "Activity/Cross Country", "score": 0.9},
        ])
        self.assertEqual(out, ["Activity/Cross Country"])

    def test_the_stronger_score_survives_the_collapse(self):
        out = suggester_with(self.PATHS)._with_taxonomy_paths([
            {"tag": "Cross Country", "score": 0.5},
            {"tag": "Activity/Cross Country", "score": 0.9},
        ])
        self.assertEqual(out[0]["score"], 0.9)

    def test_results_come_back_strongest_first(self):
        out = resolve(self.PATHS, [
            {"tag": "Kentridge", "score": 0.4},
            {"tag": "Cross Country", "score": 0.95},
        ])
        self.assertEqual(out, ["Activity/Cross Country", "School/Kentridge"])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(resolve(self.PATHS, []), [])

    def test_an_item_with_no_tag_is_skipped(self):
        self.assertEqual(resolve(self.PATHS, [{"score": 0.9}]), [])


class TestItDoesNotRewritePhotos(unittest.TestCase):
    """This changes what is *proposed*, never what a photo already holds.

    A photo tagged with a flat `Cross Country` keeps it. Promoting flat keywords across
    a library is a separate, deliberate operation -- it was declined twice here for
    good reason -- and a suggestion resolver is not the place to start doing it."""

    def test_resolution_touches_suggestions_only(self):
        source = os.path.join(WORKSPACE_DIR, "tagpup", "services", "suggester.py")
        with open(source, encoding="utf-8") as f:
            text = f.read()
        start = text.index("def _with_taxonomy_paths")
        end = text.index("\n    def ", start + 10)
        body = text[start:end]

        for writing in ("write_keyword_fields", "UPDATE photos", "set_tags"):
            self.assertNotIn(writing, body,
                             "the suggestion resolver is writing to photos")


if __name__ == "__main__":
    unittest.main()

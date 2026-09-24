"""tagpup.core.vocabulary: how a tag is spelled and taken apart -- read one way.

Python took tags apart by hand in 55 places, each slightly differently: some trimmed
the segments, some dropped empty ones, a few read "|" as a separator and most did not.
One tag could name a person to the code that filed it and nobody to the code that
looked it up.

So this checks the rules, and fails the build on a raw `.split("/")` -- or rsplit,
partition, rpartition -- anywhere outside vocabulary.py. A line splitting something
that is not a tag can say so with a `# not a tag: <why>` comment.
"""
import json
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import vocabulary  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402


class TakingATagApart(unittest.TestCase):
    def test_segments_are_trimmed_and_empty_ones_dropped(self):
        self.assertEqual(vocabulary.segments(" Family / Immediate//Rowan Thackeray "),
                         ["Family", "Immediate", "Rowan Thackeray"])
        self.assertEqual(vocabulary.normalize(" Family / Immediate//Rowan Thackeray "),
                         "Family/Immediate/Rowan Thackeray")

    def test_the_separators_other_tools_write_read_as_the_one(self):
        self.assertEqual(vocabulary.normalize("People|Rowan Thackeray"), "People/Rowan Thackeray")
        self.assertEqual(vocabulary.normalize("People\\Rowan Thackeray"), "People/Rowan Thackeray")

    def test_nothing_has_no_segments(self):
        for empty in (None, "", "  ", "/", " | "):
            self.assertEqual(vocabulary.segments(empty), [], repr(empty))
            self.assertEqual(vocabulary.normalize(empty), "")
            self.assertEqual(vocabulary.leaf_of(empty), "")
            self.assertEqual(vocabulary.root_of(empty), "")
            self.assertEqual(vocabulary.parent_of(empty), "")
            self.assertEqual(vocabulary.lineage(empty), [])

    def test_leaf_root_and_parent(self):
        tag = "Family/Immediate/Rowan Thackeray"
        self.assertEqual(vocabulary.leaf_of(tag), "Rowan Thackeray")
        self.assertEqual(vocabulary.root_of(tag), "Family")
        self.assertEqual(vocabulary.parent_of(tag), "Family/Immediate")
        self.assertEqual(vocabulary.parent_of("People"), "")

    def test_a_bare_name_is_its_own_leaf_and_root(self):
        self.assertEqual(vocabulary.leaf_of("Rowan Thackeray"), "Rowan Thackeray")
        self.assertEqual(vocabulary.root_of("Rowan Thackeray"), "Rowan Thackeray")

    def test_the_lineage_runs_from_the_root_to_the_tag(self):
        self.assertEqual(vocabulary.lineage("A/B/C"), ["A", "A/B", "A/B/C"])
        self.assertEqual(vocabulary.lineage(" A | B "), ["A", "A/B"])

    def test_a_new_leaf_keeps_the_path_above_it(self):
        self.assertEqual(vocabulary.with_leaf("People/Rowan", " Rowan Thackeray "),
                         "People/Rowan Thackeray")
        self.assertEqual(vocabulary.with_leaf("Rowan", "Rowan Thackeray"), "Rowan Thackeray")


class ComparingTags(unittest.TestCase):
    def test_one_person_under_any_path_and_in_any_case(self):
        self.assertTrue(vocabulary.same_person("People/Rowan Thackeray", "rowan thackeray "))
        self.assertTrue(vocabulary.same_person("Family/Rowan Thackeray", "People/Rowan Thackeray"))
        self.assertFalse(vocabulary.same_person("People/Rowan Thackeray", "People/Rowan"))
        self.assertFalse(vocabulary.same_person("", ""))

    def test_a_key_ignores_case_and_the_space_around_it(self):
        self.assertEqual(vocabulary.key("  Rowan Thackeray "), "rowan thackeray")
        self.assertEqual(vocabulary.key(None), "")

    def test_a_hidden_branch_hides_everything_under_it(self):
        hidden = {"Internal"}
        self.assertTrue(vocabulary.hidden_by("Internal", hidden))
        self.assertTrue(vocabulary.hidden_by("Internal/Secret", hidden))
        self.assertTrue(vocabulary.hidden_by(" Internal | Secret ", hidden))
        self.assertFalse(vocabulary.hidden_by("Internals/Secret", hidden))
        self.assertFalse(vocabulary.hidden_by("Public/Internal", hidden))
        self.assertFalse(vocabulary.hidden_by("", hidden))


class WhatAPhotosMetadataSays(unittest.TestCase):
    """The rules alone: no file, no library, milliseconds."""

    def test_a_flat_keyword_that_is_a_level_of_a_path_is_not_a_tag(self):
        meta = {"XMP:Subject": ["Family", "Beach", "Family/Immediate/Cora Ingersoll"],
                "Subject": ["Family", "Beach", "Family/Immediate/Cora Ingersoll"],
                "XMP:HierarchicalSubject": "Family/Immediate/Cora Ingersoll"}
        self.assertEqual(vocabulary.extract_tags(meta), ["Beach", "Family/Immediate/Cora Ingersoll"])

    def test_a_caption_written_to_several_fields_is_one_caption(self):
        meta = {"XMP:Description": "Harbour at dusk", "Description": "Harbour at dusk",
                "XMP:Title": ["Harbour at dusk", "Boats"]}
        self.assertEqual(vocabulary.extract_captions(meta), ["Harbour at dusk", "Boats"])

    def test_without_a_library_the_usual_roots_name_people(self):
        tags = ["People/Rowan Thackeray", "Pets/Biscuit", "Hazel Brookmire"]
        self.assertEqual(vocabulary.extract_people({"XMP:PersonInImage": "Elias Marchetti-Oakes"}, tags),
                         ["Elias Marchetti-Oakes", "Rowan Thackeray"])

    def test_a_library_adds_its_own_roots_and_its_flat_people(self):
        known = vocabulary.PeopleVocabulary.from_rows(
            ["Pets", "Crew"],
            [("Pets", "Pets"), ("Pets/Biscuit", "Biscuit"), ("Crew", "Crew"),
             ("Hazel Brookmire", "Hazel Brookmire")])
        tags = ["Pets/Biscuit", "Crew/Tamsin Oakes", "Hazel Brookmire", "Crew"]
        self.assertEqual(vocabulary.extract_people({}, tags, known),
                         ["Biscuit", "Tamsin Oakes", "Hazel Brookmire"])

    def test_a_named_face_counts_once_whatever_its_case(self):
        people = vocabulary.people_in_photo({}, ["People/Rowan Thackeray"],
                                            ["rowan thackeray", "Hazel Brookmire"])
        self.assertEqual(people, ["Rowan Thackeray", "Hazel Brookmire"])


RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_rules.json")


class WhatMayBeSet(unittest.TestCase):
    """The cases the pages are held to as well (tests/frontend/tag-rules.test.mjs)."""

    @classmethod
    def setUpClass(cls):
        with open(RULES, encoding="utf-8") as handle:
            cls.rules = json.load(handle)

    def test_tags(self):
        for text, expected in self.rules["tags"]:
            with self.subTest(tag=text):
                self.assertEqual(vocabulary.problem_with_tag(text), expected)

    def test_names(self):
        for text, expected in self.rules["names"]:
            with self.subTest(name=text):
                self.assertEqual(vocabulary.problem_with_name(text), expected)

    def test_an_allowed_tag_is_set_in_its_one_spelling(self):
        for typed, stored in self.rules["spelled"]:
            with self.subTest(tag=typed):
                self.assertIsNone(vocabulary.problem_with_tag(typed))
                self.assertEqual(vocabulary.normalize(typed), stored)

    def test_the_cases_cover_every_refusal(self):
        # Each message is one branch; a branch no case reaches is one the pages were
        # never checked against.
        said = {expected for _, expected in self.rules["tags"] + self.rules["names"] if expected}
        for kind in ("empty", "control character", '"|"', '"\\"', "empty level", 'contain "/"'):
            self.assertTrue(any(kind in message for message in said), kind)

    def test_nothing_is_nothing(self):
        self.assertEqual(vocabulary.problem_with_tag(None), "A tag cannot be empty.")
        self.assertEqual(vocabulary.problem_with_name(None), "A name cannot be empty.")


OWNER = os.path.join("tagpup", "core", "vocabulary.py")
MARKER = "# not a tag:"
RAW_SPLIT = re.compile(r"""\.(r?split|r?partition)\(\s*(['"])/\2""")


class TagsHaveOneReading(unittest.TestCase):
    def test_nothing_else_takes_a_tag_apart_by_hand(self):
        problems = []
        for relative in python_sources():
            if relative == OWNER:
                continue
            full = os.path.join(ROOT, relative)
            if not os.path.exists(full):
                continue
            with open(full, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if MARKER in line or line.lstrip().startswith("#"):
                        continue
                    if RAW_SPLIT.search(line):
                        problems.append("%s:%d\n    %s" % (relative, number, line.strip()))
        self.assertEqual(problems, [], "\n\nTags are taken apart by tagpup/core/vocabulary.py "
                         "only (segments / leaf_of / root_of / parent_of / lineage / with_leaf):"
                         "\n\n" + "\n".join(problems))

    def test_the_guard_recognises_what_it_forbids(self):
        # A guard whose pattern quietly matches nothing passes forever.
        samples = {
            'leaf = tag.split("/")[-1]': True,
            "root = tag.split('/', 1)[0]": True,
            'parent = tag.rsplit("/", 1)[0]': True,
            'head, _, rest = tag.partition("/")': True,
            "parts = vocabulary.segments(tag)": False,
            'words = caption.split(" ")': False,
        }
        for sample, expected in samples.items():
            self.assertEqual(bool(RAW_SPLIT.search(sample)), expected, sample)


if __name__ == "__main__":
    unittest.main()

"""The repair that gives a person back the path their keywords should carry.

This script rewrites photo files, so what it decides has to be pinned down before it
is pointed at a library. The cases below are taken from real rows: a bare name with no
path at all, a bare name beside its own path, and the debris the old segment-writing
habit left behind ("People" standing alone next to "People/Elias Marchetti-Oakes").

The case that matters most is the last one: a flat keyword that is not a person must
come through untouched. A first pass over this library would have rewritten "Cross
Country" to "Activity/Cross Country" -- defensible tidying, but not what this script
is named after, and not something it should do quietly.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from repair_bare_person_tags import repair_tags

PATHS = {
    "hazel brookmire": "People/Hazel Brookmire",
    "jasper keel": "People/Jasper Keel",
    "elias marchetti-oakes": "People/Elias Marchetti-Oakes",
    "linnea ingersoll": "Family/Immediate/Linnea Ingersoll",
}
ROOTS = {"people", "family", "friends", "pets"}


def repaired(tags):
    return repair_tags(tags, PATHS, ROOTS)[0]


class TestAPersonGetsTheirPath(unittest.TestCase):
    def test_a_bare_name_becomes_the_path_it_is_filed_under(self):
        self.assertEqual(repaired(["Hazel Brookmire"]), ["People/Hazel Brookmire"])

    def test_a_person_filed_deeper_keeps_every_level(self):
        self.assertEqual(
            repaired(["Linnea Ingersoll"]), ["Family/Immediate/Linnea Ingersoll"]
        )

    def test_a_name_already_pathed_is_left_alone(self):
        tags = ["People/Hazel Brookmire", "Cross Country"]
        self.assertEqual(repaired(tags), tags)

    def test_a_bare_name_beside_its_own_path_is_dropped_not_doubled(self):
        # The screenshot that started this: four People/... pills and a lone
        # "Hazel Brookmire" beside them.
        self.assertEqual(
            repaired(["People/Hazel Brookmire", "Hazel Brookmire"]),
            ["People/Hazel Brookmire"],
        )

    def test_the_real_row_from_the_library(self):
        # 2Z6A4689.jpg, exactly as exiftool read it.
        self.assertEqual(
            repaired([
                "People/Elias Marchetti-Oakes", "Jasper Keel",
                "Elias Marchetti-Oakes", "People",
            ]),
            ["People/Elias Marchetti-Oakes", "People/Jasper Keel"],
        )


class TestEverythingElseIsLeftAlone(unittest.TestCase):
    def test_a_flat_keyword_that_is_not_a_person_survives(self):
        tags = ["Cross Country", "Kentridge", "Graduation"]
        self.assertEqual(repaired(tags), tags)

    def test_a_person_mixed_with_flat_keywords_leaves_them_in_place(self):
        self.assertEqual(
            repaired(["Graduation", "Kentridge", "Linnea Ingersoll"]),
            ["Graduation", "Kentridge", "Family/Immediate/Linnea Ingersoll"],
        )

    def test_order_is_preserved(self):
        self.assertEqual(
            repaired(["Linnea Ingersoll", "Kentridge", "Graduation"]),
            ["Family/Immediate/Linnea Ingersoll", "Kentridge", "Graduation"],
        )

    def test_a_root_with_nothing_under_it_on_this_photo_is_kept(self):
        # "People" alone is only debris when the photo also files someone under it.
        # On its own it may be a deliberate tag, and guessing is not this script's job.
        self.assertEqual(repaired(["People", "Cross Country"]), ["People", "Cross Country"])

    def test_nothing_to_do_reports_nothing_to_do(self):
        _tags, replaced = repair_tags(["Cross Country"], PATHS, ROOTS)
        self.assertEqual(replaced, [])

    def test_an_empty_keyword_set_is_not_a_crash(self):
        self.assertEqual(repaired([]), [])

    def test_case_does_not_decide_whether_someone_is_recognised(self):
        self.assertEqual(repaired(["hazel brookmire"]), ["People/Hazel Brookmire"])


class TestItDoesNotInventPeople(unittest.TestCase):
    def test_a_name_not_in_the_taxonomy_is_not_given_a_path(self):
        self.assertEqual(repaired(["Someone Unknown"]), ["Someone Unknown"])

    def test_a_duplicate_path_is_collapsed(self):
        self.assertEqual(
            repaired(["People/Jasper Keel", "People/Jasper Keel"]),
            ["People/Jasper Keel"],
        )


if __name__ == "__main__":
    unittest.main()

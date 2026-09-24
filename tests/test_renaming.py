"""A Smart Rename grouping may not hold " - ", which separates the parts of a name.

Editing a photo's caption renames it by splitting its name on " - " and reading the
second part as the photo's number. A grouping holding one ("2019-06 - Summer Camp")
was read as the number and the real one dropped: docs/findings.md #29. Renaming moves
forward, so the fix is at the grouping: names already made that way are put right
the next time Smart Rename runs on them.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import renaming  # noqa: E402
from handler_harness import Library  # noqa: E402

RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_rules.json")


class WhatAGroupingMayHold(unittest.TestCase):
    def test_the_cases_the_page_is_held_to(self):
        with open(RULES, encoding="utf-8") as handle:
            cases = json.load(handle)["groupings"]
        for grouping, expected in cases:
            with self.subTest(grouping=grouping):
                self.assertEqual(renaming.problem_with_grouping(grouping), expected)


class SmartRenameRefusesIt(unittest.TestCase):
    def test_nothing_is_renamed(self):
        lib = Library(self)
        photo = os.path.join(lib.photos, "IMG_0007.jpg")
        with open(photo, "wb") as handle:
            handle.write(b"not really a photo")
        handler = lib.handler("exiftool")
        status, reply = handler.call("handle_post_folder_rename_photos", {
            "folder_path": lib.photos, "photo_paths": [photo],
            "grouping": "2019-06 - Summer Camp"})

        self.assertEqual(status, 400)
        self.assertIn('" - "', reply["error"])
        self.assertEqual(os.listdir(lib.photos), ["IMG_0007.jpg"])


if __name__ == "__main__":
    unittest.main()

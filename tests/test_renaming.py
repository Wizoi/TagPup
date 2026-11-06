"""A Smart Rename grouping may not hold " - ", which separates the parts of a name.

Editing a photo's caption renames it, reading its grouping and number back out of its
name. A split at " - " read the second part of a grouping holding one ("2019-06 - Summer
Camp") as the number and dropped the real one: docs/findings.md #29. New groupings are
refused one; names made before that are read by renaming.parts_of, which keeps the
grouping whole and the number.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import validation  # noqa: E402
from tagpup.files import metadata  # noqa: E402
from handler_harness import Library  # noqa: E402

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_cases.json")


class WhatAGroupingMayHold(unittest.TestCase):
    def test_the_cases_the_page_is_held_to(self):
        with open(CASES, encoding="utf-8") as handle:
            cases = [case for case in json.load(handle)["cases"] if case[0] == "grouping"]
        self.assertTrue(cases)
        for _kind, grouping, expected in cases:
            with self.subTest(grouping=grouping):
                self.assertEqual(validation.problem("grouping", grouping), expected)


class SmartRenameRefusesIt(unittest.TestCase):
    def test_nothing_is_renamed(self):
        lib = Library(self)
        photo = os.path.join(lib.photos, "IMG_0007.jpg")
        with open(photo, "wb") as handle:
            handle.write(b"not really a photo")
        status, reply = lib.post("/api/folder/rename-photos", {
            "folder_path": lib.photos, "photo_paths": [photo],
            "grouping": "2019-06 - Summer Camp"})

        self.assertEqual(status, 400)
        self.assertIn('" - "', reply["error"])
        self.assertEqual(os.listdir(lib.photos), ["IMG_0007.jpg"])


class ACaptionEditOnANameMadeBeforeTheRule(unittest.TestCase):
    """The owner edits the caption of a photo Smart Rename named "2019-06 - Summer Camp - 03 - Start" before a
    grouping could not hold " - ": its new name keeps the whole grouping and its number."""

    FORMAT = "{grouping} - {index} - {caption}"

    def _photo(self, name):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"not really a photo")
        return folder, path

    def test_the_grouping_and_number_are_kept(self):
        folder, photo = self._photo("2019-06 - Summer Camp - 03 - Start.jpg")
        renamed = metadata.title_filename(photo, "Finish Line", self.FORMAT, "IMG_0412.jpg")
        self.assertEqual(renamed, os.path.join(folder, "2019-06 - Summer Camp - 03 - Finish Line.jpg"))

    def test_a_name_the_format_did_not_make_is_left(self):
        _folder, photo = self._photo("Beach - Sunset.jpg")
        self.assertEqual(metadata.title_filename(photo, "Dusk", self.FORMAT, "IMG_0412.jpg"), photo)


if __name__ == "__main__":
    unittest.main()

"""tagpup.core.dates: when a photo was taken, and the year of it -- read one way.

Seven places read it: three year parsers and four sort keys, with three lists of
fields between them, one of which counted ModifyDate -- the date the file was last
edited, not the date the photo was taken.
"""
import ast
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.core import dates  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402


class WhenItWasTaken(unittest.TestCase):
    def test_the_original_date_comes_first(self):
        raw = {"EXIF:CreateDate": "2019:01:01 00:00:00",
               "EXIF:DateTimeOriginal": "2018:07:04 09:30:00"}
        self.assertEqual(dates.date_taken(raw), "2018:07:04 09:30:00")
        self.assertEqual(dates.year_taken(raw), 2018)

    def test_the_xmp_creation_date_counts(self):
        self.assertEqual(dates.year_taken({"XMP:CreateDate": "2012:05:06"}), 2012)

    def test_a_modification_date_is_not_when_it_was_taken(self):
        raw = {"EXIF:ModifyDate": "2021:03:03 10:00:00"}
        self.assertIsNone(dates.date_taken(raw))
        self.assertIsNone(dates.year_taken(raw))

    def test_a_list_value_is_its_first_entry(self):
        self.assertEqual(dates.year_taken({"DateTimeOriginal": ["2009:01:01", "2010:01:01"]}), 2009)

    def test_a_field_without_a_year_is_passed_over_for_the_next(self):
        raw = {"EXIF:DateTimeOriginal": "0000:00:00 00:00:00", "EXIF:CreateDate": "2016:02:02"}
        self.assertEqual(dates.year_taken(raw), 2016)

    def test_nothing_is_none(self):
        self.assertIsNone(dates.date_taken(None))
        self.assertIsNone(dates.year_taken({}))


class TheYearFromItsName(unittest.TestCase):
    def test_the_file_name_first_then_the_nearest_folder(self):
        self.assertEqual(dates.year_in_name(r"D:\Pictures\2008\2004 Trip\beach_2003.jpg"), 2003)
        self.assertEqual(dates.year_in_name(r"D:\Pictures\2008\2004 Trip\beach.jpg"), 2004)
        self.assertEqual(dates.year_in_name("D:/Pictures/2008/Trip/beach.jpg"), 2008)

    def test_a_counter_is_not_a_year(self):
        self.assertIsNone(dates.year_in_name(r"D:\Pictures\Camera\IMG_0001.jpg"))
        self.assertIsNone(dates.year_in_name(None))

    def test_the_metadata_wins_over_the_name(self):
        self.assertEqual(dates.photo_year({"DateTimeOriginal": "2011:01:01"}, r"D:\2008\a.jpg"), 2011)
        self.assertEqual(dates.photo_year({}, r"D:\2008\a.jpg"), 2008)


class TheSortKey(unittest.TestCase):
    def test_it_is_the_string_the_folder_view_always_sorted_by(self):
        # Kept exactly, so nothing moves: the date as ExifTool gives it, or the file
        # time behind "mtime_", which sorts after every date.
        self.assertEqual(dates.date_taken_sort_key({"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"}, 5.0),
                         "2026:06:27 12:00:00")
        self.assertEqual(dates.date_taken_sort_key({}, 1234.5), "mtime_1234.5")
        self.assertLess(dates.date_taken_sort_key({"CreateDate": "2099:12:31"}),
                        dates.date_taken_sort_key({}, 1.0))


def date_rule_lists(source):
    """List and tuple literals that are a list of when-was-it-taken fields."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            names = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if (len(names) == len(node.elts)
                    and all(n.endswith(("DateTimeOriginal", "CreateDate", "ModifyDate")) for n in names)
                    and any(n.endswith("DateTimeOriginal") for n in names)):
                yield node.lineno


class OneReadingOfTheDate(unittest.TestCase):
    OWNER = os.path.join("tagpup", "core", "dates.py")

    def test_no_other_module_lists_the_date_fields(self):
        problems = []
        for relative in python_sources():
            if relative == self.OWNER:
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                problems += ["%s:%d" % (relative, line) for line in date_rule_lists(handle.read())]
        self.assertEqual(problems, [], "read the date through tagpup.core.dates: " + ", ".join(problems))

    def test_the_pages_read_no_date_field_of_their_own(self):
        # The TagPup page kept a list that took the date a file was modified for the
        # date it was taken (#67); the pages read `taken` as the server gives it.
        found = []
        for page in (os.path.join("gui_tagpup", "app.js"), os.path.join("gui", "app.js")):
            with open(os.path.join(ROOT, page), encoding="utf-8") as handle:
                found += ["%s: %s" % (page, name) for name in
                          sorted(set(re.findall(r"\b\w*(?:DateTimeOriginal|CreateDate|ModifyDate)\b", handle.read())))]
        self.assertEqual([], found)

    def test_the_page_is_given_when_each_photo_was_taken(self):
        from metadata import build_photo_ui_record
        record = build_photo_ui_record("D:/2019/a.jpg", {"raw_metadata": {
            "EXIF:ModifyDate": "2024:01:01 00:00:00", "XMP:CreateDate": "2019:05:04 10:00:00"}})
        self.assertEqual("2019:05:04 10:00:00", record["taken"])
        self.assertIsNone(build_photo_ui_record("D:/a.jpg", {"raw_metadata": {
            "EXIF:ModifyDate": "2024:01:01 00:00:00"}})["taken"])

    def test_the_guard_recognises_what_it_forbids(self):
        self.assertTrue(list(date_rule_lists(
            'for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "EXIF:CreateDate"]: pass')))
        self.assertFalse(list(date_rule_lists(
            'FIELDS = ["XMP:Subject", "EXIF:DateTimeOriginal", "EXIF:Model"]')))
        with open(os.path.join(ROOT, self.OWNER), encoding="utf-8") as handle:
            self.assertTrue(list(date_rule_lists(handle.read())), "the owner stopped matching")


if __name__ == "__main__":
    unittest.main()

"""Smart Rename and Time Shift refuse a request holding a photo found damaged, and move and
write nothing (docs/findings.md, #407).

Skipping the damaged photo in a rename kept its old name while the numbering gave that
name to another photo: the damaged file was moved aside to "_conflict_1" and its record
lost. A tag write can skip a photo; a rename or a shift of a folder cannot, without
leaving it in the way of the rest. The reviewer's case: three photos numbered 1 to 3, the
second a possibly incomplete copy.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damaged_photos  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core.result import DAMAGED_PHOTOS, Result  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import photos  # noqa: E402


class Case(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.lib.hold(self.lib.photos)
        self.named = [os.path.join(self.lib.photos, "Regatta - %d.jpg" % n) for n in (1, 2, 3)]
        first, second, third = self.named
        damaged_photos.whole_jpeg(first)
        damaged_photos.second_half_zeros(second)
        damaged_photos.whole_jpeg(third, seed=3)
        for path in self.named:
            self.lib.add_row(path)
        stat = os.stat(second)
        damaged.remember(self.lib.library, [(second, (stat.st_mtime, stat.st_size), damaged.INCOMPLETE,
                                             "the last 1000 bytes are zeros", 1000)])
        self.before = sorted(os.listdir(self.lib.photos))

    def untouched(self, result):
        self.assertTrue(result.refused, "the request was not refused")
        self.assertIn("Regatta - 2.jpg", result.refused)
        self.assertEqual([self.named[1]], result.details[DAMAGED_PHOTOS])
        self.assertEqual(self.before, sorted(os.listdir(self.lib.photos)), "a file was moved")
        self.assertEqual(1, len(damaged.records(self.lib.library)), "the damaged photo's record was lost")


class ARename(Case):
    def test_is_refused_whole(self):
        with mock.patch("tagpup.files.names.read_for_renaming",
                        side_effect=lambda exiftool, present: {path: "" for path in present}), \
                mock.patch.object(photos, "preserve_names", return_value=Result()):
            result = photos.smart_rename(self.lib.library, self.named, "Regatta", "{grouping} - {index} - {caption}",
                                         "exiftool")
        self.untouched(result)


class ATimeShift(Case):
    def test_is_refused_whole(self):
        with mock.patch("tagpup.services.file_changes.write_fields",
                        side_effect=AssertionError("a file was written")):
            result = photos.shift_date_taken(self.lib.library, self.named, 30, "exiftool")
        self.untouched(result)


if __name__ == "__main__":
    unittest.main()

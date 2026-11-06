"""A patch in place that makes the file longer (a clip's XMP box appended at its end, or grown where it ends;
tagpup.files.in_place, record v2; DECISIONS 2026-11-05), its record, and what the file holds against it at each point a
crash can leave it (scenario D3).

The file is made byte by byte, no clip and no library: the record's rules are the bytes' alone. The record says the size
before and after and keeps the old bytes of the ranges inside the old size, nothing else read or written.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tagpup.files import in_place  # noqa: E402

#: The file as it was: 1,024 bytes, each position's byte its own.
OLD = bytes(range(256)) * 4
#: A box appended at the end, 40 bytes.
BOX = b"\x00\x00\x00\x28uuid" + b"B" * 32


class AFileGrownByItsPatch(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "clip.mov")
        with open(self.path, "wb") as handle:
            handle.write(OLD)

    def held(self):
        with open(self.path, "rb") as handle:
            return handle.read()

    def recorded(self, changes, grow=True):
        with open(self.path, "rb") as handle:
            return in_place.record(handle, changes, ("IPTC:Keywords",), grow=grow)

    def test_a_record_of_an_appended_box_says_both_sizes_and_keeps_only_the_old_bytes_inside_the_old_size(self):
        saved, before = self.recorded([(len(OLD), BOX), (100, b"abc")])
        self.assertEqual((2, len(OLD), len(OLD) + len(BOX), [100, len(OLD)], OLD[100:103]),
                         (saved["v"], saved["size"], saved["size_after"], [each["at"] for each in saved["ranges"]],
                          before))
        # A box grown where it ends: its range starts inside the file and runs past the end.
        saved, before = self.recorded([(1000, b"G" * 50)])
        self.assertEqual((1050, OLD[1000:]), (saved["size_after"], before))
        self.assertEqual(OLD, self.held(), "a record writes nothing")

    def test_a_range_past_the_end_is_refused_unless_it_is_the_last_of_a_grown_patch_and_starts_inside(self):
        refused = {
            "not asked to grow": ([(len(OLD), BOX)], False),
            "a gap before the box": ([(len(OLD) + 1, BOX)], True),
            "two ranges past the end": ([(1020, b"x" * 10), (1030, BOX)], True),
            "overlapping": ([(100, b"abc"), (101, b"d")], True),
            "empty": ([(100, b"")], True),
        }
        for why, (changes, grow) in refused.items():
            with self.subTest(why), self.assertRaises(ValueError):
                self.recorded(changes, grow)
        saved, _before = self.recorded([(100, b"abc")], grow=False)
        self.assertEqual((len(OLD), len(OLD)), (saved["size"], saved["size_after"]), "a patch at its old size")


if __name__ == "__main__":
    unittest.main()

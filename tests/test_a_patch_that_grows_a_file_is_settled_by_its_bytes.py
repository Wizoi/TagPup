"""A patch in place that makes the file longer (a clip's XMP box appended at its end, or grown where it ends;
tagpup.files.in_place, record v2; DECISIONS 2026-11-05), its record, and what the file holds against it at each point a
crash can leave it (scenario D3).

The file is made byte by byte, no clip and no library: the record's rules are the bytes' alone. The record says the size
before and after and keeps the old bytes of the ranges inside the old size, nothing else read or written. A crash
part-way leaves the file at either size or between, each byte of a range old or new (TORN, put back and written again);
a size outside the two, or a byte neither old nor new -- an appended run of zeros a power cut left among them -- is
another program's or the disk's, NEITHER, a conflict never overwritten. A put back writes the old bytes first and
cuts the file to its old size last, so a put back cut short is itself TORN or OLD, and settled the same way.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tagpup.files import in_place  # noqa: E402

#: The file as it was: 1,024 bytes, each position's byte its own.
OLD = bytes(range(256)) * 4
#: A box appended at the end, 40 bytes.
BOX = b"\x00\x00\x00\x28uuid" + b"B" * 32


class Killed(OSError):
    """The process killed at a step of a put back: an OSError, which the put back's bounded thread
    (shares.bounded) passes up as it is."""

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

    def holding(self, data):
        with open(self.path, "wb") as handle:
            handle.write(data)

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

    def test_a_grown_file_is_told_new_old_torn_or_neither_by_its_size_and_ranges(self):
        saved, before = self.recorded([(100, b"abc"), (len(OLD), BOX)])
        inside = OLD[:100] + b"abc" + OLD[103:]
        grown = inside + BOX
        cases = {
            "untouched": (OLD, in_place.OLD),
            "every range written": (grown, in_place.NEW),
            "the first range alone": (inside, in_place.TORN),
            "the box half appended": (inside + BOX[:17], in_place.TORN),
            "the box appended, the first range not yet": (OLD + BOX, in_place.TORN),
            "a byte of the first range neither": (OLD[:101] + b"#" + OLD[102:] + BOX, in_place.NEITHER),
            "zeros where the box was to be": (inside + bytes(len(BOX)), in_place.NEITHER),
            "longer than the patch makes it": (grown + b"more", in_place.NEITHER),
            "shorter than it was": (OLD[:-1], in_place.NEITHER),
        }
        for why, (data, expected) in cases.items():
            with self.subTest(why):
                self.holding(data)
                self.assertEqual(expected, in_place.held(self.path, saved, before))


    def test_put_back_returns_a_grown_file_to_its_old_size_and_bytes(self):
        saved, before = self.recorded([(100, b"abc"), (len(OLD), BOX)])
        inside = OLD[:100] + b"abc" + OLD[103:]
        for why, data in {"every range written": inside + BOX, "the box half appended": inside + BOX[:17],
                          "the box appended, the first range not yet": OLD + BOX}.items():
            with self.subTest(why):
                self.holding(data)
                in_place.put_back(self.path, saved, before)
                self.assertEqual(OLD, self.held())

    def test_a_put_back_killed_before_or_after_its_cut_is_torn_or_old_and_put_back_again(self):
        saved, before = self.recorded([(100, b"abc"), (len(OLD), BOX)])
        real = in_place._truncate

        def after_the_cut(handle, size):
            real(handle, size)
            raise Killed("after the cut")
        for why, cut, left in (("before its cut", mock.Mock(side_effect=Killed("before the cut")), in_place.TORN),
                               ("after its cut", after_the_cut, in_place.OLD)):
            with self.subTest(why):
                self.holding(OLD[:100] + b"abc" + OLD[103:] + BOX)
                with mock.patch.object(in_place, "_truncate", cut), self.assertRaises(Killed):
                    in_place.put_back(self.path, saved, before)
                self.assertEqual(left, in_place.held(self.path, saved, before))
                in_place.put_back(self.path, saved, before)
                self.assertEqual(OLD, self.held())


if __name__ == "__main__":
    unittest.main()

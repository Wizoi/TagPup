"""Settling a write a crash stopped compares the file with what a read gives for the
planned after, not with the planned value (docs/findings.md, #277).

IPTC keeps a keyword to 64 bytes and a letter outside its character set as "?". A
crash between the write of such a value and its row left the file holding the value as
IPTC keeps it, and settling, comparing it with the value asked for, called the file a
conflict. Real ExifTool, on JPEGs made here.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.services import file_changes  # noqa: E402

#: 98 bytes, longer than the 64 IPTC:Keywords keeps.
LONG = "Places/" + "Harbour" * 13
#: A letter IPTC's default character set (Latin) does not hold.
L_STROKE = chr(0x141)


class AValueIptcDoesNotKeep(ReadFilesCase):
    def test_is_read_as_iptc_keeps_it(self):
        self.assertEqual([LONG[:64]], fields.as_read("IPTC:Keywords", LONG))
        self.assertEqual([LONG], fields.as_read("XMP:Subject", LONG))
        self.assertTrue(fields.same_read("IPTC:Keywords", ["Places/?odz"], ["Places/" + L_STROKE + "odz"]))
        self.assertTrue(fields.same_read("IPTC:Caption-Abstract", ["x" * 2000], ["x" * 2100]))
        self.assertFalse(fields.same_read("XMP:Description", ["Places/?odz"], ["Places/" + L_STROKE + "odz"]))

    def settles_done(self, tags):
        a = self.make_read("a.jpg")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([a], tags)
        change = self.last_change()
        self.assertEqual(1, self.settle())
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        after = json.loads(self.rows("SELECT fields_after FROM change_files WHERE change_id = ?", (change,))[0][0])
        return a, change, after

    def test_a_long_keyword_written_before_a_crash_settles_done(self):
        a, change, after = self.settles_done([LONG])
        # Recorded as the file holds it, so undoing it finds what the change left.
        self.assertEqual(sorted(["Beach", LONG[:64]]), sorted(after["IPTC:Keywords"]))
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], self.indexed(a))

    def test_a_letter_iptc_cannot_hold_settles_done(self):
        _a, _change, after = self.settles_done(["Places/" + L_STROKE + "odz"])
        self.assertIn("Places/?odz", after["IPTC:Keywords"])


if __name__ == "__main__":
    unittest.main()

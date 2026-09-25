"""A value spelled true or false compares as a read of the file gives it (review of
pass/journal, item 5; the class of docs/findings.md, #264 and #278).

ExifTool answers a value matching /^(true|false)$/i as a JSON boolean, which the journal
keeps as "True" or "False". fields.as_read modelled JSON numbers and not booleans, so a
keyword or caption spelled "true" never compared equal to what the file read back, and
was planned and written again by every edit. Real ExifTool, on JPEGs made here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.files import field_values  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402


class ABoolean(ReadFilesCase):
    def test_is_read_as_exiftool_answers_it(self):
        a = self.make_read("a.jpg")
        written = {"XMP:Description": "true", "XMP:Subject": ["FALSE", "Beach"]}
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values.write(et, a, written)
            held = field_values.read_one(et, a, list(written))
        for field, value in written.items():
            self.assertEqual(sorted(held[field]), sorted(fields.as_read(field, value)), field)
        self.assertTrue(fields.reads_same(held, written))
        self.assertFalse(fields.reads_same({"XMP:Description": ["True"]}, {"XMP:Description": "truly"}))

    def test_a_file_holding_it_is_not_written_again(self):
        a = self.make_read("a.jpg")
        self.assertEqual(1, self.caption([a], "true").changed)
        written = os.stat(a).st_mtime_ns
        again = self.caption([a], "true")
        self.assertEqual((0, None), (again.changed, again.details["change"]))
        self.assertEqual(written, os.stat(a).st_mtime_ns)


if __name__ == "__main__":
    unittest.main()

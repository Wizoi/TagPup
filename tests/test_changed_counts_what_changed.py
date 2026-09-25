"""A journaled write counts in `changed` only a file whose read-back differs from what it
held (docs/findings.md, #276).

A write ExifTool reported done that left the file holding what it held before was read
back, recorded done with its after equal to its before, and counted as changed. A write
reports what it changed: that file is an error, and is taken out of the change. Real
ExifTool reads the files; the write that does nothing is stood in for.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import keywords_of  # noqa: E402

from tagpup.files import field_values  # noqa: E402


class AWriteThatChangedNothing(ReadFilesCase):
    def test_is_not_counted_changed(self):
        a, b = self.make_read("a.jpg"), self.make_read("b.jpg")
        real = field_values.write

        def write(et, path, values):
            if path != a:
                real(et, path, values)

        with mock.patch.object(field_values, "write", side_effect=write):
            result = self.add([a, b])
        change = result.details["change"]
        self.assertEqual(1, result.changed)
        self.assertEqual([a], [what for what, _why in result.errors])
        self.assertNotIn(a, result.details["written"])
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertEqual(["done"], self.states(change), "the file that did not change is still in the change")
        self.assertEqual("applied", self.status(change))

    def test_nothing_changed_is_a_failed_change(self):
        a = self.make_read("a.jpg")
        with mock.patch.object(field_values, "write"):
            result = self.add([a])
        self.assertEqual((0, 1), (result.changed, len(result.errors)))
        self.assertEqual(("failed", []), (self.status(result.details["change"]), self.states(result.details["change"])))


if __name__ == "__main__":
    unittest.main()

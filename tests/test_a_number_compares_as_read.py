"""The journal compares a field's values as a read of the file gives them
(docs/findings.md, #264).

ExifTool answers a text that looks like a number as a JSON number: "1.50" is read back
as 1.5. The journal compared values as trimmed text, so a caption "1.50" written
before a crash was taken, on settling, for a file holding neither what the plan read
nor what it was to hold -- a conflict, never finished -- and a file already holding it
was planned and written again on every edit. Real ExifTool, on JPEGs made here.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at  # noqa: E402

import photo_rows  # noqa: E402
from tagpup.core import fields  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.store import db  # noqa: E402


class ReadFilesCase(FilesCase):
    """Rows as the indexer makes them (tests/photo_rows.py)."""

    def make_read(self, name, tags=("Beach",)):
        path = self.make(name, tags, row=False)
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags)})
            conn.commit()
        finally:
            conn.close()
        return path

    def caption(self, photo_paths, caption):
        return tagging.write_suggestions(self.library, [(path, [], caption) for path in photo_paths], EXIFTOOL)


class ANumber(ReadFilesCase):
    def test_is_read_as_exiftool_answers_it(self):
        self.assertEqual(["1.5"], fields.as_read("XMP:Description", "1.50"))
        self.assertEqual(["007"], fields.as_read("XMP:Description", "007"))
        self.assertTrue(fields.reads_same({"XMP:Description": ["1.5"]}, {"XMP:Description": "1.50"}))
        self.assertFalse(fields.reads_same({"XMP:Description": ["1.5"]}, {"XMP:Description": "1.55"}))

    def test_written_before_a_crash_settles_done(self):
        a = self.make_read("a.jpg")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.caption([a], "1.50")
        change = self.last_change()
        self.assertEqual(1, self.settle())
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))

    def test_a_file_holding_it_is_not_written_again(self):
        a = self.make_read("a.jpg")
        self.assertEqual(1, self.caption([a], "1.50").changed)
        written = os.stat(a).st_mtime_ns
        again = self.caption([a], "1.50")
        self.assertEqual((0, None), (again.changed, again.details["change"]))
        self.assertEqual(written, os.stat(a).st_mtime_ns)


if __name__ == "__main__":
    unittest.main()

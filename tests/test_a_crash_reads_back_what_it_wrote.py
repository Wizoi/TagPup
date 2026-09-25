"""A change stopped part way still records what each file it wrote holds
(docs/findings.md, #286).

The files written are read back at the end of a change (#273). One stopped mid-loop --
a BaseException, the process stopping -- left the files it had recorded done holding
their `after` as asked for, and settling reads back only the files it writes: a value
ExifTool keeps otherwise (IPTC cuts a keyword at 64 bytes) stayed recorded as asked.
Real ExifTool, on JPEGs made here.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tagpup.services import file_changes  # noqa: E402

LONG = "Places/" + "Harbour" * 13


class AChangeStoppedPartWay(ReadFilesCase):
    def test_reads_back_the_files_it_recorded(self):
        photos = [self.make_read(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", nth=2)):
            with self.assertRaises(Crash):
                self.add(photos, [LONG])
        change = self.last_change()
        found = self.rows("SELECT path, state, fields_after FROM change_files WHERE change_id = ? ORDER BY id",
                          (change,))
        self.assertEqual(["done", "done", "planned"], [state for _p, state, _a in found])
        for path, _state, after in found[:2]:
            self.assertEqual(sorted(["Beach", LONG[:64]]), sorted(json.loads(after)["IPTC:Keywords"]), path)
            raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (path,))[0][0])
            self.assertEqual(sorted(["Beach", LONG[:64]]), sorted(raw["IPTC:Keywords"]))


if __name__ == "__main__":
    unittest.main()

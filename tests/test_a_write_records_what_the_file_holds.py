"""A journaled write records what each file holds after it, not what it asked for
(docs/findings.md, #273).

ExifTool does not always keep a value as written: IPTC:Keywords is cut at 64 bytes, a
letter outside Latin-1 comes back as "?", "1.50" as 1.5. The file was recorded done
holding what was asked for, so undoing the change refused it -- it held neither -- and
settling would have called it a conflict. The files written are read back, in one read
for the change, and the journal and the row take what each holds. A file changed by
another program since it was written is not taken as the write's. Real ExifTool, on
JPEGs made here.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, FilesCase, keywords_of, write_outside  # noqa: E402

from tagpup.files import field_values  # noqa: E402
from tagpup.services import file_changes  # noqa: E402

#: 98 bytes, longer than the 64 IPTC:Keywords keeps.
LONG = "Places/" + "Harbour" * 13


class AKeywordLongerThanIptcKeeps(FilesCase):
    def setUp(self):
        super().setUp()
        self.photos = [self.make(name) for name in ("a.jpg", "b.jpg", "c.jpg")]

    def iptc_of(self, path):
        from tagpup.files.exiftool_session import ExifToolSession

        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["IPTC:Keywords"])[0].get("IPTC:Keywords")
        return sorted(found if isinstance(found, list) else [found])

    def recorded_after(self, change, path):
        return json.loads(self.rows("SELECT fields_after FROM change_files WHERE change_id = ? AND path = ?",
                                    (change, path))[0][0])

    def test_is_recorded_as_the_file_holds_it_and_undone(self):
        with mock.patch.object(field_values, "read", wraps=field_values.read) as read:
            result = self.add(self.photos, [LONG])
        change = result.details["change"]
        self.assertEqual(3, result.changed)
        # Read for the plan and read back, each once for every file; the check before
        # each write is one file at a time.
        self.assertEqual(2, sum(1 for call in read.call_args_list if len(call.args[1]) == 3))
        cut = [t for t in self.iptc_of(self.photos[0]) if t != "Beach"][0]
        self.assertEqual(64, len(cut.encode("utf-8")))
        self.assertEqual(sorted(["Beach", cut]), sorted(self.recorded_after(change, self.photos[0])["IPTC:Keywords"]))
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (self.photos[0],))[0][0])
        self.assertEqual(sorted(["Beach", cut]), sorted(raw["IPTC:Keywords"]))
        self.assertEqual(["Beach", LONG], keywords_of(self.photos[0]))

        undone = self.undo(change)
        self.assertEqual((3, []), (undone.changed, undone.errors))
        for path in self.photos:
            self.assertEqual((["Beach"], ["Beach"]), (keywords_of(path), self.iptc_of(path)))
            self.assertEqual(["Beach"], self.indexed(path))

    def test_a_file_changed_outside_since_its_write_is_not_taken_as_the_writes(self):
        first = self.photos[0]
        seen = []

        def elsewhere(step):
            if step == "file recorded" and not seen:
                seen.append(step)
                write_outside(first, {"XMP:Subject": ["Written Elsewhere"]})

        with mock.patch.object(file_changes, "_reached", side_effect=elsewhere):
            change = self.add(self.photos, [LONG]).details["change"]
        after = self.recorded_after(change, first)
        self.assertEqual(["Beach", LONG], sorted(after["XMP:Subject"]))
        self.assertEqual(["Beach", LONG], sorted(after["IPTC:Keywords"]))
        undone = self.undo(change)
        self.assertEqual(2, undone.changed)
        self.assertIn("photo %d" % self.photo_id(first), [what for what, _why in undone.errors])
        self.assertEqual(["Written Elsewhere"], keywords_of(first))


if __name__ == "__main__":
    unittest.main()

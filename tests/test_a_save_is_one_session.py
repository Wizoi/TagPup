"""Saving one photo is one ExifTool session: one read before the write, the write, and
one read back (review of pass/journal, item 3).

Journaled, the save took four sessions and six reads -- the check of its tags, the
plan's read, the re-read before the write, the read back, the Smart Rename name, the
row's raw_metadata -- where it had taken three and three, and went from 0.69 s to 0.89 s
a save. The check's read is the plan's before, and the read back is the row's
raw_metadata. Real ExifTool, on a JPEG made here; the session counted, not stood in for.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, keywords_of  # noqa: E402

from tagpup.files import exiftool_session  # noqa: E402
from tagpup.services import tagging  # noqa: E402


class ASave(ReadFilesCase):
    def test_is_one_session_two_reads_and_one_write(self):
        a = self.make_read("a.jpg")
        real = exiftool_session.ExifToolSession
        sessions, reads, writes = [], [], []

        def counted(*args, **kwargs):
            session = real(*args, **kwargs)
            sessions.append(session)
            original_get, original_set = real.get_tags, real.set_tags
            session.get_tags = lambda *a, **k: reads.append(a) or original_get(session, *a, **k)
            session.set_tags = lambda *a, **k: writes.append(a) or original_set(session, *a, **k)
            return session

        with mock.patch.object(exiftool_session, "ExifToolSession", side_effect=counted):
            result = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"],
                                        "2019-06-15T10:30:00", EXIFTOOL, "")
        self.assertEqual((True, 1), (result.ok, result.changed))
        self.assertEqual((1, 2, 1), (len(sessions), len(reads), len(writes)),
                         "sessions, reads and writes of one save")
        self.assertEqual(["Beach", "Places/Quay"], keywords_of(a))


if __name__ == "__main__":
    unittest.main()

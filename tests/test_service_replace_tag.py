"""tagpup.services.tagging.replace_tag: renaming or deleting a tag, merging one into
another, renaming a person -- on every photo carrying it.

ExifTool is stood in for; the index rows are real.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import tagging  # noqa: E402


def exiftool():
    """A session that accepts every write and keeps what it was told."""
    et = mock.MagicMock()
    session = mock.MagicMock()
    session.return_value.__enter__.return_value = et
    session.return_value.__exit__.return_value = False
    return session, et


class ReplacingATag(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.harbour = self.lib.photo("harbour.jpg")
        self.beach = self.lib.photo("beach.jpg")
        self.lib.add_row(self.harbour, tags=["Places/Harbour", "Places/Harbour/Pier", "Relay"])
        self.lib.add_row(self.beach, tags=["Beach"])

    def replace(self, old, new):
        session, et = exiftool()
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            return tagging.replace_tag(self.lib.library, [self.harbour, self.beach], old, new,
                                       "exiftool"), et

    def tags(self, path):
        return json.loads(self.lib.rows("SELECT tags FROM photos WHERE path = ?", (path,))[0][0])

    def test_the_photos_carrying_it_are_written_and_recorded(self):
        result, et = self.replace("Places/Harbour", "Places/Marina")
        self.assertEqual((result.attempted, result.changed, result.errors), (2, 1, []))
        self.assertEqual(result.skipped, [(self.beach, "does not carry the tag")])
        written = et.set_tags.call_args.kwargs["tags"]["XMP:Subject"]
        self.assertEqual(written, ["Places/Marina", "Places/Marina/Pier", "Relay"])
        self.assertEqual(self.tags(self.harbour), ["Places/Marina", "Places/Marina/Pier", "Relay"])

    def test_without_a_new_name_it_comes_off(self):
        result, _ = self.replace("Beach", None)
        self.assertEqual(result.changed, 1)
        self.assertEqual(self.tags(self.beach), [])

    def test_a_photo_that_cannot_be_written_is_an_error_and_keeps_its_row(self):
        session, et = exiftool()
        et.set_tags.side_effect = RuntimeError("the file is locked")
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = tagging.replace_tag(self.lib.library, [self.beach], "Beach", "Places/Beach", "exiftool")
        self.assertEqual((result.changed, result.message()), (0, "the file is locked"))
        self.assertEqual(self.tags(self.beach), ["Beach"])


if __name__ == "__main__":
    unittest.main()

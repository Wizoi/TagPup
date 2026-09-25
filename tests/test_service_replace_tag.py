"""tagpup.services.tagging.replace_tag: renaming or deleting a tag, merging one into
another, renaming a person -- on every photo carrying it.

ExifTool is stood in for, answering each read with what the file holds; the index rows
are real.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import tagging  # noqa: E402


def exiftool(files):
    """A session whose photos hold the keywords in `files` (path -> tags), and which
    accepts every write and keeps what it was told."""
    et = mock.MagicMock()
    et.get_tags.side_effect = lambda paths, tags=None: [
        {"SourceFile": p, "XMP:Subject": list(files.get(p, []))} for p in paths]
    keeps_writes(et, files)
    session = mock.MagicMock()
    session.return_value.__enter__.return_value = et
    session.return_value.__exit__.return_value = False
    return session, et


def keeps_writes(et, files):
    """Have the stand-in `et` keep each keyword write in `files` (path -> tags), as a
    file would: the journal reads back what it wrote (tagpup.services.file_changes)."""
    def set_tags(paths, tags=None, params=None):
        for path in paths:
            if "XMP:Subject" in (tags or {}):
                value = tags["XMP:Subject"]
                files[path] = list(value) if isinstance(value, list) else [value]
            elif "-XMP:Subject=" in (params or []):
                files[path] = []

    def execute(*args):
        if "-XMP:Subject=" in args:
            files[args[-1]] = []
        return ""

    et.set_tags.side_effect = set_tags
    et.execute.side_effect = execute


HARBOUR = ["Places/Harbour", "Places/Harbour/Pier", "Relay"]


class ReplacingATag(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.harbour = self.lib.photo("harbour.jpg")
        self.beach = self.lib.photo("beach.jpg")
        self.lib.add_row(self.harbour, tags=HARBOUR)
        self.lib.add_row(self.beach, tags=["Beach"])
        self.files = {self.harbour: list(HARBOUR), self.beach: ["Beach"]}

    def replace(self, old, new, photos=None):
        session, et = exiftool(self.files)
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = tagging.replace_tag(self.lib.library, photos or [self.harbour, self.beach],
                                         old, new, "exiftool")
        return result, et

    def written(self, et, path):
        """What was written into `path`'s keywords, or None if nothing was."""
        for call in et.set_tags.call_args_list:
            if call.args[0] == [path]:
                return call.kwargs["tags"]["XMP:Subject"]
        return None

    def tags(self, path):
        return json.loads(self.lib.rows("SELECT tags FROM photos WHERE path = ?", (path,))[0][0])

    def test_the_photos_carrying_it_are_written_and_recorded(self):
        result, et = self.replace("Places/Harbour", "Places/Marina")
        self.assertEqual((result.attempted, result.changed, result.errors), (2, 1, []))
        self.assertEqual(result.skipped, [(self.beach, "does not carry the tag")])
        self.assertEqual(self.written(et, self.harbour), ["Places/Marina", "Places/Marina/Pier", "Relay"])
        self.assertEqual(self.tags(self.harbour), ["Places/Marina", "Places/Marina/Pier", "Relay"])

    def test_without_a_new_name_it_comes_off(self):
        result, _ = self.replace("Beach", None)
        self.assertEqual(result.changed, 1)
        self.assertEqual(self.tags(self.beach), [])

    def test_a_photo_that_cannot_be_written_is_an_error_and_keeps_its_row(self):
        session, et = exiftool(self.files)
        et.set_tags.side_effect = RuntimeError("the file is locked")
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = tagging.replace_tag(self.lib.library, [self.beach], "Beach", "Places/Beach", "exiftool")
        self.assertEqual((result.changed, result.message()), (0, "the file is locked"))
        self.assertEqual(self.tags(self.beach), ["Beach"])

    # Finding #30: the write started from the index's copy of the tags, not the file's.

    def test_a_keyword_only_the_file_holds_is_kept(self):
        # Written by another program since the photo was last indexed.
        self.files[self.harbour] = HARBOUR + ["Written Elsewhere"]
        _, et = self.replace("Places/Harbour", "Places/Marina", [self.harbour])
        self.assertIn("Written Elsewhere", self.written(et, self.harbour))

    def test_a_tag_only_the_index_holds_is_not_written_into_the_file(self):
        # Taken off in another program: renaming it must not put it back.
        self.files[self.beach] = []
        result, et = self.replace("Beach", "Places/Beach", [self.beach])
        self.assertIsNone(self.written(et, self.beach))
        self.assertEqual(result.changed, 0)
        # And the row says what the file holds, as every other writer leaves it.
        self.assertEqual(self.tags(self.beach), [])


if __name__ == "__main__":
    unittest.main()

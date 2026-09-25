"""The CLI's `write` of a suggestions file is one change of photo files, which undo
reverses (docs/findings.md, #266).

It wrote each photo through its own ExifTool session with no record of what the files
held before, so a write could not be undone but from the _original copies ExifTool left
beside each file. It now goes through the journal the page's bulk writes use
(tagpup.services.file_changes). Real ExifTool, on JPEGs made here; the rows seeded as
the indexer stores them.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, FilesCase, field_of, keywords_of  # noqa: E402

from tagpup_cli import write_suggestions_file  # noqa: E402


class TheClisWrite(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make("a.jpg"), self.make("b.jpg", ["Beach", "Relay"])
        self.suggestions = os.path.join(self.home.root, "suggestions.json")
        with open(self.suggestions, "w", encoding="utf-8") as handle:
            json.dump([{"path": path, "suggested_tags": [{"tag": "Activity/Rowing", "score": 0.9}]}
                       for path in (self.a, self.b)], handle)

    def write(self):
        with mock.patch("builtins.input", return_value="YES"):
            return write_suggestions_file(self.suggestions, self.library.path, EXIFTOOL, live=True)

    def test_is_a_change_that_undo_reverses(self):
        captions = {path: field_of(path, "XMP:Description") for path in (self.a, self.b)}
        self.assertTrue(self.write())
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(self.a))
        self.assertEqual(["Activity/Rowing", "Beach"], self.indexed(self.a))
        change = self.last_change()
        self.assertEqual([("write suggestions", "applied")],
                         self.rows("SELECT operation, status FROM changes WHERE id = ?", (change,)))
        self.assertEqual(["done", "done"], self.states(change))
        self.assertEqual([], [name for name in os.listdir(self.folder) if name.endswith("_original")],
                         "ExifTool kept a copy beside a file: the journal is the way back")

        undone = self.undo(change)
        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], keywords_of(self.a))
        self.assertEqual(["Beach", "Relay"], keywords_of(self.b))
        self.assertEqual(["Beach", "Relay"], self.indexed(self.b))
        self.assertEqual(captions, {path: field_of(path, "XMP:Description") for path in (self.a, self.b)})


if __name__ == "__main__":
    unittest.main()

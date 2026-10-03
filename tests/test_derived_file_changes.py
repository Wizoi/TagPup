"""The derived tables follow a change of photo files, a crash in it, and its undo: real JPEGs, written by
the real ExifTool (tests/test_file_journal.py's FilesCase), the rows seeded as the indexer stores them.

A bulk tag write is journaled file by file (tagpup.services.file_changes) and tells each photo's row what its file
now holds in the transaction that marks the file done (store.photos.follow_fields); the undo does the same
backwards. photo_tags, photo_folder and photo_meta (tagpup.store.derived) are what the views read, so they must be
what a rebuild from the rows would make them after each of those, or the views show what a photo used to hold.
Smart Rename moves the rows of the photos it renames.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, FilesCase, crash_at, Crash  # noqa: E402

from tagpup.services import file_changes, photos as photo_actions  # noqa: E402
from tagpup.store import db, derived, taxonomy  # noqa: E402


class WhatTheViewsRead(FilesCase):
    def setUp(self):
        super().setUp()
        self.a = self.make("a.jpg", ["Beach"])
        self.b = self.make("b.jpg", ["Beach", "Relay"])
        db.write_with_connection(self.library.path, lambda conn: [taxonomy.add_node(conn, tag)
                                                                  for tag in ("Beach", "Relay", "Harbour")])
        db.write_with_connection(self.library.path, derived.rebuild_all)   # a library at rest

    def keyword_rows(self):
        return self.rows("SELECT pt.photo_id, t.tag FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id"
                         " ORDER BY pt.photo_id, t.tag")

    def agree(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
        finally:
            conn.close()

    def test_a_bulk_tag_write_and_its_undo(self):
        before = self.keyword_rows()
        self.assertEqual([(self.photo_id(self.a), "Beach"), (self.photo_id(self.b), "Beach"),
                          (self.photo_id(self.b), "Relay")], before)
        result = self.add([self.a, self.b], ("Harbour",))
        self.assertEqual((2, 2, []), (result.attempted, result.changed, result.errors))
        self.assertEqual(["Harbour"] * 2, [tag for _photo, tag in self.keyword_rows() if tag == "Harbour"])
        self.assertEqual(5, len(self.keyword_rows()))
        self.agree()
        undone = self.undo(result.details["change"])
        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual(before, self.keyword_rows(), "the undo gives the views the rows they had")
        self.agree()

    def test_a_write_the_process_died_in_is_finished_with_its_rows(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([self.a], ("Harbour",))
        self.settle()
        self.assertIn((self.photo_id(self.a), "Harbour"), self.keyword_rows())
        self.agree()

    def test_a_smart_rename_moves_the_rows_and_its_undo_moves_them_back(self):
        c = self.make("IMG_0001.jpg", caption="Start", preserved="IMG_0001.jpg")
        db.write_with_connection(self.library.path, derived.rebuild_all)
        folder_rows = self.rows("SELECT COUNT(*) FROM folders")[0][0]
        before = self.keyword_rows()
        result = photo_actions.smart_rename(self.library, [c], "Regatta", "{grouping} - {index} - {caption}", EXIFTOOL)
        self.assertEqual(1, result.changed)
        self.assertEqual(folder_rows, self.rows("SELECT COUNT(*) FROM folders")[0][0], "the same folder")
        self.assertEqual(before, self.keyword_rows())
        self.agree()
        self.undo(result.details["change"])
        self.assertEqual(before, self.keyword_rows())
        self.agree()


if __name__ == "__main__":
    unittest.main()

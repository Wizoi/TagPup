"""Settling a write a crash stopped carries the photo's CLIP vectors over it
(docs/findings.md, #265).

A metadata write changes a file's mtime and size, and the vectors computed from the
file before it are carried to its new stamp by the stamp it had just before the write
(embeddings.restamp). A crash between the write and its row lost that stamp -- it was
in memory only -- so settling left the vectors stamped for a file that no longer was,
and they were computed again. The stamp is now recorded as the file is marked writing
(change_files.stamp, migration 12). Real ExifTool, on JPEGs made here.
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
from tagpup.store import db, embeddings  # noqa: E402

MODEL = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
VECTOR = b"\x00\x00\x80\x3f" * 4


class AWriteACrashStopped(ReadFilesCase):
    def setUp(self):
        super().setUp()
        self.a = self.make_read("a.jpg")
        stat = os.stat(self.a)
        self.before = (stat.st_mtime, stat.st_size)
        conn = db.connect(self.library.path)
        try:
            embeddings.put(conn, self.a, MODEL, stat.st_mtime, stat.st_size, VECTOR)
            conn.commit()
        finally:
            conn.close()

    def vector_stamp(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            found = embeddings.get(conn, self.a, MODEL)
        finally:
            conn.close()
        return (found.mtime, found.size)

    def test_keeps_the_stamp_from_before_its_write(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([self.a])
        change = self.last_change()
        stamp = self.rows("SELECT stamp FROM change_files WHERE change_id = ?", (change,))[0][0]
        self.assertEqual(list(self.before), json.loads(stamp))
        self.assertEqual(1, self.settle())
        self.assertEqual(["done"], self.states(change))
        stat = os.stat(self.a)
        self.assertNotEqual(self.before, (stat.st_mtime, stat.st_size))
        self.assertEqual((stat.st_mtime, stat.st_size), self.vector_stamp(), "the vectors were not carried over")

    def test_a_write_not_stopped_carries_them_too(self):
        self.add([self.a])
        stat = os.stat(self.a)
        self.assertEqual((stat.st_mtime, stat.st_size), self.vector_stamp())


if __name__ == "__main__":
    unittest.main()

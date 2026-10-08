"""A photo's row is compared with its file by the store's rule for "same modified time" (docs/findings.md, #302).

tagpup.services.inspect kept a 0.1 s tolerance of its own beside tagpup.store.photos.MTIME_TOLERANCE, which the scan and
refresh_rows use; a change of the one would have left the MCP's "is this row stale" answering by the other.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import inspect  # noqa: E402
from tagpup.store import db, photos, schema  # noqa: E402


class TheMtimeOfARowAndItsFile(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.db_path = home.library("library.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)
        self.path = os.path.join(home.root, "Photos", "a.jpg")
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as handle:
            handle.write(b"x")
        conn = db.connect(self.db_path)
        try:
            self.photo_id = conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                                         " VALUES (?, 100.0, 1, '[]', '[]', '{}')", (self.path,)).lastrowid
            conn.commit()
        finally:
            conn.close()

    def compared(self, file_mtime):
        record = {"document_id": None, "mtime": file_mtime, "size": 1, "tags": [], "captions": [], "people": [],
                  "raw_metadata": {}}
        with mock.patch.object(inspect, "MetadataExtractor") as extractor:
            extractor.return_value.batch_read.return_value = [record]
            return inspect.photo_against_file(self.library, self.photo_id, exiftool_path="exiftool")

    def test_the_tolerance_is_the_stores(self):
        self.assertTrue(self.compared(100.05)["mtime"]["same"])
        self.assertFalse(self.compared(100.5)["mtime"]["same"])
        with mock.patch.object(photos, "MTIME_TOLERANCE", 5.0):
            self.assertTrue(self.compared(103.0)["mtime"]["same"], "inspect keeps a tolerance of its own")


if __name__ == "__main__":
    unittest.main()

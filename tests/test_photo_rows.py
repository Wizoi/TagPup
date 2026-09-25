"""tests/photo_rows.py makes the rows the library holds: a read row looks read, an unread
one does not, and a read row is dated from what was read."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.services import refresh_rows  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
import photo_rows  # noqa: E402


class PhotoRows(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="photo_rows_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        schema.ensure(self.db)
        self.photo = os.path.join(self.dir, "harbour.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"jpeg")

    def row(self, add):
        conn = db.connect(self.db)
        try:
            photo_id = add(conn)
            conn.commit()
            return conn.execute("SELECT mtime, size, tags, raw_metadata, taken FROM photos WHERE id = ?",
                                (photo_id,)).fetchone()
        finally:
            conn.close()

    def test_a_read_row_is_stamped_dated_and_not_taken_for_unread(self):
        mtime, size, tags, raw, taken = self.row(lambda conn: photo_rows.add_read(
            conn, self.photo, {"XMP:Subject": ["Activity/Sailing"], "EXIF:DateTimeOriginal": "2024:07:04 10:00:00"}))
        self.assertEqual((mtime, size), (os.stat(self.photo).st_mtime, 4))
        self.assertEqual(json.loads(tags), ["Activity/Sailing"])
        self.assertIn("Subject", json.loads(raw), "a read records the bare name too")
        self.assertTrue(taken.startswith("2024:07:04"), taken)
        self.assertFalse(refresh_rows.never_read(raw))

    def test_an_unread_row_holds_the_path_and_nothing_else(self):
        mtime, size, tags, raw, taken = self.row(lambda conn: photo_rows.add_unread(conn, self.photo))
        self.assertEqual((mtime, size, json.loads(tags), json.loads(raw)), (None, None, [], {}))


if __name__ == "__main__":
    unittest.main()

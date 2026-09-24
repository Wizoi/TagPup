"""The pass that gives already-indexed photos the identity new ones get.

Rows indexed before identities existed have `document_id` NULL, and would only fill in
as those photos were re-indexed -- which for a settled library is never. This walks
them, reading the file's DocumentID and minting one where the file has none.

The test that matters most is the path-spelling one. ExifTool answers with forward
slashes whatever it was handed; the index stores whatever spelling it was given, which
on Windows is backslashes. The UPDATE matches on path exactly, so recording against
ExifTool's spelling updates nothing -- and the run still reports every photo as read.
The first live run did exactly that: 60 photos processed, 60 reported, 0 recorded.
"""
import json
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
from backfill_document_ids import record, rows_without_identity

from tagpup.store import schema  # noqa: E402


class BackfillCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)

        schema.ensure(self.db_path)

        def cleanup():
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def add(self, path, document_id=None):
        def store(conn):
            conn.execute(
                "INSERT INTO photos (path, tags, captions, raw_metadata, "
                "document_id) VALUES (?, ?, ?, ?, ?)",
                (path, json.dumps([]), json.dumps([]),
                 json.dumps({}), document_id))
        tagpup_db.write_with_connection(self.db_path, store)

    def identity_of(self, path):
        conn = tagpup_db.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT document_id FROM photos WHERE path = ?", (path,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None


class TestRecording(BackfillCase):
    def test_an_identity_is_written_against_the_path_the_index_holds(self):
        # The live failure: ExifTool reports D:/x/a.jpg, the index holds D:\x\a.jpg,
        # and the UPDATE matches nothing while the run reports success.
        indexed = "D:\\Library\\2020\\a.jpg"
        self.add(indexed)
        written = record(self.db_path, {indexed: "xmp.did:abc"})
        self.assertEqual(written, 1, "the update matched no row")
        self.assertEqual(self.identity_of(indexed), "xmp.did:abc")

    def test_recording_reports_what_it_actually_changed(self):
        # Not what it was asked to change. A count of attempts hid the bug above.
        self.add("D:\\Library\\2020\\a.jpg")
        written = record(self.db_path, {"D:/Library/2020/nowhere.jpg": "xmp.did:abc"})
        self.assertEqual(written, 0, "a write that matched nothing reported success")

    def test_several_at_once(self):
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            self.add("D:\\Library\\2020\\" + name)
        written = record(self.db_path, {
            "D:\\Library\\2020\\a.jpg": "xmp.did:1",
            "D:\\Library\\2020\\b.jpg": "xmp.did:2",
        })
        self.assertEqual(written, 2)
        self.assertIsNone(self.identity_of("D:\\Library\\2020\\c.jpg"))


class TestWhatNeedsDoing(BackfillCase):
    def test_a_row_that_has_an_identity_is_not_revisited(self):
        # What makes the pass resumable: an interrupted run leaves the rows it
        # finished alone, and re-running picks up where it stopped.
        here = os.path.abspath(__file__)
        self.add(here, document_id="xmp.did:already")
        missing, _gone = rows_without_identity(self.db_path)
        self.assertEqual(missing, [])

    def test_a_row_with_no_identity_is_listed(self):
        here = os.path.abspath(__file__)
        self.add(here)
        missing, _gone = rows_without_identity(self.db_path)
        self.assertEqual(missing, [here])

    def test_an_empty_identity_counts_as_missing(self):
        here = os.path.abspath(__file__)
        self.add(here, document_id="")
        missing, _gone = rows_without_identity(self.db_path)
        self.assertEqual(missing, [here])

    def test_a_photo_that_has_moved_away_is_separated_not_failed(self):
        # Its row is not wrong, it is pointing where the file no longer is -- the very
        # problem an identity prevents, and one this pass cannot fix retroactively.
        self.add("D:\\Library\\2020\\gone.jpg")
        missing, gone = rows_without_identity(self.db_path)
        self.assertEqual(missing, [])
        self.assertEqual(gone, ["D:\\Library\\2020\\gone.jpg"])

    def test_a_database_without_the_column_says_so_rather_than_crashing(self):
        import tempfile
        fd, other = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(other)
        conn = tagpup_db.connect(other)
        conn.execute("CREATE TABLE photos (path TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.addCleanup(lambda: os.path.exists(other) and os.remove(other))

        with self.assertRaises(SystemExit) as caught:
            rows_without_identity(other)
        self.assertIn("document_id", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

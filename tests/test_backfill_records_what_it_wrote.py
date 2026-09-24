"""backfill_document_ids records identities against any spelling, with the new stat.

Its record() matched rows with WHERE path = ?, so a row spelled differently from the
path it was given took nothing and nobody was told. And a photo it had just written an
identity into kept its old mtime and size in the index, so every scan afterwards
distrusted the row and read the photo again.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backfill_document_ids as backfill  # noqa: E402
import db as tagpup_db  # noqa: E402
import paths  # noqa: E402


class BackfillRecordsWhatItWrote(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="backfill_record_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        self.photo = os.path.join(self.dir, "Regatta", "start.jpg")
        os.makedirs(os.path.dirname(self.photo))
        with open(self.photo, "wb") as handle:
            handle.write(b"a photo, with its identity just written")
        conn = tagpup_db.connect(self.db)
        conn.execute("CREATE TABLE photos (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, document_id TEXT)")
        conn.execute("INSERT INTO photos VALUES (?, 1.0, 1, NULL)", (self.photo,))
        conn.commit()
        conn.close()

    def row(self):
        conn = tagpup_db.connect(self.db)
        try:
            return conn.execute("SELECT document_id, mtime, size FROM photos").fetchone()
        finally:
            conn.close()

    @unittest.skipUnless(paths.COLLATE == "NOCASE", "paths compare without case only on Windows")
    def test_another_spelling_of_the_path_finds_the_row(self):
        written = backfill.record(self.db, {self.photo.upper(): "xmp.did:abc"})
        self.assertEqual(1, written)
        self.assertEqual("xmp.did:abc", self.row()[0])

    def test_a_minted_photo_takes_its_new_stat(self):
        backfill.record(self.db, {self.photo: "xmp.did:abc"}, minted={self.photo})
        stat = os.stat(self.photo)
        self.assertEqual(("xmp.did:abc", stat.st_mtime, stat.st_size), self.row())


if __name__ == "__main__":
    unittest.main()

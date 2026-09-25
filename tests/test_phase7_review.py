"""What the review of phase 7 found in the MCP server and the maintenance scaffold.

- `photo_against_file` called nearly every real row stale: a fresh read keeps each field
  twice, prefixed and bare (`XMP:Subject`, `Subject`), and most stored rows hold the
  prefixed spelling only -- two spellings of one value (findings, phase 7 review).
- It answered "KeyError" for a file ExifTool could not read.
- A duplicate face named between the plan and the write was deleted with its name.
- An apply whose write failed answered with the error's kind alone, not the backup it
  had already taken.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import duplicate_faces, inspect, maintenance  # noqa: E402
from tagpup.store import db, schema, taxonomy  # noqa: E402


class ALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("library.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)

    def execute(self, sql, params=()):
        conn = db.connect(self.db_path)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class APhotoAgainstItsFile(ALibrary):
    def photo(self):
        from PIL import Image
        path = os.path.join(self.home.root, "Photos", "a.jpg")
        os.makedirs(os.path.dirname(path))
        Image.new("RGB", (8, 8)).save(path, "JPEG")
        return path

    def test_a_row_that_matches_its_file_is_not_stale(self):
        exiftool = own_home.installed_exiftool()
        if not exiftool:
            self.skipTest("ExifTool is not installed")
        path = self.photo()
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            known = taxonomy.read_people_vocabulary(conn)
        finally:
            conn.close()
        record = MetadataExtractor(exiftool).batch_read([path], people=known)[0]
        # As most real rows hold it: the prefixed spelling of each field only.
        prefixed = {k: v for k, v in record["raw_metadata"].items() if ":" in k}
        photo_id = self.execute(
            "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, document_id) VALUES (?,?,?,?,?,?,?)",
            (path, record["mtime"], record["size"], json.dumps(record["tags"]), json.dumps(record["captions"]),
             json.dumps(prefixed, default=str), record.get("document_id")))
        answer = inspect.photo_against_file(self.library, photo_id, exiftool_path=exiftool)
        self.assertFalse(answer["stale"], answer)
        self.assertEqual([], answer["raw_metadata"]["fields_differing"])

    def test_a_file_exiftool_cannot_read_is_said_so(self):
        path = self.photo()
        photo_id = self.execute("INSERT INTO photos (path, mtime, size, tags, raw_metadata) VALUES (?,?,?,?,?)",
                                (path, 1.0, 1, "[]", "{}"))
        answer = inspect.photo_against_file(self.library, photo_id,
                                            exiftool_path=os.path.join(self.home.root, "no-exiftool.exe"))
        self.assertTrue(answer["file_exists"])
        self.assertFalse(answer["file_read"])
        self.assertIsNone(answer["stale"])


class AFaceNamedBetweenThePlanAndTheWrite(ALibrary):
    def test_is_not_deleted(self):
        photo = self.execute("INSERT INTO photos (path) VALUES (?)", (os.path.abspath("D:/Pictures/a.jpg"),))
        kept = self.execute("INSERT INTO faces (photo_id, box) VALUES (?, '[0, 0, 10, 10]')", (photo,))
        copy = self.execute("INSERT INTO faces (photo_id, box) VALUES (?, '[0, 0, 10, 10]')", (photo,))
        real_backup = maintenance.db.backup

        def named_meanwhile(path, reason):
            # What TagTuner could do while the library is copied.
            self.execute("UPDATE faces SET name = 'Rowan Thackeray', name_source = 'manual' WHERE id = ?", (copy,))
            return real_backup(path, reason)

        with mock.patch.object(maintenance.db, "backup", side_effect=named_meanwhile):
            result = duplicate_faces.dedupe_faces(self.library, apply=True)
        self.assertEqual(1, result.attempted)
        self.assertEqual(0, result.changed)
        self.assertEqual([(kept,), (copy,)], self.rows("SELECT id FROM faces ORDER BY id"))


class AnApplyWhoseWriteFails(ALibrary):
    def test_says_what_it_backed_up(self):
        def plan(library):
            return maintenance.Plan(size=1, counts={"n": 1})

        def write(library, planned, result):
            raise RuntimeError("database is locked")

        result = maintenance.run(self.library, "trial", plan, write, apply=True)
        self.assertFalse(result.ok)
        self.assertEqual(0, result.changed)
        self.assertTrue(result.errors)
        self.assertTrue(result.details["backup"] and os.path.exists(result.details["backup"]))


if __name__ == "__main__":
    unittest.main()

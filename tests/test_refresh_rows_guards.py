"""refresh_rows_from_files: a row saved during the run keeps the save, and more cases.

- A row the app saved after this run read the file was overwritten with the older read:
  each UPDATE now requires the row to still have the mtime and size it was read with.
- It read with whatever ExifTool was on PATH, not the one config.ini names; --exiftool
  chooses, and the configured one is the default.
- Two cases the tests did not cover: a person named only on a face stays in the row's
  people, and a file that cannot be read is left alone and counted.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_refresh_rows_from_files as base  # noqa: E402

import db  # noqa: E402


class RefreshGuards(base.RefreshRowsFromFiles):
    def test_a_row_saved_during_the_run_keeps_the_save(self):
        saved_tags = ["Activity/Rowing"]
        real = self.fake_batch_read

        def read_then_the_app_saves(extractor, paths, db_path=None):
            records = real(extractor, paths, db_path)
            conn = db.connect(self.db)
            conn.execute("UPDATE photos SET tags = ?, mtime = 2000000.0 WHERE path = ?",
                         (json.dumps(saved_tags), self.files["stale_keywords"]))
            conn.commit()
            conn.close()
            return records

        self.fake_batch_read = read_then_the_app_saves
        out = self.run_script("--apply")
        tags, _captions, _raw, mtime = self.rows()[self.files["stale_keywords"]]
        self.assertEqual(saved_tags, tags, "the app's save was overwritten with the older read")
        self.assertEqual(2000000.0, mtime)
        self.assertIn("changed after this run read them: 1", out)

    def test_the_exiftool_asked_for_is_the_one_used(self):
        used = []
        real = self.fake_batch_read

        def recording(extractor, paths, db_path=None):
            used.append(extractor.exiftool_path)
            return real(extractor, paths, db_path)

        self.fake_batch_read = recording
        self.run_script("--exiftool", r"C:\Tools\exiftool.exe")
        self.assertEqual({r"C:\Tools\exiftool.exe"}, set(used))

    def test_a_person_named_only_on_a_face_stays_in_people(self):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO faces (photo_path, box, name, name_source) VALUES (?, '[]', 'Rowan Thackeray', 'manual')",
                     (self.files["stale_keywords"],))
        conn.commit()
        conn.close()
        self.run_script("--apply")
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            people = json.loads(conn.execute("SELECT people FROM photos WHERE path = ?",
                                             (self.files["stale_keywords"],)).fetchone()[0])
        finally:
            conn.close()
        self.assertIn("Rowan Thackeray", people)

    def test_a_file_that_cannot_be_read_is_left_alone_and_counted(self):
        self.truth[self.files["stale_keywords"]] = {}
        before = self.rows()[self.files["stale_keywords"]]
        out = self.run_script("--apply")
        self.assertEqual(before, self.rows()[self.files["stale_keywords"]])
        self.assertIn("could not be read (left alone): 1", out)


if __name__ == "__main__":
    unittest.main()

"""refresh_rows_from_files: a row saved during the run keeps the save, and more cases.

- A row the app saved after this run read the file was overwritten with the older read:
  each row must still have what the run found before it read the files, or it is
  skipped, and reported, and the rest are written (phase 7.5's journal: the whole change
  was refused, and on a library in use a run might never finish); a second run reads it
  again.
- The script printed no errors: a failed write crashed on a change never recorded, and
  a failed rebuild of the people and dates reported success. It prints them, and exits
  non-zero.
- It read with whatever ExifTool was on PATH, not the one config.ini names; --exiftool
  chooses, and the configured one is the default.
- Two cases the tests did not cover: a person named only on a face stays in the row's
  people, and a file that cannot be read is left alone and counted.
"""
import contextlib
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_refresh_rows_from_files as base  # noqa: E402
from test_refresh_rows_from_files import refresh  # noqa: E402
from face_rows import people_of  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.store import db, journal  # noqa: E402


class RefreshGuards(base.RefreshRowsFromFiles):
    def test_a_row_saved_during_the_run_keeps_the_save(self):
        saved_tags = ["Activity/Rowing"]
        real = self.fake_batch_read

        def read_then_the_app_saves(extractor, paths, people=None):
            records = real(extractor, paths, people)
            conn = db.connect(self.db)
            conn.execute("UPDATE photos SET tags = ?, mtime = 2000000.0 WHERE path = ?",
                         (json.dumps(saved_tags), self.files["stale_keywords"]))
            conn.commit()
            conn.close()
            return records

        self.fake_batch_read = read_then_the_app_saves
        before = self.rows()
        out = self.run_script("--apply")
        rows = self.rows()
        tags, _captions, _raw, mtime = rows[self.files["stale_keywords"]]
        self.assertEqual(saved_tags, tags, "the app's save was overwritten with the older read")
        self.assertEqual(2000000.0, mtime)
        saved = self.photo_id(self.files["stale_keywords"])
        self.assertIn("Skipped photos %d: not what the plan read: mtime, tags changed" % saved, out)
        self.assertIn("left for the next run: 1", out)
        # The rest were written, and recorded as one change without the skipped row.
        self.assertIn("rows changed from their files: 2", out)
        self.assertIn("rows with repeated captions removed: 1", out)
        self.assertNotEqual(before[self.files["garbled"]], rows[self.files["garbled"]])
        change = int(re.search(r"Recorded as change (\d+)", out).group(1))
        keys = journal.history(self.db, change_id=change)[0]["keys"]["photos"]
        self.assertEqual(sorted([self.photo_id(self.files[n])] for n in ("garbled", "stale_stat", "twice")),
                         sorted(keys))
        # And it is undone like any change: the saved row keeps its save.
        journal.undo(self.db, change)
        after_undo = self.rows()
        for name in ("garbled", "stale_stat", "twice", "fine"):
            self.assertEqual(before[self.files[name]], after_undo[self.files[name]], name)
        self.assertEqual(saved_tags, after_undo[self.files["stale_keywords"]][0])

    def photo_id(self, path):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            where, params = paths.sql_equals("path", path)
            return conn.execute("SELECT id FROM photos WHERE " + where, params).fetchone()[0]
        finally:
            conn.close()

    def run_for_code(self, *extra):
        """(exit code, output) of the script."""
        self.read = []
        with mock.patch("tagpup.files.metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=self.fake_batch_read), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = refresh.main(["--db", self.db, *extra])
        return code, out.getvalue()

    def test_a_failed_write_is_printed_and_exits_non_zero(self):
        before = self.rows()
        with mock.patch.object(journal, "_write", side_effect=RuntimeError("disk I/O error")):
            code, out = self.run_for_code("--apply")
        self.assertEqual(1, code, out)
        self.assertIn("FAILED: the write: RuntimeError: disk I/O error", out)
        self.assertIn("Nothing was recorded", out)
        self.assertEqual(before, self.rows())

    def test_a_failed_rebuild_is_printed_and_exits_non_zero(self):
        with mock.patch.object(journal, "_derive", side_effect=RuntimeError("database is locked")):
            with self.assertLogs("tagpup.store.journal", "ERROR"):
                code, out = self.run_for_code("--apply")
        self.assertEqual(1, code, out)
        self.assertRegex(out, r"Recorded as change \d+")
        self.assertIn("FAILED: the people and dates of the photos it touched: not rebuilt yet", out)

    def test_the_exiftool_asked_for_is_the_one_used(self):
        used = []
        real = self.fake_batch_read

        def recording(extractor, paths, people=None):
            used.append(extractor.exiftool_path)
            return real(extractor, paths, people)

        self.fake_batch_read = recording
        self.run_script("--exiftool", r"C:\Tools\exiftool.exe")
        self.assertEqual({r"C:\Tools\exiftool.exe"}, set(used))

    def test_a_person_named_only_on_a_face_stays_in_people(self):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES ((SELECT id FROM photos WHERE path = ?), '[]', 'Rowan Thackeray', 'manual')",
                     (self.files["stale_keywords"],))
        conn.commit()
        conn.close()
        self.run_script("--apply")
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            people = people_of(conn, self.files["stale_keywords"])
        finally:
            conn.close()
        self.assertIn("Rowan Thackeray", people)

    def test_a_row_missing_a_face_name_is_found_and_fixed(self):
        # "fine" agrees with its file in every other way; only its people are short.
        conn = db.connect(self.db)
        conn.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES ((SELECT id FROM photos WHERE path = ?), '[]', 'Imogen Vale', 'manual')",
                     (self.files["fine"],))
        conn.commit()
        conn.close()
        out = self.run_script("--apply")
        self.assertIn("people incomplete", out)
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            people = people_of(conn, self.files["fine"])
        finally:
            conn.close()
        self.assertIn("Imogen Vale", people)

    def test_a_file_that_cannot_be_read_is_left_alone_and_counted(self):
        self.truth[self.files["stale_keywords"]] = {}
        before = self.rows()[self.files["stale_keywords"]]
        out = self.run_script("--apply")
        self.assertEqual(before, self.rows()[self.files["stale_keywords"]])
        self.assertIn("could not be read (left alone): 1", out)


if __name__ == "__main__":
    unittest.main()

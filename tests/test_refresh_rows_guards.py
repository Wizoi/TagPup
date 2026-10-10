"""refresh_rows: a row saved during the run keeps the save, and more cases.

- A row the app saved after this run read the file was overwritten with the older read:
  each row must still have what the run found before it read the files, or it is
  skipped, and reported, and the rest are written (phase 7.5's journal: the whole change
  was refused, and on a library in use a run might never finish); a second run reads it
  again.
- A failed write crashed on a change never recorded, and a failed rebuild of the people
  and dates reported success. The Result carries them as errors.
- It reads with the ExifTool it is given.
- Two cases the tests did not cover: a person named only on a face stays in the row's
  people, and a file that cannot be read is left alone and counted.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_refresh_rows as base  # noqa: E402
from face_rows import people_of  # noqa: E402

from tagpup.services import maintenance  # noqa: E402
from tagpup.store import db, journal  # noqa: E402


class RefreshGuards(base.RefreshFixture):
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
        result = self.refresh(apply=True)
        rows = self.rows()
        tags, _captions, _raw, mtime = rows[self.files["stale_keywords"]]
        self.assertEqual(saved_tags, tags, "the app's save was overwritten with the older read")
        self.assertEqual(2000000.0, mtime)
        saved = self.photo_id(self.files["stale_keywords"])
        self.assertEqual([("photos %d" % saved, "not what the plan read: mtime, tags changed")], result.skipped)
        # The rest were written, and recorded as one change without the skipped row.
        self.assertEqual({"from_files": 3, "captions": 1}, result.details["changed"])
        self.assertNotEqual(before[self.files["garbled"]], rows[self.files["garbled"]])
        change = result.details["change"]
        keys = journal.history(self.db, change_id=change)[0]["keys"]["photos"]
        self.assertEqual(sorted([self.photo_id(self.files[n])] for n in ("garbled", "stale_stat", "twice", "never_read")),
                         sorted(keys))
        # And it is undone like any change: the saved row keeps its save.
        journal.undo(self.db, change)
        after_undo = self.rows()
        for name in ("garbled", "stale_stat", "twice", "fine"):
            self.assertEqual(before[self.files[name]], after_undo[self.files[name]], name)
        self.assertEqual(saved_tags, after_undo[self.files["stale_keywords"]][0])

    def test_a_failed_write_is_an_error_and_nothing_is_recorded(self):
        before = self.rows()
        with mock.patch.object(journal, "_write", side_effect=RuntimeError("disk I/O error")):
            result = self.refresh(apply=True)
        self.assertIn("FAILED: the write: RuntimeError: disk I/O error", maintenance.failed(result))
        self.assertIn("Nothing was recorded", maintenance.recorded(result, self.db))
        self.assertEqual(before, self.rows())

    def test_a_failed_rebuild_is_an_error_beside_the_change_that_was_recorded(self):
        with mock.patch.object(journal, "_derive", side_effect=RuntimeError("database is locked")):
            with self.assertLogs("tagpup.store.journal", "ERROR"):
                result = self.refresh(apply=True)
        self.assertIsInstance(result.details["change"], int)
        self.assertTrue(any(line.startswith("FAILED: the people and dates of the photos it touched: not rebuilt yet")
                            for line in maintenance.failed(result)), maintenance.failed(result))

    def test_the_exiftool_asked_for_is_the_one_used(self):
        used = []
        real = self.fake_batch_read

        def recording(extractor, paths, people=None):
            used.append(extractor.exiftool_path)
            return real(extractor, paths, people)

        self.fake_batch_read = recording
        self.refresh(exiftool=r"C:\Tools\exiftool.exe")
        self.assertEqual({r"C:\Tools\exiftool.exe"}, set(used))

    def test_a_person_named_only_on_a_face_stays_in_people(self):
        conn = db.connect(self.db)
        conn.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES ((SELECT id FROM photos WHERE path = ?), '[]', 'Rowan Thackeray', 'manual')",
                     (self.files["stale_keywords"],))
        conn.commit()
        conn.close()
        self.refresh(apply=True)
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
        result = self.refresh(apply=True)
        self.assertEqual(1, result.details["counts"]["reasons"]["people incomplete"])
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            people = people_of(conn, self.files["fine"])
        finally:
            conn.close()
        self.assertIn("Imogen Vale", people)

    def test_a_file_that_cannot_be_read_is_left_alone_and_counted(self):
        self.truth[self.files["stale_keywords"]] = {}
        before = self.rows()[self.files["stale_keywords"]]
        result = self.refresh(apply=True)
        self.assertEqual(before, self.rows()[self.files["stale_keywords"]])
        self.assertEqual(1, result.details["counts"]["unreadable"])


if __name__ == "__main__":
    unittest.main()

"""The clips indexed before a clip's XMP date was read first are dated again from their own rows (scripts/rederive_clip_dates.py;
DECISIONS 2026-11-08, A5 (D) REVISED): photo_index held 80 such clip rows, Adobe's wrapping, the other two libraries none.

A library in a home of its own holds three clips and a photo, each recorded as the indexer records its read
(tests/photo_rows.add_read) while the old rule held -- a clip dated by its QuickTime dates alone (dates.CLIP_XMP_DATE_FIELDS
empty): a clip holding a Date Taken saved into its XMP:DateTimeOriginal, an Adobe clip whose zoned xmp:CreateDate is
of another year, a clip holding QuickTime dates only (already right), and a photo. The script's dry run counts them and
writes nothing; --apply dates the two apart as one change of the journal, its undo puts back the dates as they were, a
second run finds nothing, and a clip saved between the plan and the write is skipped, not overwritten. On the code before
the script (2076471d) every test here fails: there is nothing to run.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import photo_rows  # noqa: E402
import rederive_clip_dates  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import dates  # noqa: E402
from tagpup.services import journal, refresh_rows  # noqa: E402

#: A Date Taken saved into a clip's XMP, the same year as its QuickTime date.
SAVED = {"QuickTime:CreateDate": "2024:06:01 12:00:00", "CreateDate": "2024:06:01 12:00:00",
         "XMP:DateTimeOriginal": "2024:05:31 10:00:00"}
#: Adobe's wrapping: an xmp:CreateDate with its zone, of another year than the QuickTime date.
WRAPPED = {"QuickTime:CreateDate": "2019:06:01 12:00:00", "CreateDate": "2019:06:01 12:00:00",
           "XMP:CreateDate": "2021:06:01 10:00:00+02:00"}
#: A camera's clip: QuickTime dates only.
PLAIN = {"QuickTime:CreateDate": "2022:08:14 09:00:00", "CreateDate": "2022:08:14 09:00:00"}


class ClipsStoredByTheOldRule(unittest.TestCase):

    def setUp(self):
        self.vl = ViewLibrary(self, "library")
        with mock.patch.object(dates, "CLIP_XMP_DATE_FIELDS", ()):
            self.saved = self.vl.photo("Coast", "Swim.mov", fields=SAVED)
            self.wrapped = self.vl.photo("Coast", "Ferry.mp4", fields=WRAPPED)
            self.plain = self.vl.photo("Coast", "Pier.mp4", fields=PLAIN)
            self.photo = self.vl.photo("Coast", "dawn.jpg", "2023:03:03 08:00:00")
        self.stored = self.dated()

    def dated(self):
        return {photo_id: (taken, year) for photo_id, taken, year in self.vl.rows("SELECT id, taken, year FROM photos")}

    def journaled(self):
        return self.vl.rows("SELECT (SELECT COUNT(*) FROM changes), (SELECT COUNT(*) FROM change_rows)")[0]

    def whole_rows(self):
        return self.vl.rows("SELECT id, path, taken, year, raw_metadata, tags, captions, mtime, size FROM photos ORDER BY id")

    def run_script(self, *apply):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = rederive_clip_dates.main(["--db", self.vl.path, *apply])
        return code, said.getvalue()

    def test_the_old_rule_stored_the_quicktime_dates(self):
        self.assertEqual(2019, self.stored[self.wrapped][1], "as the 80 live rows hold it")
        self.assertNotEqual("2024:05:31 10:00:00", self.stored[self.saved][0])

    def test_a_dry_run_counts_the_clips_apart_and_writes_nothing(self):
        journaled, rows = self.journaled(), self.whole_rows()
        code, said = self.run_script()
        self.assertEqual(0, code, said)
        self.assertIn("library: clips 3, apart 2, year 1.", said)
        self.assertIn("Rehearsed: 2 row(s) written and undone, and the undo restored every one exactly.", said)
        self.assertEqual((journaled, rows), (self.journaled(), self.whole_rows()), "the journal and the rows untouched")

    def test_apply_dates_the_two_apart_once_and_its_undo_puts_back_the_old_dates(self):
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Written: 2 row(s).", said)
        self.assertIn("Read again: 0 clip(s) still apart.", said)
        now = self.dated()
        self.assertEqual(("2024:05:31 10:00:00", 2024), now[self.saved], "the Date Taken saved into its XMP")
        self.assertEqual(("2021:06:01 10:00:00+02:00", 2021), now[self.wrapped], "Adobe's date, its year with it")
        self.assertEqual(self.stored[self.plain], now[self.plain], "a clip already right is left")
        self.assertEqual(self.stored[self.photo], now[self.photo], "a photo is no clip")

        journaled, rows = self.journaled(), self.whole_rows()
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("library: clips 3, apart 0, year 0.", said)
        self.assertIn("Written: 0 row(s).", said)
        self.assertEqual((journaled, rows), (self.journaled(), self.whole_rows()), "run again, it changes nothing")

        change, = self.vl.rows("SELECT id FROM changes WHERE operation = ?", refresh_rows.REDATE_CLIPS_OPERATION)[0]
        undone = journal.undo(self.vl.library, change, apply=True)
        self.assertEqual((True, 2), (undone.ok, undone.changed), undone.message())
        self.assertEqual(self.stored, self.dated(), "the dates as they were, exactly")

    def test_a_clip_saved_between_the_plan_and_the_write_is_skipped_not_overwritten(self):
        clips_apart = refresh_rows.clips_apart
        resaved = dict(SAVED, **{"XMP:DateTimeOriginal": "2024:05:30 09:00:00"})

        def a_save_meanwhile(library):
            planned = clips_apart(library)
            # The page saves another Date Taken into the clip: the index records it as it records every write.
            photo_rows.add_read(self.vl.conn, self.vl.path_of(self.saved), resaved)
            self.vl.conn.commit()
            return planned

        with mock.patch.object(refresh_rows, "clips_apart", side_effect=a_save_meanwhile):
            result = refresh_rows.redate_clips(self.vl.library, apply=True)
        self.assertEqual((True, 1, 1), (result.ok, result.changed, len(result.skipped)), result.message())
        now = self.dated()
        self.assertEqual(("2024:05:30 09:00:00", 2024), now[self.saved], "the save kept")
        self.assertEqual(("2021:06:01 10:00:00+02:00", 2021), now[self.wrapped])
        self.assertEqual(({"clips": 3, "apart": 0, "year": 0}, []), refresh_rows.clips_apart(self.vl.library))


if __name__ == "__main__":
    unittest.main()

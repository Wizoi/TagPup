"""The one-off that writes into each clip's XMP the Date Taken the library reads from its file name
(scripts/date_undated_clips.py; tagpup.services.tagging.date_clips_by_name), as the owner runs it on a library with
TagPup closed: a dry run first, then --apply, then a dry run again that finds none left (scenario C5; deleted with the
script once its dry run says 0 left on the three libraries).

The dry run reads the index alone, names by id the clips whose name holds no date either, and writes nothing. --apply
is refused while an app runs; it patches each clip's XMP:DateTimeOriginal through the journaled clip save, reports what
the journal holds written, and History's Undo takes it back off (the library dating the clip by its name again). A clip
saved by hand between the listing and the write is left alone and said, never written over. Its --apply first dates the
rows again: a clip dated by its name gets its `taken` and its year, and one with no date in its name loses the year its
folder gave it (#2941), so the library view counts it undated; an undated photo of that folder keeps the folder's year.

A library of its own (web_client), real ffmpeg clips with zeroed QuickTime dates and the real ExifTool
(tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip); the owner's libraries are never opened. The script
did not exist before this file. Fictional names only.
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
import date_undated_clips  # noqa: E402
import test_a_clip_dated_by_its_name as by_name  # noqa: E402
import test_a_clip_dated_through_the_journal as dated  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.core.result import Refused, Result  # noqa: E402
from tagpup.files import images, in_place  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import tagging  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
TOOLS = tagged.TOOLS


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class DatingClipsByTheirNames(tagged.ALibraryHoldingAClip):
    """A clip named for 2019-07-04 and one whose name holds no date, both of zeroed QuickTime dates."""

    taken = dated.AClipDatedThroughTheJournal.taken
    save_date_from_the_panel = dated.AClipDatedThroughTheJournal.save_date_from_the_panel
    read = dated.AClipDatedThroughTheJournal.read
    undo = dated.AClipDatedThroughTheJournal.undo

    def setUp(self):
        super().setUp()
        named = os.path.join(self.folder, by_name.CLIP_NAME)
        os.replace(self.clip_path, named)
        self.clip_path = named
        # In a folder named for a day, as 2 of the 11 photo_index clips with no date in their name are.
        dated_folder = os.path.join(self.vl.pictures, by_name.FOLDER)
        os.makedirs(dated_folder)
        self.loose_path = by_name.zeroed_clip(os.path.join(dated_folder, "dock.mp4"))
        self.quay_path = os.path.join(dated_folder, "quay.jpg")
        tagged.make_jpeg(self.quay_path, size=(32, 32))
        self.clip, self.loose = self.index(self.clip_path, self.loose_path)
        # No app is running for the test (launcher.running reads the user's own servers folder).
        running = mock.patch.object(date_undated_clips.launcher, "running", return_value=[])
        self.running = running.start()
        self.addCleanup(running.stop)

    def run_it(self, *apply):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = date_undated_clips.main(["--library", self.vl.library.path, *apply])
        return code, said.getvalue()

    def changes(self):
        return self.vl.rows("SELECT id FROM changes WHERE operation = ?", tagging.DATE_CLIPS)

    def xmp_date(self, path):
        return self.read(path, ["XMP:DateTimeOriginal"])["XMP:DateTimeOriginal"]

    def test_the_dry_run_counts_from_the_index_names_the_undated_by_id_and_writes_nothing(self):
        before = self.read_bytes(self.clip_path), self.read_bytes(self.loose_path)
        code, said = self.run_it()
        self.assertEqual(0, code, said)
        self.assertIn("1 clip(s) with no date of their own dated by their names; 1 left undated: no date in its name: "
                      "clips %d." % self.loose, said)
        self.assertIn("A dry run: nothing was read or written.", said)
        self.assertEqual(before, (self.read_bytes(self.clip_path), self.read_bytes(self.loose_path)))
        self.assertEqual([], self.changes())

    def test_apply_writes_the_names_date_into_its_xmp_a_second_run_finds_none_left_and_history_undoes_it(self):
        before = self.read_bytes(self.clip_path)
        code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Dated: wrote 1 clip file(s).", said)
        self.assertIn("Left to date: 0.", said)
        self.assertEqual(by_name.NAMED, self.xmp_date(self.clip_path), "the name's date, written into its XMP")
        self.assertIsNone(self.xmp_date(self.loose_path), "a clip whose name holds no date is not written")
        self.assertEqual(["done"], self.change_states(self.clip))
        self.assertEqual(by_name.NAMED, self.taken(self.clip))

        code, said = self.run_it()
        self.assertIn("0 clip(s) with no date of their own dated by their names; 1 left undated", said)

        (change,), = self.changes()
        self.undo(change)
        self.assertIsNone(self.xmp_date(self.clip_path), "History's Undo takes the date back off")
        # A clip holding no XMP is given a box of it at its end by its first patch; the undo empties the box and leaves
        # it (as a panel save's undo does): every byte the clip held before is as it was.
        self.assertEqual(before, self.read_bytes(self.clip_path)[:len(before)], "the clip's own bytes as they were")
        self.assertEqual(by_name.NAMED, self.taken(self.clip), "and the library dates it by its name again")
        code, said = self.run_it()
        self.assertIn("1 clip(s) with no date of their own dated by their names", said)

    def test_a_clip_dated_by_hand_between_the_listing_and_the_write_is_left_alone_and_said(self):
        listed, calls = tagging.clips_dated_by_name, []

        def then_saved_by_hand(library):
            found = listed(library)
            calls.append(library)
            if len(calls) == 1:
                reply = self.save_date_from_the_panel(self.clip)
                self.assertEqual(200, reply.status_code, reply.get_json())
            return found

        with mock.patch.object(tagging, "clips_dated_by_name", side_effect=then_saved_by_hand):
            code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Dated: wrote 0 clip file(s). Left alone: 1 %s" % tagging.DATED_SINCE, said)
        self.assertIn("Left to date: 0.", said)
        self.assertIn("Nothing was recorded: no file was written.", said)
        self.assertEqual(dated.SAVED, self.xmp_date(self.clip_path), "the date saved by hand is kept")

    def indexed_before_the_clips_were_dated_by_name(self):
        """The rows as photo_index holds its 680 clips with no date of their own: indexed before the fallback, `taken`
        NULL and `year` the one the indexer of the day read from the names, the file's or else a folder's (every one of the
        680 holds one, counted read-only 2026-11-13): the clip named for 2019-07-04 2019, the one with no date in its name
        its folder's 2021. The name's date in no column (the panel and the folder scan show it; the library view reads the
        columns). A photo taken in 2023 beside them, and an undated photo in the clip's folder of 2021."""
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo_path], tags={"EXIF:DateTimeOriginal": dated.PHOTO_TAKEN}, params=["-overwrite_original"])
        self.photo, self.quay = self.index(self.photo_path, self.quay_path)
        for photo_id, year in ((self.clip, 2019), (self.loose, 2021)):
            self.vl.conn.execute("UPDATE photos SET taken = NULL, year = ? WHERE id = ?", (year, photo_id))
        self.vl.conn.commit()

    def library_view(self):
        """(the ids by Date Taken, the ids of 2019-07, the navigator's undated tally, its {year: (count, months)}, the ids
        of the year 2021 as the year filter gives them), by the routes."""
        got = self.client.get("/library/api/library/ids", query_string={"kind": "all", "order": "taken"})
        month = self.client.get("/library/api/library/ids", query_string={"kind": "month", "value": "2019-07"})
        year = self.client.get("/library/api/library/ids", query_string={"kind": "year", "value": "2021"})
        navigator = self.client.get("/library/api/library/navigator", query_string={"section": "dates"})
        for reply in (got, month, year, navigator):
            self.assertEqual(200, reply.status_code, reply.data)
        years = {each["year"]: (each["count"], [m["month"] for m in each["months"]])
                 for each in navigator.get_json()["years"]}
        return (got.get_json()["ids"], month.get_json()["ids"], navigator.get_json()["undated"], years,
                year.get_json()["ids"])

    def test_a_row_indexed_before_the_fallback_is_dated_by_apply_and_a_clip_with_no_date_in_its_name_loses_its_folders_year(self):
        self.indexed_before_the_clips_were_dated_by_name()
        as_indexed = ([self.photo, self.clip, self.loose, self.quay], [], 0,
                      {2019: (1, []), 2021: (2, []), 2023: (1, ["2023-05"])}, [self.loose, self.quay])
        self.assertEqual(as_indexed, self.library_view(),
                         "the views read the columns: neither clip in a month, the loose one under its folder's year")
        code, said = self.run_it()
        self.assertEqual(0, code, said)
        self.assertIn("1 clip row(s) hold no Date Taken yet", said, "the dry run says how many it would date")
        self.assertIn("1 clip row(s) with no Date Taken hold a year", said, "and on how many it would clear the year")
        self.assertEqual(as_indexed, self.library_view(), "and wrote nothing")

        code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Re-dated 1 clip row(s)", said)
        self.assertIn("Year cleared on 1 clip row(s) with no Date Taken.", said)
        self.assertEqual(([self.clip, self.photo, self.loose, self.quay], [self.clip], 1,
                          {2019: (1, ["2019-07"]), 2021: (1, []), 2023: (1, ["2023-05"])}, [self.quay]),
                         self.library_view(), "the clip in its month and its place by the date; the clip with no date in "
                         "its name undated, out of its folder's year, which keeps the undated photo")
        self.assertEqual([(None, None), (None, 2021)],
                         [tuple(row) for row in self.vl.rows("SELECT taken, year FROM photos WHERE id IN (?, ?) ORDER BY id",
                                                             self.loose, self.quay)])
        code, said = self.run_it("--apply")
        self.assertIn("Re-dated 0 clip row(s)", said, "idempotent: nothing left to date again")
        self.assertIn("Year cleared on 0 clip row(s)", said, "nor a year to clear")

    def test_a_clip_the_write_leaves_is_dated_in_the_views_all_the_same(self):
        """A share away, or a writer that refuses: the file is not written, the row is dated (counted from the row)."""
        self.indexed_before_the_clips_were_dated_by_name()
        with mock.patch.object(tagging.file_changes, "write_fields", return_value=Result(attempted=1)):
            code, said = self.run_it("--apply")
        self.assertIn("Re-dated 1 clip row(s)", said)
        self.assertEqual(([self.clip, self.photo, self.loose, self.quay], [self.clip], 1,
                          {2019: (1, ["2019-07"]), 2021: (1, []), 2023: (1, ["2023-05"])}, [self.quay]),
                         self.library_view())
        self.assertIsNone(self.xmp_date(self.clip_path), "the file was not written")
        self.assertEqual([], self.changes())

    def test_a_clip_the_writer_refuses_is_named_by_its_id_and_sentence_and_by_its_real_cause(self):
        refusals = (images.CLIP_NOT_PATCHED, in_place.OPEN_ELSEWHERE)
        for sentence in refusals:
            with self.subTest(sentence=sentence), mock.patch.object(in_place, "patch_clip", side_effect=Refused(sentence)):
                code, said = self.run_it("--apply")
                self.assertEqual(1, code, said)
                cause = (1, 0) if sentence == images.CLIP_NOT_PATCHED else (0, 1)
                self.assertIn("Refused by the writer: 1 (%d its XMP cannot be patched where it is, %d open in another "
                              "program)." % cause, said)
                self.assertIn("Clip %d: %s" % (self.clip, sentence), said)
                self.assertNotIn(self.clip_path, said, "an id and a sentence, never a path")

    def test_a_ctrl_c_after_the_write_returned_does_not_say_the_write_was_stopped(self):
        listed, calls = tagging.clips_dated_by_name, []

        def interrupted_in_the_recount(library):
            calls.append(library)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return listed(library)

        with mock.patch.object(tagging, "clips_dated_by_name", side_effect=interrupted_in_the_recount):
            code, said = self.run_it("--apply")
        self.assertEqual(130, code)
        self.assertIn("the write had finished (1 clip(s) written", said)
        self.assertNotIn("stopped:", said)
        self.assertNotIn("stopped again", said)
        self.assertEqual(by_name.NAMED, self.xmp_date(self.clip_path), "and it had been written")

    def test_apply_is_refused_while_an_app_runs(self):
        self.running.return_value = [{"pid": 4242}]
        before = self.read_bytes(self.clip_path)
        with mock.patch.object(date_undated_clips.snapshots, "before_a_long_write") as snapshot:
            code, said = self.run_it("--apply")
        self.assertEqual((1, date_undated_clips.APP_RUNNING % 1), (code, said.strip()))
        snapshot.assert_not_called()
        self.assertEqual(before, self.read_bytes(self.clip_path))
        self.assertEqual([], self.changes())


if __name__ == "__main__":
    unittest.main()

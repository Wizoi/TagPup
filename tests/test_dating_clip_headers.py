"""The one-off that writes each clip's Date Taken into its QuickTime headers (scripts/date_clip_headers.py;
tagpup.services.tagging.date_clip_headers), as the owner runs it with TagPup closed: a dry run, --limit 1, then the rest,
then a dry run that finds none left (scenario C5; deleted with the script once its dry run says 0 left).

Listed: a clip whose XMP holds a Date Taken and whose headers are zero (A), and a clip the owner's list names whose header
holds another time (B, its XMP date zoned: the headers get the digits, the XMP keeps the zone). Never written: a clip the
list keeps (nine hours off: a camera's true UTC) and one no list names. Each is saved through the journaled clip save as
one change History undoes; a clip whose date changed since the listing, whose header holds the date already, whose file
is gone, or whose share stops answering is left alone and counted; a clip dated outside 1904..2040 is refused and
counted; Ctrl+C keeps what was written and a second run finishes. A library of its own holding its clips under one root,
as each of the three live libraries holds every clip (2,909 of photo_index's, 5 of kr-track's; counted 2026-11-13), the
list's folders relative to it; clips laid out as real ones are (tests/fixtures/clip_headers.json), the real ExifTool; a
synthetic list, never the owner's. The script did not exist before this file. Fictional names only.
"""
import contextlib
import csv
import io
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import date_clip_headers  # noqa: E402
import roots_library  # noqa: E402
import test_a_clip_dated_through_the_journal as dated  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values, in_place  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import tagging  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
ZONED = "2018:03:02 09:15:00-07:00"
OTHER = "2021:06:01 10:00:00"
#: A Date Taken no version 0 header holds (clip_patch.holds_date): after early 2040.
OUTSIDE = "2041:05:01 10:00:00"
#: The folder of the clip the list dates, relative to the root, as the list spells it.
NESTED = "Trips/Shore"
COLUMNS = ("file_name", "folder", "xmp_date_taken", "header_raw", "header_as_utc_shown_local", "difference_hours",
           "keys_creationdate_present", "software")


@unittest.skipIf(EXIFTOOL is None or tagged.TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class DatingClipHeaders(tagged.ALibraryHoldingAClip):
    read = dated.AClipDatedThroughTheJournal.read
    undo = dated.AClipDatedThroughTheJournal.undo
    save_date_from_the_panel = dated.AClipDatedThroughTheJournal.save_date_from_the_panel

    def setUp(self):
        super().setUp()
        self.zero = self.dated_clip(dated.ZERO_HEADERS, "slipway.mp4", dated.SAVED)       # A
        self.named = self.dated_clip(dated.THREE_TRACKS, "harbour.mov", ZONED, NESTED)   # B
        self.kept = self.dated_clip(dated.THREE_TRACKS, "quay.mov", OTHER)               # listed, nine hours off
        self.unnamed = self.dated_clip(dated.THREE_TRACKS, "dock.mov", OTHER)            # no list names it
        self.clips = (self.zero, self.named, self.kept, self.unnamed)
        roots_library.convert_to_root(self.vl.library.path, self.vl.pictures)
        self.ids = self.index(*self.clips)
        held = [row for row, in self.vl.rows("SELECT path FROM photos ORDER BY id")]
        self.assertTrue(all(row.startswith(paths.ROOT_MARK) for row in held), "the clips are held under the root: %r" % held)
        self.list = os.path.join(os.path.dirname(self.vl.library.path), "disagreements.csv")
        with open(self.list, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for name, folder, hours in (("harbour.mov", NESTED, "-0.5"), ("quay.mov", "Harbour", "-9.0"),
                                        ("pier.mov", "Harbour", "10.0"), ("gone.mp4", "Harbour", "1.0")):
                writer.writerow((name, folder, "", "", "", hours, "False", ""))
        running = mock.patch.object(date_clip_headers.launcher, "running", return_value=[])
        self.running = running.start()
        self.addCleanup(running.stop)

    def dated_clip(self, shape, name, date, folder="Harbour"):
        """A clip laid out as the real clips of `shape` in `folder` (relative to the root), its XMP dated `date`."""
        path = os.path.join(self.vl.pictures, *folder.split("/"), name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(dated.clip_of(shape))
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([path], tags={"XMP:DateTimeOriginal": date}, params=["-overwrite_original"])
        return path

    def run_it(self, *more):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = date_clip_headers.main(["--library", self.vl.library.path, "--csv", self.list, *more])
        return code, said.getvalue()

    def files(self):
        return [self.read_bytes(path) for path in self.clips]

    def changes(self):
        return [change for change, in self.vl.rows("SELECT id FROM changes WHERE operation = ? ORDER BY id",
                                                   tagging.DATE_HEADERS)]

    def headers(self, path, date):
        written = self.read_bytes(path)
        return dated.creation_times(written) == dated.saved_times(written, date)

    def test_a_dry_run_then_limit_one_then_the_rest_dates_a_and_b_alone_and_history_undoes_it(self):
        before = self.files()
        code, said = self.run_it()
        self.assertEqual(0, code, said)
        self.assertIn("2 clip(s) whose headers do not hold their XMP Date Taken: 1 zero or absent (A), 1 named in the "
                      "list (B).", said)
        self.assertIn("The list names 2 clip(s) to date and 2 that keep their header (8 to 10 hours: true UTC); 1 of its "
                      "names are no clip of this library.", said)
        self.assertIn("\n  @%s/Harbour: 1\n  @%s/%s: 1\n" % (roots_library.NAME, roots_library.NAME, NESTED), said,
                      "each folder relative to the root")
        self.assertEqual((before, []), (self.files(), self.changes()), "a dry run writes nothing")

        code, said = self.run_it("--apply", "--limit", "1")
        self.assertIn("Dated: wrote 1 clip file(s).", said)
        self.assertIn("Left to date: 1.", said)
        self.assertTrue(self.headers(self.zero, dated.SAVED), "the first listed, by id: the A clip")
        code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Dated: wrote 1 clip file(s).", said)
        self.assertIn("Left to date: 0.", said)
        self.assertTrue(self.headers(self.named, "2018:03:02 09:15:00"), "a zoned date's digits in every header")
        self.assertEqual(ZONED, self.read(self.named, ["XMP:DateTimeOriginal"])["XMP:DateTimeOriginal"], "the XMP's zone kept")
        self.assertEqual(before[2:], self.files()[2:], "the clip the list keeps and the one it does not name: untouched")
        self.assertIn("0 clip(s) whose headers do not hold", self.run_it()[1])

        self.undo(self.changes()[-1])
        self.assertEqual(dated.quicktime_date_boxes(before[1]), dated.quicktime_date_boxes(self.read_bytes(self.named)),
                         "History's Undo gives each header box its own bytes back")

    def test_a_ctrl_c_keeps_what_was_written_and_a_second_run_finishes(self):
        patch, calls = in_place.patch_clip, []

        def stopped_at_the_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return patch(*args, **kwargs)

        with mock.patch.object(in_place, "patch_clip", side_effect=stopped_at_the_second):
            code, said = self.run_it("--apply")
        self.assertEqual(130, code, said)
        self.assertIn("stopped: 1 clip(s) written", said)
        code, said = self.run_it("--apply")
        self.assertIn("Dated: wrote 1 clip file(s).", said)
        self.assertIn("Left to date: 0.", said)

    def test_a_clip_dated_since_the_listing_or_holding_the_date_already_is_not_written_nor_counted_written(self):
        listed, calls = tagging.clips_to_date_headers, []

        def then_saved_by_hand(library, named=()):
            found = listed(library, named)
            calls.append(library)
            if len(calls) == 1:
                reply = self.save_date_from_the_panel(self.ids[0], "2024-09-01T08:00:00")
                self.assertEqual(200, reply.status_code, reply.get_json())
                # Another run's patch of the B clip since its row was read: its header holds the date already.
                field_values.write(None, self.named, {"XMP:DateTimeOriginal": [ZONED], "QuickTime:CreateDate": [ZONED[:19]],
                                                      "QuickTime:TrackCreateDate": [ZONED[:19]],
                                                      "QuickTime:MediaCreateDate": [ZONED[:19]]},
                                   saving=lambda record, before: None)
            return found

        with mock.patch.object(tagging, "clips_to_date_headers", side_effect=then_saved_by_hand):
            code, said = self.run_it("--apply")
        self.assertIn("Dated: wrote 0 clip file(s). Held it already: 1. Left alone: 1 %s" % tagging.REDATED_SINCE, said)
        self.assertIn("Nothing was recorded: no file was written.", said)
        self.assertTrue(self.headers(self.zero, "2024:09:01 08:00:00"), "the panel's date kept")

    def test_a_gone_file_or_a_share_that_stops_answering_is_left_alone_and_said(self):
        os.remove(self.zero)
        with mock.patch.object(in_place, "headers_held", side_effect=in_place.Away("the share did not answer")):
            code, said = self.run_it("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("1 unreadable or gone, 1 away (their network share did not answer)", said)
        self.assertIn("Left to date: 2.", said)
        self.assertEqual([], self.changes())

    def test_a_clip_dated_outside_1904_to_2040_is_refused_counted_and_never_written(self):
        outside = self.dated_clip(dated.ZERO_HEADERS, "lighthouse.mp4", OUTSIDE)
        self.index(outside)
        before = self.read_bytes(outside)
        code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("3 clip(s) whose headers do not hold their XMP Date Taken: 2 zero or absent (A)", said)
        self.assertIn("Refused, never written: 1 clip(s): %s" % field_values.clip_date_refused(OUTSIDE), said)
        self.assertIn("Dated: wrote 2 clip file(s).", said)
        self.assertIn("Left to date: 1.", said)
        self.assertEqual(before, self.read_bytes(outside), "never written")

    def test_apply_is_refused_while_an_app_runs(self):
        self.running.return_value = [{"pid": 4242}]
        before = self.files()
        with mock.patch.object(date_clip_headers.snapshots, "before_a_long_write") as snapshot:
            code, said = self.run_it("--apply")
        self.assertEqual((1, date_clip_headers.APP_RUNNING % 1), (code, said.strip()))
        snapshot.assert_not_called()
        self.assertEqual((before, []), (self.files(), self.changes()))


if __name__ == "__main__":
    unittest.main()

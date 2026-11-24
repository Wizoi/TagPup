"""A clip whose file holds no date is dated by the date in its file name, at midnight (owner, 2026-11-13; plan
docs/plans/clip-date-from-name.md; scenario C5).

What the owner lives through: a clip copied off a phone as `2019-07-04 jetty.mp4`, its QuickTime dates zeros (680 of
photo_index's 2,910 clips, 669 of them dated in their name, counted read-only 2026-11-13), in a folder named for another
day. The library dates it by its name: its row's `taken` and year, the library's order by Date Taken, the folder view's
order and the page's record give the one date (dates.date_taken, given the path), and the file is not touched. A real
date always wins: a clip whose QuickTime date is set keeps it, and a Date Taken saved from the panel is read before the
name, its undo from History bringing the name's date back. A folder's date never dates a clip (the 11 dated only in
their folder stay undated, for the owner), and a photo is never dated by its name. A clip with no Date Taken has no year
either, neither its folder's nor a year alone in its name, so the owner finds it among the undated (#2941); a photo
beside it keeps its folder's year. A clip is told from a photo by its kind (photos.kind, as the index gave it), never by
the fields its metadata holds: an older-format clip, a .avi holding no QuickTime date, is dated as every clip (#2952).

The one-off that writes the name's date into each such clip's XMP is tests/test_dating_clips_by_their_names.py.

Real ffmpeg clips (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip: its QuickTime dates zeros) and the
real ExifTool. Fictional names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import test_a_clip_dated_through_the_journal as dated  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.core import dates  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
TOOLS = tagged.TOOLS

#: The date the clip's name holds, as the library spells it; the folder it is in is named for another day.
NAMED = "2019:07:04 00:00:00"
CLIP_NAME = "2019-07-04 jetty.mp4"
FOLDER = "2021-05-01 Harbour"


def zeroed_clip(path, creation_time=None):
    """A real ffmpeg clip at `path` (made once and kept, tests/corpus.py): its QuickTime dates zeros, or
    `creation_time` (UTC) in each header."""
    stamp = ["-metadata", "creation_time=" + creation_time] if creation_time else []
    return corpus.clip(path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", *stamp], TOOLS.ffmpeg)


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipDatedByItsName(tagged.ALibraryHoldingAClip):
    """The clip named for 2019-07-04 beside a photo taken in 2023, in a folder named for 2021-05-01."""

    taken = dated.AClipDatedThroughTheJournal.taken
    order = dated.AClipDatedThroughTheJournal.order
    save_date_from_the_panel = dated.AClipDatedThroughTheJournal.save_date_from_the_panel
    change_of = dated.AClipDatedThroughTheJournal.change_of
    undo = dated.AClipDatedThroughTheJournal.undo

    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.vl.pictures, FOLDER)
        os.makedirs(self.folder)
        moved = os.path.join(self.folder, "pier.jpg")
        os.replace(self.photo_path, moved)
        self.photo_path = moved
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo_path], tags={"EXIF:DateTimeOriginal": dated.PHOTO_TAKEN},
                        params=["-overwrite_original"])
        moved = os.path.join(self.folder, CLIP_NAME)
        os.replace(self.clip_path, moved)
        self.clip_path = moved

    def year(self, photo_id):
        return self.vl.rows("SELECT year FROM photos WHERE id = ?", photo_id)[0][0]

    def scanned(self):
        """[(filename, taken, year)] of the folder's scan, in the order the folder view is given it."""
        reply = self.client.get("/library/api/folder/scan", query_string={"path": self.folder, "force": "true"})
        self.assertEqual(200, reply.status_code, reply.data)
        listed = reply.get_json()
        listed = listed if isinstance(listed, list) else listed["photos"]
        return [(record["filename"], record["taken"], record["year"]) for record in listed]

    def panel(self, photo_id):
        reply = self.client.get("/library/api/library/photo", query_string={"id": photo_id})
        self.assertEqual(200, reply.status_code, reply.data)
        record = reply.get_json()["photo"]
        return record["taken"], record["year"]

    def test_a_clip_of_zeroed_dates_is_dated_by_its_name_and_every_view_orders_it_so(self):
        before = self.read_bytes(self.clip_path)
        photo, clip = self.index(self.photo_path, self.clip_path)
        self.assertEqual((NAMED, 2019), (self.taken(clip), self.year(clip)),
                         "dated by its name at midnight, not by its folder's day")
        self.assertEqual([clip, photo], self.order(), "the library's order: 2019 before the photo's 2023")
        self.assertEqual([(CLIP_NAME, NAMED, "2019"), ("pier.jpg", dated.PHOTO_TAKEN, "2023")], self.scanned(),
                         "the folder view gives the same date and order")
        self.assertEqual((NAMED, "2019"), self.panel(clip), "and the panel's record the same date")
        self.assertEqual(before, self.read_bytes(self.clip_path), "the library reads the name: the file is not touched")

    def test_a_clip_whose_quicktime_date_is_set_keeps_it_whatever_its_name_says(self):
        zeroed_clip(self.clip_path, creation_time="2020-07-04T18:30:00Z")
        clip, = self.index(self.clip_path)
        self.assertTrue(self.taken(clip).startswith("2020:07:0"), self.taken(clip))
        self.assertEqual(2020, self.year(clip))

    def test_a_date_saved_from_the_panel_wins_over_the_name_and_its_undo_brings_the_names_date_back(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        reply = self.save_date_from_the_panel(clip)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(dated.SAVED, self.taken(clip), "the date saved into its XMP is read before its name")
        self.assertEqual([photo, clip], self.order())
        self.assertEqual((dated.SAVED, "2024"), self.panel(clip))
        self.undo(self.change_of(clip))
        self.assertEqual((NAMED, 2019), (self.taken(clip), self.year(clip)), "undone, the name dates it again")
        self.assertEqual([clip, photo], self.order())
        self.assertEqual((NAMED, "2019"), self.panel(clip))

    def test_a_clip_dated_only_in_its_folder_or_by_a_year_alone_in_its_name_has_no_year_a_photo_beside_it_keeps_its_folders(self):
        """The owner finds the undated clips to date them by hand (owner, 2026-11-13, #2941): a clip with no Date Taken has
        no year, neither its folder's (2 of photo_index's clips) nor a year alone in its name (`2016-07 ...`, `2015Dec ...`:
        9, counted read-only 2026-11-13), name_date taking a whole date only. An undated photo beside them keeps the year
        of its folder, as every photo does."""
        loose = zeroed_clip(os.path.join(self.folder, "jetty.mp4"))
        by_a_year = zeroed_clip(os.path.join(self.folder, "2016-07 slipway.mp4"))
        quay = os.path.join(self.folder, "quay.jpg")
        tagged.make_jpeg(quay, size=(32, 32))
        photo, clip, partial, undated_photo = self.index(self.photo_path, loose, by_a_year, quay)
        for each in (clip, partial):
            self.assertEqual((None, None), (self.taken(each), self.year(each)),
                             "a folder's date never dates a clip, nor a year alone in its name (owner, 2026-11-13)")
            self.assertEqual((None, dates.UNKNOWN_YEAR), self.panel(each))
        self.assertEqual((None, 2021), (self.taken(undated_photo), self.year(undated_photo)), "a photo keeps its folder's year")
        self.assertEqual([photo, clip, partial, undated_photo], self.order(), "undated, after the dated")
        scanned = {name: (taken, year) for name, taken, year in self.scanned()}
        self.assertEqual({"pier.jpg": (dated.PHOTO_TAKEN, "2023"), CLIP_NAME: (NAMED, "2019"),
                          "jetty.mp4": (None, dates.UNKNOWN_YEAR), "2016-07 slipway.mp4": (None, dates.UNKNOWN_YEAR),
                          "quay.jpg": (None, "2021")}, scanned,
                         "the folder view's records the same, the clip dated by its name its name's year")

    def test_an_older_format_clip_holding_no_quicktime_date_is_dated_as_every_clip(self):
        """A .avi's header is RIFF's: it holds no QuickTime date, and was dated as a photo, by its folder's year and never
        by its name, while the dating rules told a clip by the QuickTime date its record holds. Copied into a folder before
        its Convert, it is a row of kind video (images.VIDEO_TYPES lists the older formats so the owner sees them; none is
        in the live libraries now, each converted, counted read-only 2026-11-13), dated by its kind as every clip is."""
        named = zeroed_clip(os.path.join(self.folder, "2019-07-04 regatta.avi"))
        loose = zeroed_clip(os.path.join(self.folder, "slipway.avi"))
        by_name, undated = self.index(named, loose)
        self.assertEqual([("video",), ("video",)], self.vl.rows("SELECT kind FROM photos WHERE id IN (?, ?)", by_name, undated))
        self.assertEqual((NAMED, 2019), (self.taken(by_name), self.year(by_name)), "dated by the date in its name")
        self.assertEqual((None, None), (self.taken(undated), self.year(undated)), "never by its folder's year")
        self.assertEqual((NAMED, "2019"), self.panel(by_name), "the panel's record the same")
        self.assertEqual((None, dates.UNKNOWN_YEAR), self.panel(undated))
        scanned = {name: (taken, year) for name, taken, year in self.scanned()}
        self.assertEqual((NAMED, "2019"), scanned["2019-07-04 regatta.avi"], "and the folder view's")
        self.assertEqual((None, dates.UNKNOWN_YEAR), scanned["slipway.avi"])

    def test_a_photo_with_a_date_in_its_name_is_never_dated_by_it(self):
        named = os.path.join(self.folder, "2019-07-04 dock.jpg")
        tagged.make_jpeg(named, size=(32, 32))
        photo, = self.index(named)
        self.assertIsNone(self.taken(photo), "a photo's Date Taken is its file's alone")
        self.assertIsNone(self.panel(photo)[0])


if __name__ == "__main__":
    unittest.main()

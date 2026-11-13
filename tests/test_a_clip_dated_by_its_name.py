"""A clip whose file holds no date is dated by the date in its file name, at midnight (owner, 2026-11-13; plan
docs/plans/clip-date-from-name.md; scenario C5).

What the owner lives through: a clip copied off a phone as `2019-07-04 jetty.mp4`, its QuickTime dates zeros (680 of
photo_index's 2,910 clips, 669 of them dated in their name, counted read-only 2026-11-13), in a folder named for another
day. The library dates it by its name: its row's `taken` and year, the library's order by Date Taken, the folder view's
order and the page's record give the one date (dates.date_taken, given the path), and the file is not touched. A real
date always wins: a clip whose QuickTime date is set keeps it, and a Date Taken saved from the panel is read before the
name, its undo from History bringing the name's date back. A folder's date never dates a clip (the 11 dated only in
their folder stay undated, for the owner), and a photo is never dated by its name.

The one-off that writes the name's date into each such clip's XMP is tests/test_dating_clips_by_their_names.py.

Real ffmpeg clips (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip: its QuickTime dates zeros) and the
real ExifTool. Fictional names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_dated_through_the_journal as dated  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
TOOLS = tagged.TOOLS

#: The date the clip's name holds, as the library spells it; the folder it is in is named for another day.
NAMED = "2019:07:04 00:00:00"
CLIP_NAME = "2019-07-04 jetty.mp4"
FOLDER = "2021-05-01 Harbour"


def zeroed_clip(path, creation_time=None):
    """A real ffmpeg clip at `path`: its QuickTime dates zeros, or `creation_time` (UTC) in each header."""
    stamp = ["-metadata", "creation_time=" + creation_time] if creation_time else []
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                          "-t", "1", "-pix_fmt", "yuv420p", *stamp, path], capture_output=True)
    assert made.returncode == 0, made.stderr
    return path


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

    def test_a_clip_dated_only_in_its_folder_stays_undated(self):
        loose = zeroed_clip(os.path.join(self.folder, "jetty.mp4"))
        photo, clip = self.index(self.photo_path, loose)
        self.assertIsNone(self.taken(clip), "a folder's date never dates a clip (owner, 2026-11-13)")
        self.assertEqual([photo, clip], self.order(), "undated, after the dated")
        self.assertIsNone(self.panel(clip)[0])

    def test_a_photo_with_a_date_in_its_name_is_never_dated_by_it(self):
        named = os.path.join(self.folder, "2019-07-04 dock.jpg")
        tagged.make_jpeg(named, size=(32, 32))
        photo, = self.index(named)
        self.assertIsNone(self.taken(photo), "a photo's Date Taken is its file's alone")
        self.assertIsNone(self.panel(photo)[0])


if __name__ == "__main__":
    unittest.main()

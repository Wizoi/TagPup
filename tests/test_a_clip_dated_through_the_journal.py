"""A clip's Date Taken saved through the journal into its XMP, and read first (DECISIONS 2026-11-08, A5 (D) REVISED;
scenario F12).

What the owner lives through: a clip's card opens the photo panel and its Date Taken is set. The save patches the clip's
XMP in place (in_place.patch_clip, the patcher a tag save uses): XMP:DateTimeOriginal always, xmp:CreateDate refreshed
only where the clip held one already (Adobe's tools wrote one into 41 photo_index clips), never made in another
(fields.held_dates). Its QuickTime dates are never written: their bytes stay as they were, stale as every copy outside
XMP is. The library reads a clip's XMP date first (tagpup.core.dates.CLIP_XMP_DATE_FIELDS), so the row's `taken` and
the library's order follow the save; History's Undo puts the XMP back as it was -- an xmp:CreateDate the save refreshed
gets its old value back, not wiped -- and the row with it. A clip of a folder the library does not hold (Just look) is
refused with images.CLIP_NOT_EDITED and nothing is journaled, while the same save of the held one is written. A date
save cut short is settled at the next start by its bytes, as a tag's is (tests/test_a_clip_patch_cut_short.py): no arm
of the settle is a date's own.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip); ExifTool
writes the photo's EXIF date and, beforehand, the xmp:CreateDate an Adobe tool would have left. Each of these failed on
the code before the revision, which refused a clip's Date Taken whole. Fictional names only.
"""
import os
import shutil
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402
from test_a_clip_patch_cut_short import cut_half_way  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, journal, tagging  # noqa: E402
from tagpup.store import file_journal  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
TOOLS = tagged.TOOLS

#: When the photo beside the clip was taken; the clip's QuickTime CreateDate (UTC) is three years before, the date saved
#: a year after, so the save moves the clip from before the photo to after it in the library's order.
PHOTO_TAKEN = "2023:05:01 12:00:00"
SAVED = "2024:08:15 09:30:00"
#: The page's spelling of SAVED (its date input's value).
SAVED_AS_THE_PAGE_SENDS = "2024-08-15T09:30:00"
#: The xmp:CreateDate an Adobe tool left in a clip.
ADOBES = "2019:03:02 08:00:00"

XMP_DATES = ("XMP:DateTimeOriginal", "XMP:CreateDate")
QUICKTIME_DATES = ("QuickTime:CreateDate", "QuickTime:ModifyDate", "QuickTime:TrackCreateDate",
                   "QuickTime:TrackModifyDate", "QuickTime:MediaCreateDate", "QuickTime:MediaModifyDate")


def quicktime_date_boxes(data):
    """The bytes of each box of `data` that holds a QuickTime date (the movie's, each track's and each media's header),
    in order: what a clip's Date Taken save must leave byte for byte."""
    found = []
    for name in (b"mvhd", b"tkhd", b"mdhd"):
        at = data.find(name)
        while at >= 4:
            size, = struct.unpack(">I", data[at - 4:at])
            found.append(data[at - 4:at - 4 + size])
            at = data.find(name, at + 1)
    return found


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipDatedThroughTheJournal(tagged.ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        # As a camera leaves a clip: its QuickTime dates set (ffmpeg writes creation_time into each header), in UTC.
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2020-07-04T18:30:00Z",
                              self.clip_path], capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo_path], tags={"EXIF:DateTimeOriginal": PHOTO_TAKEN}, params=["-overwrite_original"])

    def read(self, path, names):
        with ExifToolSession(executable=EXIFTOOL) as et:
            held = et.get_tags([path], tags=list(names))[0]
        return {name: held.get(name) for name in names}

    def taken(self, photo_id):
        return self.vl.rows("SELECT taken FROM photos WHERE id = ?", photo_id)[0][0]

    def order(self):
        reply = self.client.get("/library/api/library/ids", query_string={"kind": "all", "order": "taken"})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["ids"]

    def save_date_from_the_panel(self, photo_id, date_taken=SAVED_AS_THE_PAGE_SENDS):
        """The panel's Save of a Date Taken: the record the page holds, its tags and title sent with it (edits.js
        postPhotoMetadata)."""
        reply = self.client.get("/library/api/library/photo", query_string={"id": photo_id})
        self.assertEqual(200, reply.status_code, reply.data)
        record = reply.get_json()["photo"]
        self.assertIn("date", record["writes"], "the panel's date control is alive for a clip")
        return self.post("photo/save-metadata", {
            "path": record["path"], "title": record["title"], "tags": record["tags"], "date_taken": date_taken,
            "stamp": {"mtime": record["mtime"], "size": record["size"]},
            "base": {"tags": record["tags"], "title": record["title"]}})

    def change_of(self, photo_id):
        (change,), = self.vl.rows("SELECT DISTINCT change_id FROM change_files WHERE photo_id = ?", photo_id)
        return change

    def undo(self, change):
        undone = journal.undo(self.vl.library, change, apply=True, exiftool_path=EXIFTOOL)
        self.assertEqual((1, []), (undone.changed, undone.errors), undone.message())
        return undone

    def test_a_date_saved_from_the_panel_is_written_into_its_xmp_its_quicktime_dates_left_and_the_library_follows(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        self.assertEqual([clip, photo], self.order(), "the clip by its QuickTime date, three years before the photo")
        before = self.read_bytes(self.clip_path)
        quicktime = self.read(self.clip_path, QUICKTIME_DATES)
        self.assertTrue(quicktime["QuickTime:CreateDate"].startswith("2020:07:04"), quicktime)

        reply = self.save_date_from_the_panel(clip)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual({"XMP:DateTimeOriginal": SAVED, "XMP:CreateDate": None}, self.read(self.clip_path, XMP_DATES),
                         "XMP:DateTimeOriginal written; no xmp:CreateDate made in a clip that held none")
        self.assertEqual(quicktime, self.read(self.clip_path, QUICKTIME_DATES), "its QuickTime dates read as they were")
        after = self.read_bytes(self.clip_path)
        self.assertNotEqual(before, after, "the clip's XMP was patched")
        self.assertEqual(quicktime_date_boxes(before), quicktime_date_boxes(after),
                         "and the boxes holding its QuickTime dates are byte for byte as they were")
        self.assertEqual(["done"], self.change_states(clip), "one journaled file, done")
        self.assertEqual(SAVED, self.taken(clip), "the row is dated by the XMP date saved")
        self.assertEqual([photo, clip], self.order(), "and the library orders it after the photo now")

        self.undo(self.change_of(clip))
        self.assertEqual({"XMP:DateTimeOriginal": None, "XMP:CreateDate": None}, self.read(self.clip_path, XMP_DATES),
                         "History's Undo takes the XMP date back off")
        self.assertEqual(quicktime_date_boxes(before), quicktime_date_boxes(self.read_bytes(self.clip_path)))
        self.assertTrue(self.taken(clip).startswith("2020:07:0"), "the row is dated by its QuickTime date again")
        self.assertEqual([clip, photo], self.order(), "and ordered before the photo again")

    def test_an_xmp_create_date_the_clip_held_is_refreshed_with_the_date_and_its_undo_gives_the_old_one_back(self):
        held = os.path.join(self.folder, "slipway.mp4")
        shutil.copyfile(self.clip_path, held)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([held], tags={"XMP:CreateDate": ADOBES}, params=["-overwrite_original"])
        clip, plain = self.index(held, self.clip_path)
        self.assertEqual(ADOBES, self.taken(clip), "a clip's XMP date is read before its QuickTime ones")

        for photo_id, path, created in ((clip, held, SAVED), (plain, self.clip_path, None)):
            with self.subTest(os.path.basename(path)):
                reply = self.save_date_from_the_panel(photo_id)
                self.assertEqual(200, reply.status_code, reply.get_json())
                self.assertEqual({"XMP:DateTimeOriginal": SAVED, "XMP:CreateDate": created}, self.read(path, XMP_DATES))
                self.assertEqual(SAVED, self.taken(photo_id))
                self.assertEqual((["XMP:CreateDate"] if created else []) + ["XMP:DateTimeOriginal"],
                                 sorted(self.journaled_fields(photo_id)), "the journal names only what it wrote")

        self.undo(self.change_of(clip))
        self.assertEqual({"XMP:DateTimeOriginal": None, "XMP:CreateDate": ADOBES}, self.read(held, XMP_DATES),
                         "the undo gives the xmp:CreateDate its old value back, never wipes it")
        self.assertEqual(ADOBES, self.taken(clip), "and the row is dated by it again")

    def journaled_fields(self, photo_id):
        """The date fields the journal's record of `photo_id`'s one file names after the write."""
        row, = file_journal.files_of(self.vl.library.path, self.change_of(photo_id))
        return [field for field in row.after if field.startswith("XMP:") and "Date" in field]

    def test_a_clip_of_a_folder_the_library_does_not_hold_is_refused_and_the_held_one_dated(self):
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        loose = os.path.join(elsewhere, "jetty.mp4")
        shutil.copyfile(self.clip_path, loose)
        clip, = self.index(self.clip_path)
        before, changes = self.read_bytes(loose), self.vl.rows("SELECT id FROM changes")

        reply = self.post("photo/save-metadata", {"path": loose, "tags": [], "date_taken": SAVED_AS_THE_PAGE_SENDS})
        self.assertNotEqual(200, reply.status_code)
        self.assertIn(images.CLIP_NOT_EDITED, reply.get_json()["error"])
        self.assertEqual(before, self.read_bytes(loose), "the Just-look clip is byte for byte as it was")
        self.assertEqual(changes, self.vl.rows("SELECT id FROM changes"), "nothing is journaled")

        reply = self.post("photo/save-metadata", {"path": self.clip_path, "tags": [],
                                                  "date_taken": SAVED_AS_THE_PAGE_SENDS})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(SAVED, self.read(self.clip_path, XMP_DATES)["XMP:DateTimeOriginal"])
        self.assertEqual(SAVED, self.taken(clip))

    def save_cut_short(self, cutting):
        """A Date Taken save of the clip killed part-way (the process gone: a Crash), then the next start's settle."""
        self.clip, = self.index(self.clip_path)
        with cutting:
            with self.assertRaises(Crash):
                tagging.save_photo(self.vl.library, self.clip_path, "", [], SAVED, EXIFTOOL, "")
        self.assertEqual(["writing"], self.change_states(self.clip))
        with mock.patch.object(file_changes, "_settled", set()):
            self.assertEqual(1, file_changes.settle_once(self.vl.library, EXIFTOOL))
        self.assertEqual(["done"], self.change_states(self.clip))
        self.assertEqual({"XMP:DateTimeOriginal": SAVED, "XMP:CreateDate": None}, self.read(self.clip_path, XMP_DATES),
                         "written again by the record the journal saved before its first byte")
        self.assertEqual(SAVED, self.taken(self.clip), "its row is dated by the date the settle wrote")

    def test_a_date_save_killed_once_its_record_was_saved_is_written_again_at_the_next_start(self):
        self.save_cut_short(mock.patch.object(file_changes, "_reached",
                                              side_effect=crash_at(file_changes.PATCH_SAVED)))

    def test_a_date_save_killed_half_way_through_its_box_is_cut_back_and_written_again_at_the_next_start(self):
        self.save_cut_short(cut_half_way())


if __name__ == "__main__":
    unittest.main()

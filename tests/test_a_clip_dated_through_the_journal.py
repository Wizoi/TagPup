"""A clip's Date Taken saved through the journal into its XMP, and read first (DECISIONS 2026-11-08, A5 (D) REVISED;
scenario C5).

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

A clip something else holds open (Explorer's preview, a scanner, another computer on the share) is refused, nothing
written, and the sentence says who holds it when this PC can tell (in_place.held_open, #2969): the owner's save of a
418.5 MB clip was refused again and again with nothing to go on. Restart Manager sees this PC's handles alone: for a
clip on the server it names none, and the sentence says no program on this PC holds it; when it
fails the save is refused all the same. A Convert of the clip goes through the same rule: only a sharing violation is
"open in another program"; access denied or a share gone keeps its own sentence (#2970).

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
from tagpup.files import images, in_place, lock_owners  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import convert, file_changes, journal, tagging  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
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
        self.assertEqual(SAVED, reply.get_json()["taken"], "the reply spells the date saved, for the page to show")
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

    def test_the_page_record_is_told_what_the_save_wrote_not_left_to_decide_it_from_its_own_raw_metadata(self):
        """A record whose raw_metadata lags the file (the file gained an Adobe xmp:CreateDate after the page scanned it)
        says what the save wrote: the xmp:CreateDate refreshed, and the reply carries the date as the library spells it."""
        held = os.path.join(self.folder, "slipway.mp4")
        shutil.copyfile(self.clip_path, held)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([held], tags={"XMP:CreateDate": ADOBES}, params=["-overwrite_original"])
        clip, = self.index(held)
        lagging = {"raw_metadata": {"QuickTime:CreateDate": "2020:07:04 18:30:00"}, "tags": []}

        result = tagging.save_photo(self.vl.library, held, "", [], SAVED_AS_THE_PAGE_SENDS, EXIFTOOL, "")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(SAVED, result.details["taken"])
        photo_actions.record_written(self.vl.library, lagging, held, [], result.details["flat"],
                                     result.details["hierarchical"], result.details["dates"])
        self.assertEqual({"XMP:DateTimeOriginal": SAVED, "XMP:CreateDate": SAVED},
                         {field: lagging["raw_metadata"].get(field) for field in XMP_DATES},
                         "the record says what the file now holds")
        self.assertEqual(self.read(held, XMP_DATES), {field: lagging["raw_metadata"].get(field) for field in XMP_DATES})
        self.assertEqual(SAVED, self.taken(clip))

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

    @unittest.skipUnless(os.name == "nt", "a clip held open is refused by Windows' sharing")
    def test_a_date_save_of_a_clip_another_handle_holds_is_refused_and_names_who_holds_it(self):
        clip, = self.index(self.clip_path)
        before, changes = self.read_bytes(self.clip_path), self.vl.rows("SELECT id FROM changes")
        with open(self.clip_path, "rb"):
            reply = self.save_date_from_the_panel(clip)
        self.assertNotEqual(200, reply.status_code)
        self.assertEqual(changes, self.vl.rows("SELECT id FROM changes"), "nothing is journaled")
        error = reply.get_json()["error"]
        self.assertIn(in_place.OPEN_ELSEWHERE, error)
        self.assertRegex(error, r"It is held open by python\w*\.exe \(this TagPup process\)\.",
                         "the refusal names the holder on this PC")
        self.assertEqual(before, self.read_bytes(self.clip_path), "nothing was written")

        reply = self.save_date_from_the_panel(clip)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(SAVED, self.taken(clip), "closed, the same save is written")

    @unittest.skipUnless(os.name == "nt", "a clip held open is refused by Windows' sharing")
    def test_a_clip_held_where_restart_manager_names_no_one_is_refused_and_on_a_share_says_so(self):
        clip, = self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        with mock.patch.object(lock_owners, "_ask", side_effect=OSError("Restart Manager could not be asked")):
            with open(self.clip_path, "rb"):
                reply = self.save_date_from_the_panel(clip)
        error = reply.get_json()["error"]
        self.assertIn(in_place.OPEN_ELSEWHERE, error, "Restart Manager failing is no holder named, never a crash")
        self.assertNotIn("held open by", error)
        self.assertEqual(before, self.read_bytes(self.clip_path))

        # On the server, Restart Manager answers no holder (the server's own handles, or another PC's, are not this
        # PC's): a fictional share's spelling, nothing opened on it.
        on_the_server = "\\\\fileserver\\Pictures\\Harbour\\jetty.mp4"
        violation = OSError(None, "The process cannot access the file", on_the_server, 32)
        with mock.patch.object(lock_owners, "_ask", return_value=[]):
            said = in_place.held_open(violation, on_the_server, in_place.OPEN_ELSEWHERE)
        self.assertEqual("%s. %s" % (in_place.OPEN_ELSEWHERE, in_place.NOT_ON_THIS_PC), said)

    @unittest.skipUnless(os.name == "nt", "a clip held open is refused by Windows' sharing")
    def test_a_convert_of_a_clip_another_handle_holds_is_refused_and_names_who_holds_it(self):
        self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        with open(self.clip_path, "rb"):
            held = convert.convert_clip(self.vl.library, EXIFTOOL, TOOLS, self.clip_path, reason=convert.SMALLER)
        self.assertEqual(0, held.changed)
        self.assertTrue(held.refused.startswith(convert.HELD_OPEN), held.refused)
        self.assertIn("(this TagPup process).", held.refused)
        self.assertEqual(before, self.read_bytes(self.clip_path))

    def test_a_convert_of_a_clip_whose_open_fails_otherwise_keeps_its_own_sentence(self):
        """Access denied, or the share gone, is not "open in another program" (#2970)."""
        self.index(self.clip_path)
        opened = lock_owners.opened
        for winerror, said in ((5, "Access is denied"), (64, "The specified network name is no longer available")):
            def failing(path, sharing, write=False):
                if sharing == lock_owners.NO_SHARING:
                    raise OSError(None, said, path, winerror)
                return opened(path, sharing, write)
            with self.subTest(winerror=winerror), mock.patch.object(lock_owners, "opened", side_effect=failing):
                result = convert.convert_clip(self.vl.library, EXIFTOOL, TOOLS, self.clip_path, reason=convert.SMALLER)
                self.assertEqual(0, result.changed)
                self.assertNotIn("open in another program", result.refused)
                self.assertEqual(convert.UNREADABLE % said, result.refused)

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

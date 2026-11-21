"""A clip's Date Taken saved through the journal into its XMP and its QuickTime headers, the XMP read first (DECISIONS
2026-11-08, A5 (D) REVISED; 2026-11-13, A CLIP'S DATE TAKEN IS WRITTEN INTO ITS QUICKTIME HEADERS; scenario C5).

What the owner lives through: a clip's card opens the photo panel and its Date Taken is set. The save patches the clip's
XMP in place (in_place.patch_clip, the patcher a tag save uses): XMP:DateTimeOriginal always, xmp:CreateDate refreshed
only where the clip held one already (Adobe's tools wrote one into 41 photo_index clips), never made in another
(fields.held_dates). In the same patch it sets the creation time of every movie, track and media header (mvhd, tkhd,
mdhd), at the box's own width (4 bytes in version 0, 8 in version 1), every other byte as it was, since SmugMug reads a
clip's date there alone; a clip whose XMP holds the date already and whose headers do not is written (#3002), and a
header is never written without the XMP date beside it. The library reads a clip's XMP date first
(tagpup.core.dates.CLIP_XMP_DATE_FIELDS), so the row's `taken` and the library's order follow the save; History's Undo
puts the XMP back as it was -- an xmp:CreateDate the save refreshed gets its old value back, not wiped -- and every
header box its own old bytes, a clip whose tracks disagreed included (the journal reads them box by box, #3004). Two
saves of one clip run one after the other; a share that stops answering while the headers are read writes nothing; a
date no version 0 header holds (before 1904, after early 2040) is refused with a sentence. A clip of a folder the library does not hold (Just look) is
refused with images.CLIP_NOT_EDITED and nothing is journaled, while the same save of the held one is written. A date
save cut short is settled at the next start by its bytes, as a tag's is (tests/test_a_clip_patch_cut_short.py): no arm
of the settle is a date's own.

A clip something else holds open (Explorer's preview, a scanner, another computer on the share) is refused, nothing
written, and the sentence says who holds it when this PC can tell (lock_owners.explain, #2969): the owner's save of a
418.5 MB clip was refused again and again with nothing to go on. Restart Manager sees this PC's handles alone: for a
clip on the server it names none, and the sentence says no program on this PC holds it; when it
fails the save is refused all the same. A Convert of the clip goes through the same rule: only a sharing violation is
"open in another program"; access denied or a share gone keeps its own sentence (#2970).

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip); ExifTool
writes the photo's EXIF date and, beforehand, the xmp:CreateDate an Adobe tool would have left. Clips laid out as real
ones are (tests/fixtures/clip_headers.json: their headers' bytes, captured read-only by container, version and track
count, their dates moved to a fictional day). Each of these failed on the code before the revision, which refused a
clip's Date Taken whole, and the header scenarios on the code before the 2026-11-13 decision, which wrote no header.
Fictional names only.
"""
import io
import json
import os
import shutil
import struct
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402
from test_a_clip_patch_cut_short import cut_half_way  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tests import page_writes  # noqa: E402
from tagpup.core import fields, processes  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import clip_patch, containers, field_values, images, in_place, lock_owners  # noqa: E402
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


#: Real clips' header layouts (the module): the shape of most of the 681 clips the one-off dates, of the clips holding
#: version 1 headers, of a dated three-track .mov, and of one whose track and media headers disagree.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "clip_headers.json"), encoding="utf-8") as _f:
    SHAPES = json.load(_f)["shapes"]
ZERO_HEADERS = "mp4 version 0, two tracks, zero headers"
VERSION_ONE = "mp4 version 1 movie and track headers, version 0 media headers, two tracks, zero"
THREE_TRACKS = "mov version 0, three tracks, dated"
DISAGREEING = "mov version 0, four tracks whose track and media headers disagree"


def _box(kind, content=b""):
    return struct.pack(">I", 8 + len(content)) + kind.encode("latin-1") + content


def _boxes(entries, at, depth):
    made = b""
    while at < len(entries) and entries[at]["depth"] == depth:
        entry = entries[at]
        children, at = _boxes(entries, at + 1, depth + 1)
        made += bytes.fromhex(entry["bytes"]) if entry["bytes"] else _box(entry["type"], children)
    return made, at


def clip_of(shape, moov_after_mdat=False):
    """The bytes of a clip laid out as the real clips of `shape` are: its boxes under moov as the fixture lists them (a
    header's bytes as captured, any other box empty), an ftyp before them and an mdat before or after."""
    moov, _ = _boxes(SHAPES[shape]["boxes"], 0, 0)
    brand = b"qt  \0\0\0\0qt  " if shape.startswith("mov") else b"isom\0\0\2\0isomiso2mp41"
    mdat = _box("mdat", bytes(64))
    return _box("ftyp", brand) + (mdat + moov if moov_after_mdat else moov + mdat)


def creation_times(data):
    """The bytes of every header's creation time in `data`, in file order, each at its box's width."""
    return [data[box.payload + 4:box.payload + 4 + box.width]
            for box in containers.read(io.BytesIO(data)).headers]


def without_creation_times(data):
    """`data`, every header's creation time zeroed: what a date save leaves of each byte it does not date."""
    zeroed = bytearray(data)
    for box in containers.read(io.BytesIO(data)).headers:
        zeroed[box.payload + 4:box.payload + 4 + box.width] = bytes(box.width)
    return bytes(zeroed)


def saved_times(data, date=None):
    """Every header's creation time of `data` holding `date` (SAVED), at its box's width."""
    seconds = clip_patch.seconds_of(date or SAVED)
    return [seconds.to_bytes(box.width, "big") for box in containers.read(io.BytesIO(data)).headers]


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

    def add_clip(self, shape, name, moov_after_mdat=False):
        """A clip laid out as the real clips of `shape` (clip_of) put in the library's folder; its path."""
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(clip_of(shape, moov_after_mdat))
        return path

    def taken(self, photo_id):
        return self.vl.rows("SELECT taken FROM photos WHERE id = ?", photo_id)[0][0]

    def order(self):
        reply = self.client.get("/library/api/library/ids", query_string={"kind": "all", "order": "taken"})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["ids"]

    def save_date_from_the_panel(self, photo_id, date_taken=SAVED_AS_THE_PAGE_SENDS):
        """The panel's Save of a Date Taken: the record the page holds, its tags and title sent with it
        (web/common/photo-write.js postPhotoMetadata)."""
        reply = self.client.get("/library/api/library/photo", query_string={"id": photo_id})
        self.assertEqual(200, reply.status_code, reply.data)
        record = reply.get_json()["photo"]
        self.assertIn("date", record["writes"], "the panel's date control is alive for a clip")
        return self.post("photos/write", page_writes.set_from(record, date_taken=date_taken))

    def change_of(self, photo_id):
        (change,), = self.vl.rows("SELECT DISTINCT change_id FROM change_files WHERE photo_id = ?", photo_id)
        return change

    def undo(self, change):
        undone = journal.undo(self.vl.library, change, apply=True, exiftool_path=EXIFTOOL)
        self.assertEqual((1, []), (undone.changed, undone.errors), undone.message())
        return undone

    def test_a_date_saved_from_the_panel_is_written_into_its_xmp_and_every_quicktime_header_and_the_library_follows(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        self.assertEqual([clip, photo], self.order(), "the clip by its QuickTime date, three years before the photo")
        before = self.read_bytes(self.clip_path)
        quicktime = self.read(self.clip_path, QUICKTIME_DATES)
        self.assertTrue(quicktime["QuickTime:CreateDate"].startswith("2020:07:04"), quicktime)

        reply = self.save_date_from_the_panel(clip)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(SAVED, page_writes.saved(reply.get_json())["taken"], "the reply spells the date saved, for the page to show")
        self.assertEqual({"XMP:DateTimeOriginal": SAVED, "XMP:CreateDate": None}, self.read(self.clip_path, XMP_DATES),
                         "XMP:DateTimeOriginal written; no xmp:CreateDate made in a clip that held none")
        self.assertEqual({**quicktime, **{field: SAVED for field in fields.CLIP_HEADER_DATES}},
                         self.read(self.clip_path, QUICKTIME_DATES),
                         "its headers' creation times read as the date saved, their modification times as they were")
        after = self.read_bytes(self.clip_path)
        self.assertEqual(saved_times(after), creation_times(after), "every header box holds the date's digits")
        self.assertEqual(quicktime_date_boxes(without_creation_times(before)),
                         quicktime_date_boxes(without_creation_times(after)),
                         "and every other byte of the header boxes is as it was")
        self.assertEqual(["done"], self.change_states(clip), "one journaled file, done")
        self.assertEqual(SAVED, self.taken(clip), "the row is dated by the XMP date saved")
        self.assertEqual([photo, clip], self.order(), "and the library orders it after the photo now")

        self.undo(self.change_of(clip))
        self.assertEqual({"XMP:DateTimeOriginal": None, "XMP:CreateDate": None}, self.read(self.clip_path, XMP_DATES),
                         "History's Undo takes the XMP date back off")
        self.assertEqual(quicktime_date_boxes(before), quicktime_date_boxes(self.read_bytes(self.clip_path)),
                         "and gives every header box its own bytes back")
        self.assertTrue(self.taken(clip).startswith("2020:07:0"), "the row is dated by its QuickTime date again")
        self.assertEqual([clip, photo], self.order(), "and ordered before the photo again")

    def test_a_clip_laid_out_as_real_ones_are_has_every_header_dated_at_its_width_and_its_undo_gives_each_box_back(self):
        """Version 0 and version 1 boxes, moov before and after mdat, three tracks, and four whose track and media
        headers disagree: each box given back its own seconds, byte for byte."""
        for n, (shape, moov_after_mdat) in enumerate((shape, last) for shape in SHAPES for last in (False, True)):
            with self.subTest(shape=shape, moov_after_mdat=moov_after_mdat):
                path = self.add_clip(shape, "shore-%d.%s" % (n, shape[:3]), moov_after_mdat)
                before = self.read_bytes(path)
                clip, = self.index(path)

                reply = self.save_date_from_the_panel(clip)
                self.assertEqual(200, reply.status_code, reply.get_json())
                after = self.read_bytes(path)
                self.assertEqual(saved_times(after), creation_times(after), "every header holds the date's digits")
                self.assertEqual(without_creation_times(before), without_creation_times(after)[:len(before)],
                                 "every byte the save does not date is as it was (its XMP appended after them)")
                self.assertEqual({"XMP:DateTimeOriginal": SAVED, **{field: SAVED for field in fields.CLIP_HEADER_DATES}},
                                 self.read(path, ("XMP:DateTimeOriginal",) + fields.CLIP_HEADER_DATES))
                self.assertEqual((["done"], SAVED), (self.change_states(clip), self.taken(clip)))
                row, = file_journal.files_of(self.vl.library.path, self.change_of(clip))
                boxes = clip_patch.header_dates(containers.read(io.BytesIO(before)))
                self.assertEqual(boxes, {field: row.before[field] for field in fields.CLIP_HEADER_DATES},
                                 "the journal holds what each box held, in box order")

                self.undo(self.change_of(clip))
                self.assertEqual(quicktime_date_boxes(before), quicktime_date_boxes(self.read_bytes(path)),
                                 "the undo gives every header box its own bytes back")
                self.assertEqual(creation_times(before), creation_times(self.read_bytes(path)))
        disagreeing = clip_patch.header_dates(containers.read(io.BytesIO(clip_of(DISAGREEING))))
        tracks = disagreeing["QuickTime:TrackCreateDate"]
        self.assertEqual((4, 2), (len(tracks), len(set(tracks))),
                         "the four-track clip's track headers disagree, each given back its own")

    def add_clip_with_a_garbage_header(self, name):
        """A version 1 clip (VERSION_ONE) whose first version 1 header holds 8 bytes of 0xFF as its creation time: a time
        no date holds (#3005); its path."""
        path = self.add_clip(VERSION_ONE, name)
        data = bytearray(self.read_bytes(path))
        box = [box for box in containers.read(io.BytesIO(bytes(data))).headers if box.version == 1][0]
        data[box.payload + 4:box.payload + 12] = b"\xff" * 8
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        return path

    def test_a_clip_whose_header_holds_a_time_no_date_holds_is_unreadable_for_its_save_not_a_failed_batch(self):
        """#3005: 8 bytes of 0xFF in a version 1 header is past every date; the clip answers the per-clip sentence, the
        clip beside it in the same read is read, and the save of the clip is refused with nothing written."""
        bad = self.add_clip_with_a_garbage_header("garbled.mp4")
        good = self.add_clip(ZERO_HEADERS, "sound.mp4")
        bad_id, good_id = self.index(bad, good)
        with ExifToolSession(executable=EXIFTOOL) as et:
            held = field_values.read(et, [bad, good], fields.CLIP_HEADER_DATES)
        unreadable = [one for one in held.values() if isinstance(one, field_values.Unreadable)]
        self.assertEqual(1, len(unreadable), "the garbled clip alone is unreadable")
        self.assertIn("its QuickTime headers could not be read", str(unreadable[0]))
        self.assertEqual(1, len([one for one in held.values() if isinstance(one, dict)]), "the clip beside it is read")
        before, changes = self.read_bytes(bad), self.vl.rows("SELECT id FROM changes")
        reply = self.save_date_from_the_panel(bad_id)
        self.assertIn("its QuickTime headers could not be read", page_writes.why(reply.get_json()), "a sentence, not a traceback")
        self.assertEqual(before, self.read_bytes(bad))
        self.assertEqual(changes, self.vl.rows("SELECT id FROM changes"))

    def test_a_clip_whose_media_box_does_not_hold_its_children_is_refused_whole_and_none_of_its_headers_is_written(self):
        """#3006: the media box's header cannot be found, so the headers the walk does find would be dated and one
        would keep its old date; the save says CLIP_NOT_PATCHED and writes no byte."""
        path = self.add_clip(ZERO_HEADERS, "short.mp4")
        data = bytearray(self.read_bytes(path))
        media = data.find(b"mdia")
        data[media + 4:media + 8] = struct.pack(">I", 0x00FFFFFF)
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        clip, = self.index(path)
        before, changes = self.read_bytes(path), self.vl.rows("SELECT id FROM changes")
        reply = self.save_date_from_the_panel(clip)
        self.assertFalse(reply.get_json()["success"], reply.data)
        self.assertIn(images.CLIP_NOT_PATCHED, page_writes.why(reply.get_json()))
        self.assertEqual((before, changes), (self.read_bytes(path), self.vl.rows("SELECT id FROM changes")))

    def test_a_clip_whose_xmp_holds_the_date_already_has_its_zero_headers_written(self):
        """#3002: a clip saved before the headers were written (its XMP dated, its headers 0), saved again with the same
        date, has its headers written; nothing else changes."""
        path = self.add_clip(ZERO_HEADERS, "slipway.mp4")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([path], tags={"XMP:DateTimeOriginal": SAVED}, params=["-overwrite_original"])
        clip, = self.index(path)
        self.assertEqual(SAVED, self.taken(clip))
        self.assertEqual([bytes(4)] * 5, creation_times(self.read_bytes(path)))

        reply = self.save_date_from_the_panel(clip)
        self.assertEqual(200, reply.status_code, reply.get_json())
        after = self.read_bytes(path)
        self.assertEqual(saved_times(after), creation_times(after), "its headers are written")
        self.assertEqual(["done"], self.change_states(clip))
        self.assertEqual(sorted(fields.CLIP_HEADER_DATES), sorted(set(self.journaled_quicktime(clip))))

    def journaled_quicktime(self, photo_id):
        row, = file_journal.files_of(self.vl.library.path, self.change_of(photo_id))
        return [field for field in row.after if field.startswith("QuickTime:")]

    def test_a_header_is_never_written_without_the_xmp_date_beside_it(self):
        path = self.add_clip(ZERO_HEADERS, "slipway.mp4")
        before = self.read_bytes(path)
        headers = {field: [SAVED] for field in fields.CLIP_HEADER_DATES}
        with self.assertRaises(Refused) as refused:
            field_values.write(None, path, headers, saving=lambda record, before: None)
        self.assertEqual(images.CLIP_NOT_EDITED, str(refused.exception))
        self.assertEqual(before, self.read_bytes(path), "nothing was written")

        field_values.write(None, path, {"XMP:DateTimeOriginal": [SAVED], **headers}, saving=lambda record, before: None)
        after = self.read_bytes(path)
        self.assertEqual(saved_times(after), creation_times(after), "beside its XMP date, the header is written")

    def test_two_saves_of_one_clip_at_once_leave_its_headers_and_its_xmp_one_date_and_the_undo_gives_the_first_back(self):
        path = self.add_clip(THREE_TRACKS, "harbour.mov")
        clip, = self.index(path)
        other = "2021:02:03 04:05:06"
        results, start = {}, threading.Barrier(2)

        def save(date):
            start.wait(10)
            results[date] = tagging.save_photo(self.vl.library, path, "", [], date, EXIFTOOL, "")
        saves = [threading.Thread(target=save, args=(date,)) for date in (SAVED, other)]
        for one in saves:
            one.start()
        for one in saves:
            one.join(120)
        self.assertEqual([True, True], [results[date].ok for date in (SAVED, other)],
                         [results[date].message() for date in results])
        self.assertEqual(["done", "done"], self.change_states(clip), "one after the other")
        last = self.read(path, ("XMP:DateTimeOriginal",))["XMP:DateTimeOriginal"]
        after = self.read_bytes(path)
        self.assertEqual(saved_times(after, last), creation_times(after), "its headers hold the date its XMP holds")

        (later,), = self.vl.rows("SELECT MAX(change_id) FROM change_files WHERE photo_id = ?", clip)
        self.undo(later)
        first = other if last == SAVED else SAVED
        self.assertEqual(first, self.read(path, ("XMP:DateTimeOriginal",))["XMP:DateTimeOriginal"])
        undone = self.read_bytes(path)
        self.assertEqual(saved_times(undone, first), creation_times(undone), "and its headers the first save's again")

    def test_a_share_that_stops_answering_while_its_headers_are_read_writes_nothing(self):
        path = self.add_clip(ZERO_HEADERS, "slipway.mp4")
        second = self.add_clip(ZERO_HEADERS, "jetty-two.mp4")
        clip, _ = self.index(path, second)
        before, changes = self.read_bytes(path), self.vl.rows("SELECT id FROM changes")
        with mock.patch.object(in_place, "headers_held", side_effect=in_place.Away("the share did not answer")) as held:
            reply = self.save_date_from_the_panel(clip)
            self.assertFalse(reply.get_json()["success"])
            self.assertIn("did not answer", page_writes.why(reply.get_json()))
            self.assertEqual((before, changes), (self.read_bytes(path), self.vl.rows("SELECT id FROM changes")),
                             "nothing written, nothing journaled")
            held.reset_mock()
            with ExifToolSession(executable=EXIFTOOL) as et:
                found = field_values.read(et, [path, second], fields.CLIP_HEADER_DATES)
        self.assertEqual(1, held.call_count, "one read waits one deadline: the clips after it are not read")
        self.assertTrue(all(isinstance(one, field_values.NotAnswered) for one in found.values()), found)

    def test_a_date_on_the_minute_as_the_date_input_sends_it_without_seconds_is_written_to_the_second(self):
        # A datetime-local input (step 1) answers the shortest form: "2024-08-15T09:30" for a time on the minute.
        clip, = self.index(self.clip_path)
        reply = self.save_date_from_the_panel(clip, "2024-08-15T09:30")
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual("2024:08:15 09:30:00", self.read(self.clip_path, XMP_DATES)["XMP:DateTimeOriginal"])
        written = self.read_bytes(self.clip_path)
        self.assertEqual(saved_times(written, "2024:08:15 09:30:00"), creation_times(written))

    def test_a_date_no_quicktime_header_holds_is_refused_with_a_sentence_and_nothing_is_written(self):
        clip, = self.index(self.clip_path)
        before, changes = self.read_bytes(self.clip_path), self.vl.rows("SELECT id FROM changes")
        for date in ("1899-12-31T10:00:00", "2041-01-01T10:00:00"):
            with self.subTest(date):
                reply = self.save_date_from_the_panel(clip, date)
                self.assertFalse(reply.get_json()["success"])
                self.assertIn("holds a date from 1904 to early 2040 only", page_writes.why(reply.get_json()))
                self.assertEqual((before, changes), (self.read_bytes(self.clip_path),
                                                     self.vl.rows("SELECT id FROM changes")))

    def test_a_zoned_date_taken_saved_on_a_clip_writes_its_digits_into_the_headers_and_keeps_its_zone_in_the_xmp(self):
        # The one-off saves a clip's own Date Taken as its XMP holds it, zone included (the panel never sends one): the
        # save and Convert's carry decide the header digits by one rule (fields.date_taken_fields).
        clip, = self.index(self.clip_path)
        reply = self.save_date_from_the_panel(clip, "2018:03:02 09:15:00-07:00")
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual("2018:03:02 09:15:00-07:00", self.read(self.clip_path, XMP_DATES)["XMP:DateTimeOriginal"])
        written = self.read_bytes(self.clip_path)
        self.assertEqual(saved_times(written, "2018:03:02 09:15:00"), creation_times(written))

    def test_a_zoned_date_taken_no_header_holds_is_refused_as_an_unzoned_one_is(self):
        clip, = self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        reply = self.save_date_from_the_panel(clip, "2041:01:01 10:00:00+01:00")
        self.assertFalse(reply.get_json()["success"])
        self.assertIn("holds a date from 1904 to early 2040 only", page_writes.why(reply.get_json()))
        self.assertEqual(before, self.read_bytes(self.clip_path))

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

        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": loose, "tags": [], "date_taken": SAVED_AS_THE_PAGE_SENDS}))
        self.assertFalse(reply.get_json()["success"])
        self.assertIn(images.CLIP_NOT_EDITED, page_writes.why(reply.get_json()))
        self.assertEqual(before, self.read_bytes(loose), "the Just-look clip is byte for byte as it was")
        self.assertEqual(changes, self.vl.rows("SELECT id FROM changes"), "nothing is journaled")

        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": [],
                                                  "date_taken": SAVED_AS_THE_PAGE_SENDS}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual(SAVED, self.read(self.clip_path, XMP_DATES)["XMP:DateTimeOriginal"])
        self.assertEqual(SAVED, self.taken(clip))

    @unittest.skipUnless(os.name == "nt", "a clip held open is refused by Windows' sharing")
    def test_a_date_save_of_a_clip_another_handle_holds_is_refused_and_names_who_holds_it(self):
        clip, = self.index(self.clip_path)
        before, changes = self.read_bytes(self.clip_path), self.vl.rows("SELECT id FROM changes")
        with open(self.clip_path, "rb"):
            reply = self.save_date_from_the_panel(clip)
        self.assertFalse(reply.get_json()["success"])
        self.assertEqual(changes, self.vl.rows("SELECT id FROM changes"), "nothing is journaled")
        error = page_writes.why(reply.get_json())
        self.assertIn(in_place.OPEN_ELSEWHERE, error)
        self.assertRegex(error, r"It is held open by python\w*\.exe \(this TagPup process, pid \d+\)\.",
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
        error = page_writes.why(reply.get_json())
        self.assertIn(in_place.OPEN_ELSEWHERE, error, "Restart Manager failing is no holder named, never a crash")
        self.assertNotIn("held open by", error)
        self.assertEqual(before, self.read_bytes(self.clip_path))

        # On the server, Restart Manager answers no holder (the server's own handles, or another PC's, are not this
        # PC's): a fictional share's spelling, nothing opened on it.
        on_the_server = "\\\\fileserver\\Pictures\\Harbour\\jetty.mp4"
        violation = OSError(None, "The process cannot access the file", on_the_server, 32)
        with mock.patch.object(lock_owners, "_ask", return_value=[]):
            said = lock_owners.explain(violation, on_the_server, in_place.OPEN_ELSEWHERE)
        self.assertEqual("%s. %s" % (in_place.OPEN_ELSEWHERE, lock_owners.NOT_ON_THIS_PC), said)

    @unittest.skipUnless(os.name == "nt", "a clip held open is refused by Windows' sharing")
    def test_a_convert_of_a_clip_another_handle_holds_is_refused_and_names_who_holds_it(self):
        self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        with open(self.clip_path, "rb"):
            held = convert.convert_clip(self.vl.library, EXIFTOOL, TOOLS, self.clip_path, reason=convert.SMALLER)
        self.assertEqual(0, held.changed)
        self.assertTrue(held.refused.startswith(convert.HELD_OPEN), held.refused)
        self.assertRegex(held.refused, r"\(this TagPup process, pid \d+\)\.")
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
        written = self.read_bytes(self.clip_path)
        self.assertEqual(saved_times(written), creation_times(written), "its headers with it")
        self.assertEqual(SAVED, self.taken(self.clip), "its row is dated by the date the settle wrote")

    def test_a_date_save_killed_once_its_record_was_saved_is_written_again_at_the_next_start(self):
        self.save_cut_short(mock.patch.object(file_changes, "_reached",
                                              side_effect=crash_at(file_changes.PATCH_SAVED)))

    def test_a_date_save_killed_half_way_through_its_box_is_cut_back_and_written_again_at_the_next_start(self):
        self.save_cut_short(cut_half_way())


if __name__ == "__main__":
    unittest.main()

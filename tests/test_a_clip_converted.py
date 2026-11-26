"""A clip of an old format converted to H.264 and AAC in an MP4 (C7; tagpup.services.convert, DECISIONS 2026-11-09, 'A
CLIP IS CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'): its row followed to the MP4, what it carried written into the
copy, the original deleted for good last (never to a Recycle Bin: owner 2026-11-12), one change of History that is
never undone.

What the owner lives through: the one-time convert of the old formats (.wmv, .mpg, .avi; and an .mp4 whose picture is
MPEG-4 Part 2, converted under its own name), each clip one change; the run cut short at any step and settled at the next
start, or its share falling asleep as its copy is put in place; a clip open in a player before or after the plan, a full
disk, ffmpeg failing; two libraries holding the folder, the second asked to convert the clip meanwhile, or finding it at
its MP4 by the name it kept (TwoLibrariesHoldingTheFolder); a clip tagged long ago in Windows, its Windows Tags carried
into its copy's tags, one the tree lacks refused through the panel (AClipHoldingWindowsTags); a clip holding XMP
other programs wrote, its packet carried whole into its copy, or refused when no patch reads it (AClipHoldingXmpOfOtherPrograms);
a dated clip (a camera's .mov, an iPhone's dated with its zone, an .mp4 or a .wmv dated in its XMP) its Date Taken in
its copy's XMP and every QuickTime header, ffmpeg carrying none, through a cut-short run and two libraries alike; one
no header holds, in its XMP alone (assert_dated).
The panel's
Convert and Make smaller (MP4) by id: answered at once with a ticket, a second click refused, Cancel on the ticket, a bulk
edit waiting behind, a shrink no smaller left as it is, a button that is not the clip's plan refused (ThePanelButtons).

Real one-second clips made by the machine's ffmpeg in each old format, recorded by the indexer's own read
(test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip), in a home of its own; a conversion that reached for a Recycle Bin
fails the test (AnOldClipInTheLibrary). Skipped, saying so, without ffmpeg or
ExifTool. Fictional names only.
"""
import contextlib
import json
import os
import re
import struct
import sys
import threading
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import page_fixtures  # noqa: E402
import photo_rows  # noqa: E402
import test_a_clip_patched_in_place as patched  # noqa: E402
import test_xmp_packet_edits_what_it_owns as shapes  # noqa: E402
from test_a_clip_tagged_through_the_journal import EXIFTOOL, TOOLS, ALibraryHoldingAClip  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import fields, paths  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.files import (clip_convert, containers, field_values, in_place, recycle_bin,  # noqa: E402
                          shares, video_frames)
from tagpup.services import batches, convert, file_changes, sync, tagging  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import file_journal, photos  # noqa: E402

#: The old formats as the library holds them: (extension, ffmpeg's picture and sound encoders, its container).
FORMATS = {
    ".wmv": ("wmv2", "wmav2", "asf"),
    ".mpg": ("mpeg1video", "mp2", "mpeg"),
    ".avi": ("mpeg4", "pcm_s16le", "avi"),
    ".mp4": ("mpeg4", "aac", "mp4"),       # MPEG-4 Part 2 in an MP4: converted under its own name
}
TITLE = "Gulls over the slipway"
#: When a camera made a clip: its container's creation time (an MP4's or a .mov's mvhd, read as QuickTime:CreateDate, in
#: UTC), as every dated .mov of photo_index is dated (counted 2026-11-12).
CREATED = "2019-05-04T17:00:00Z"
#: A Date Taken in a clip's XMP, with its zone, as Adobe's tools wrote the 105 XMP-dated .mp4 of photo_index (counted
#: 2026-11-12): the date the library reads first (tagpup.core.dates.CLIP_XMP_DATE_FIELDS).
XMP_TAKEN = "2018:03:02 09:15:00-07:00"
#: A Date Taken in a .wmv's XMP (wmv_dated); and what a QuickTime header holds when it holds none.
WMV_TAKEN = "2011:08:20 14:05:00"
ZERO = "0000:00:00 00:00:00"
#: The top-level ASF object holding a .wmv's XMP, where Adobe's XMP specification (part 3) puts it.
ASF_XMP = uuid.UUID("BE7ACFCB-97A9-42E8-9C71-999491E3AFAC").bytes_le
#: The replies of the clip panel's Convert its page tests answer from (harness.mjs convertedAs): written by
#: ThePanelButtons.test_the_convert_replies_the_page_reads_are_written_for_its_test.
CONVERT_REPLIES = page_fixtures.path_of("convert_replies.json")


def make_clip(path, title=None, created=None, taken=None):
    """A one-second clip at `path` in the old format its extension names (FORMATS), with sound, titled `title`; `created`,
    its container's creation time (a .wmv's ASF one is when its file was made, here when the corpus made it, perhaps
    days ago: never its Date Taken); a .wmv `taken`, dated so in its XMP (wmv_dated). Made once and kept (tests/corpus.py)."""
    video, audio, container = FORMATS[os.path.splitext(path)[1]]
    titled = ["-metadata", "title=" + title] if title else []
    titled += ["-metadata", "creation_time=" + created] if created else []
    corpus.clip(path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "1", "-c:v", video,
                       "-c:a", audio] + titled + ["-f", container], TOOLS.ffmpeg)
    return wmv_dated(path, taken) if taken else path


def wmv_dated(path, taken):
    """The .wmv at `path` given an XMP packet dated `taken` ("YYYY:MM:DD HH:MM:SS") in a top-level ASF object of its own
    (ASF_XMP), which ExifTool reads as XMP:DateTimeOriginal and ffmpeg passes over."""
    packet = (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
              b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
              b'xmlns:exif="http://ns.adobe.com/exif/1.0/"><exif:DateTimeOriginal>%s</exif:DateTimeOriginal>'
              b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
              % (taken[:10].replace(":", "-") + "T" + taken[11:]).encode())
    with open(path, "ab") as handle:
        handle.write(ASF_XMP + struct.pack("<Q", 24 + len(packet)) + packet)
    return path


class AnOldClipInTheLibrary(ALibraryHoldingAClip):
    """The library of ALibraryHoldingAClip, where a conversion that reaches for a Recycle Bin fails the test: its original
    is deleted for good."""

    def setUp(self):
        super().setUp()
        for name in ("delete_file", "send_to_recycle_bin"):
            patcher = mock.patch.object(recycle_bin, name, side_effect=AssertionError("a conversion used the Recycle Bin"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def clip(self, name, title=TITLE, taken=None):
        """A clip `name` in the folder (a .wmv `taken`: dated so in its XMP), made and recorded as the indexer reads it:
        (its path, its id)."""
        path = make_clip(os.path.join(self.folder, name), title, taken=taken)
        return path, self.index(path)[0]

    def convert(self, path, **kwargs):
        return convert.convert_clip(self.vl.library, EXIFTOOL, TOOLS, path, **kwargs)

    def held(self, path, *wanted):
        with field_values.session(EXIFTOOL) as et:
            return field_values.read_one(et, path, list(wanted))

    def assert_dated(self, path, taken, headers):
        """The clip at `path` dated by the carry as a save dates it: `taken` in its XMP, `headers` in every QuickTime
        header box, read box by box (in_place.headers_held)."""
        self.assertEqual([taken], fields.field_values(self.held(path, "XMP:DateTimeOriginal").get("XMP:DateTimeOriginal")))
        self.assertEqual({field: [headers] for field in fields.CLIP_HEADER_DATES}, in_place.headers_held(path))

    def row_of(self, photo_id):
        (path, tags, raw), = self.vl.rows("SELECT path, tags, raw_metadata FROM photos WHERE id = ?", photo_id)
        return path, json.loads(tags or "[]"), json.loads(raw or "{}")

    def changes(self):
        return self.vl.rows("SELECT id, operation, status FROM changes ORDER BY id")

    def converted_states(self, photo_id):
        """The states of the photo's file in the conversions of the journal (not in their carries)."""
        return [state for state, in self.vl.rows(
            "SELECT state FROM change_files WHERE photo_id = ? AND change_id IN (SELECT id FROM changes WHERE operation = ?)",
            photo_id, file_journal.CONVERT)]

    def convert_notes(self, photo_id):
        """The notes of the photo's file in the conversions of the journal."""
        return [note for note, in self.vl.rows(
            "SELECT note FROM change_files WHERE photo_id = ? AND change_id IN (SELECT id FROM changes WHERE operation = ?)",
            photo_id, file_journal.CONVERT)]

    def ask(self, photo_id, mode, path, **changed):
        """The panel's button `mode` on the clip `photo_id` at `path` (POST /api/clip/convert), with its stamp as the
        page's record has it (`size`: that many bytes off)."""
        stamp = photo_actions.file_stamp(path)
        body = {"id": photo_id, "mode": mode, "stamp": {"mtime": stamp[0], "size": stamp[1] + changed.get("size", 0)}}
        return self.post("clip/convert", body)

    def ended(self, reply):
        """What the panel reads by the ticket of `reply` once the conversion ended (/api/clip/converted)."""
        self.assertEqual(200, reply.status_code, reply.get_json())
        ticket = reply.get_json()["ticket"]
        convert.outcome(self.vl.library, ticket, wait=120)
        said = self.client.get("/library/api/clip/converted?ticket=%d" % ticket).get_json()
        self.assertTrue(said["done"], said)
        return said

    def leftovers(self):
        return sorted(name for name in os.listdir(self.folder) if name.endswith(clip_convert.SUFFIX))

    def settled(self):
        """The settle of the next start, a while later: a temp the killed run left is older than the scratch rule's
        minutes by then (SCRATCH_STALE, 0 here: a temp's own modified time set back would make it older than its
        original, which the rule reads as the original changed since)."""
        with mock.patch.object(file_changes, "SCRATCH_STALE", 0):
            file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertEqual([], [row for row in self.changes() if row[2] == "planned"], "every change finished: %s" % (
            self.vl.rows("SELECT change_id, state, note FROM change_files"),))

    def assert_converted(self, source, photo_id, title):
        target = clip_convert.target_of(source)
        made = video_frames.streams(TOOLS, target)
        self.assertEqual(("h264", "aac"), (made.video, made.audio), made.problem)
        path, _tags, raw = self.row_of(photo_id)
        self.assertEqual(paths.key(target), paths.key(path), "the row followed to the MP4")
        self.assertEqual(title, fields.own_title(raw), "the row says the title the copy holds")
        self.assertEqual(title, fields.own_title(self.held(target, *convert.READ)), "the copy holds the title")
        name = os.path.basename(source)
        self.assertEqual([name], fields.field_values(self.held(target, fields.PRESERVED_NAME).get(fields.PRESERVED_NAME)),
                         "the copy holds the name it had")
        self.assertEqual([], self.leftovers(), "no temp left")
        if not paths.same(source, target):
            self.assertFalse(os.path.exists(source), "the original deleted for good")
        return target


class TheConversion(AnOldClipInTheLibrary):

    def test_each_old_format_is_converted_its_row_followed_its_title_and_name_carried_the_original_deleted(self):
        for name in ("quay.wmv", "slipway.mpg", "breakwater.avi"):
            with self.subTest(name):
                source, photo_id = self.clip(name)
                title = fields.own_title(self.row_of(photo_id)[2])
                self.assertEqual("" if name.endswith(".mpg") else TITLE, title, "an MPEG program stream holds no title")
                result = self.convert(source)
                self.assertEqual(1, result.changed, result.message())
                self.assertTrue(result.ok, result.message())
                self.assertFalse(os.path.exists(source), "the original is gone from its folder")
                self.assert_converted(source, photo_id, title)
        operations = [(operation, status) for _id, operation, status in self.changes()]
        self.assertEqual(3, operations.count((file_journal.CONVERT, "applied")), operations)

    def test_an_mp4_of_mpeg4_part_2_is_converted_under_its_own_name_its_tags_carried(self):
        source, photo_id = self.clip("dock.mp4")
        saved = file_changes.write_fields(self.vl.library, "save", EXIFTOOL, [source], list(tagging.KEYWORD_READ),
                                          tagging.keywords_planner(self.vl.library, lambda _p, _h: (["Trips/Coast"], ())))
        self.assertEqual(1, saved.changed, saved.message())
        result = self.convert(source)
        self.assertEqual(1, result.changed, result.message())
        target = self.assert_converted(source, photo_id, TITLE)
        self.assertEqual(source, target)
        self.assertEqual(["Trips/Coast"], self.row_of(photo_id)[1], "the row holds its tag")
        self.assertIn("Trips/Coast", fields.field_values(self.held(target, "XMP:Subject").get("XMP:Subject")))

    def test_history_says_a_conversion_is_not_undone(self):
        source, _photo_id = self.clip("quay.wmv")
        result = self.convert(source)
        self.assertEqual(1, result.changed, result.message())
        reply = self.post("history/%d/undo" % result.details["change"], {})
        self.assertIn("not undone, the original is deleted", json.dumps(reply.get_json()))


class CutShort(AnOldClipInTheLibrary):
    """The conversion killed at each of its steps (convert.STEPS), then settled as the next start settles it."""

    def setUp(self):
        super().setUp()
        self.source, self.photo_id = self.clip("quay.wmv")
        self.before = self.read_bytes(self.source)

    def killed_at(self, step, source=None):
        with mock.patch.object(convert, "_reached", side_effect=crash_at(step)):
            with self.assertRaises(Crash):
                self.convert(source or self.source)

    def assert_as_it_was(self):
        self.assertEqual(self.before, self.read_bytes(self.source), "the original as it was")
        self.assertFalse(os.path.exists(clip_convert.target_of(self.source)), "no MP4")
        self.assertEqual([], self.leftovers(), "no temp left")
        self.assertEqual(paths.key(self.source), paths.key(self.row_of(self.photo_id)[0]), "the row where it was")

    def test_killed_before_its_copy_was_recorded_the_temp_is_removed_and_the_original_kept(self):
        self.killed_at("temp verified")
        self.assertEqual(1, len(self.leftovers()), "the killed run's temp is there")
        self.settled()
        self.assert_as_it_was()

    def test_killed_at_each_step_after_its_copy_was_recorded_it_is_finished_by_the_settle(self):
        for n, step in enumerate(("renamed", "rows followed", "carried", "original deleted")):
            with self.subTest(step):
                source, photo_id = self.clip("slipway%d.wmv" % n, taken=WMV_TAKEN)
                self.killed_at(step, source)
                self.settled()
                self.assertEqual(["done"], self.converted_states(photo_id), "the conversion done, never a conflict")
                self.assertFalse(os.path.exists(source), "the original deleted")
                self.assert_dated(self.assert_converted(source, photo_id, TITLE), WMV_TAKEN, WMV_TAKEN)

    def test_its_copy_is_on_its_disk_before_it_is_recorded_so_a_power_cut_after_the_record_finds_it_whole(self):
        # ffmpeg does not flush: cut after the record and before the disk's cache was written, the copy's name, file id
        # and length would be as recorded and its bytes not, and the next start's settle would put it in place and
        # delete the original. The copy is flushed to its disk first; the settle trusts what was recorded.
        real_fsync, real_set_after, flushed, recorded = os.fsync, file_journal.set_after, [], []

        def fsync(fd):
            real_fsync(fd)
            flushed.append(os.fstat(fd).st_ino)

        def set_after(conn, file_id, after):
            if "made" in after:
                recorded.append((after["made"]["ino"], list(flushed)))
            return real_set_after(conn, file_id, after)

        with mock.patch("os.fsync", side_effect=fsync), mock.patch.object(file_journal, "set_after", side_effect=set_after):
            result = self.convert(self.source)
        self.assertEqual(1, result.changed, result.message())
        ((made, before_it),) = recorded
        self.assertIn(made, before_it, "its copy flushed to its disk before it was recorded as made")

    def test_the_time_its_copy_is_given_to_reach_its_disk_grows_with_the_copy(self):
        # A large clip on a share takes longer to flush than 120 s would allow: refused, its ffmpeg minutes wasted.
        asked = []
        real_bounded = clip_convert.shares.bounded

        def bounded(folder, call, seconds, **how):
            asked.append(seconds)
            return real_bounded(folder, call, seconds, **how)

        with mock.patch.object(clip_convert.shares, "bounded", side_effect=bounded), \
                mock.patch.object(clip_convert.shares, "stamp_of", return_value=(1.0, 3 * 1024 ** 3)):
            temp = os.path.join(self.vl.pictures, "flush-probe.tmp")
            with open(temp, "wb") as handle:
                handle.write(b"x")
            self.assertIsNone(clip_convert._flushed(temp))
        self.assertEqual([clip_convert.VERIFY_SECONDS + 3 * 1024 ** 3 / clip_convert.READ_RATE], asked)

    def test_a_share_that_stops_answering_as_ffmpeg_ends_refuses_the_flush_without_waiting_on_it(self):
        temp = os.path.join(self.vl.pictures, "flush-probe-away.tmp")
        with open(temp, "wb") as handle:
            handle.write(b"x")
        with mock.patch.object(clip_convert.shares, "stamp_of", return_value=clip_convert.shares.UNANSWERED), \
                mock.patch("os.fsync") as flushed:
            said = clip_convert._flushed(temp)
        self.assertEqual(clip_convert.NOT_FLUSHED % "the share did not answer", said)
        flushed.assert_not_called()

    def test_a_copy_its_disk_will_not_take_is_removed_the_original_kept_and_nothing_recorded(self):
        with mock.patch("os.fsync", side_effect=OSError(5, "Access is denied")):
            result = self.convert(self.source)
        self.assertEqual((0, clip_convert.NOT_FLUSHED % "Access is denied"), (result.changed, result.message()))
        self.assert_as_it_was()
        self.assertEqual([], [after for (after,) in self.vl.rows("SELECT fields_after FROM change_files")
                              if "made" in json.loads(after or "{}")], "no copy recorded as made")

    def test_its_share_asleep_as_its_copy_is_put_in_place_keeps_the_original_and_the_next_click_converts_it(self):
        # The share stops answering once ffmpeg's copy is verified: every look at the folder hangs, then says "not found"
        # (a stand-in for the share, as test_a_share_asleep's), and a bounded look waits a moment, not Windows' minutes.
        asleep, awake, under = threading.Event(), threading.Event(), os.path.normcase(self.folder)
        self.addCleanup(awake.set)
        shares.forget()
        self.addCleanup(shares.forget)

        def hang(real):
            def look(path, *args, **kwargs):
                if asleep.is_set() and os.path.normcase(str(path)).startswith(under):
                    awake.wait(1)
                    raise FileNotFoundError(2, "The network path was not found")
                return real(path, *args, **kwargs)
            return look
        with mock.patch.object(os, "stat", hang(os.stat)), mock.patch.object(os, "remove", hang(os.remove)), \
                mock.patch.object(os, "rename", hang(os.rename)), mock.patch.object(shares, "LISTING_WAIT", 0.1), \
                mock.patch.object(convert, "_reached", side_effect=lambda step: step == "temp verified" and asleep.set()):
            result = self.convert(self.source)
            asleep.clear()
            awake.set()
        shares.forget()
        self.assertEqual(0, result.changed, result.message())
        self.assertNotIn("removed and", result.message(), "it says the copy could not be removed, and nothing else")
        self.assertIn("as it was. Its unfinished copy could not be removed just now (the share did not answer)",
                      result.message(), "two sentences")
        self.assertEqual(self.before, self.read_bytes(self.source), "the original as it was")
        self.assertEqual(paths.key(self.source), paths.key(self.row_of(self.photo_id)[0]), "the row where it was")
        self.assertEqual(["writing"], self.converted_states(self.photo_id), "left for the next settle")
        again = self.convert(self.source)
        self.assertEqual(1, again.changed, again.message())
        self.assert_converted(self.source, self.photo_id, TITLE)

    @contextlib.contextmanager
    def on_a_share_that_falls_asleep_at(self, step, dropped=False):
        """The clip's folder on a share (a stand-in, as test_a_share_asleep's) that falls asleep at the conversion's
        `step` (convert.STEPS): from then, a look in the folder says "not found" at once, the words Windows has for a file
        deleted (WinError 53, #1543), and the share's root is not there. `dropped`, a share whose connection drops
        instead: a look says "the specified network name is no longer available" (WinError 64, which Python raises as a
        plain OSError, errno 22). Awake again after the block, nothing remembered. Yields the Event that is set while
        it sleeps."""
        under, root = os.path.normcase(self.folder), os.path.normcase(os.path.splitdrive(self.folder)[0] + os.sep)
        asleep, real_stat, real_exists = threading.Event(), os.stat, os.path.exists
        shares.forget()
        self.addCleanup(shares.forget)

        def stat(path, *args, **kwargs):
            if asleep.is_set() and os.path.normcase(str(path)).startswith(under):
                if dropped:
                    raise OSError(22, "The specified network name is no longer available", str(path), 64)
                raise FileNotFoundError(2, "The network path was not found")
            return real_stat(path, *args, **kwargs)

        def exists(path):
            return not (asleep.is_set() and not dropped and os.path.normcase(str(path)) == root) and real_exists(path)
        with mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(under)), \
                mock.patch.object(os, "stat", stat), mock.patch.object(os.path, "exists", exists), \
                mock.patch.object(convert, "_reached", side_effect=lambda reached: reached == step and asleep.set()):
            try:
                yield asleep
            finally:
                asleep.clear()
        shares.forget()

    def test_its_share_asleep_once_its_original_is_deleted_says_it_converted_and_how_large_its_copy_is(self):
        # Was: the copy's size read by an unbounded look at the share asleep, None, and a TypeError raised out of a
        # conversion that was done: the panel said it could not be converted.
        with self.on_a_share_that_falls_asleep_at("original deleted"):
            result = self.convert(self.source)
        self.assertEqual((1, True), (result.changed, result.ok), result.message())
        target = clip_convert.target_of(self.source)
        self.assertEqual(os.path.getsize(target), result.details["size_after"], "the copy's size, as its row records it")
        self.assertEqual(["done"], self.converted_states(self.photo_id))
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_its_share_asleep_as_its_original_is_deleted_keeps_both_says_so_and_the_next_settle_deletes_it(self):
        # Asleep once the copy is carried: both files kept, the owner told so, the file left `writing`; awake, the next
        # settle deletes the original. Was: the original's unbounded look said None, taken as "changed", its delete
        # skipped and the conversion marked done in silence, the original left beside its copy for the next sync to find
        # as a new clip (#2890).
        with self.on_a_share_that_falls_asleep_at("carried"):
            result = self.convert(self.source)
        kept = convert.ORIGINAL_KEPT % "its share did not answer"
        self.assertEqual((1, kept, kept), (result.changed, result.details.get("original_kept"), result.message()))
        self.assertEqual(self.before, self.read_bytes(self.source), "the original kept")
        self.assertEqual(["writing"], self.converted_states(self.photo_id), "left for the next settle")
        self.assertEqual([kept], self.convert_notes(self.photo_id), "the original kept, noted")
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertEqual(["done"], self.converted_states(self.photo_id))
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_its_share_dropping_as_its_original_is_deleted_keeps_both_says_so_and_a_settle_waits_for_it(self):
        # The share's connection drops once the copy is carried (WinError 64): the look at the original says neither
        # "there" nor "not found". Both files kept, said and noted, the file left `writing`; a settle while it is still
        # down leaves it so (never a conflict); back, the next settle deletes the original. Was: the drop read as an
        # original changed since, the conversion marked done in silence, the original beside its copy.
        with self.on_a_share_that_falls_asleep_at("carried", dropped=True) as down:
            result = self.convert(self.source)
            self.assertEqual(["writing"], self.converted_states(self.photo_id), "left for the next settle")
            kept = convert.ORIGINAL_KEPT % convert.NOT_LOOKED_AT
            self.assertEqual((1, kept, kept), (result.changed, result.details.get("original_kept"), result.message()))
            self.assertEqual([kept], self.convert_notes(self.photo_id), "the original kept, noted")
            down.set()
            file_changes.settle(self.vl.library, EXIFTOOL)
            self.assertEqual(["writing"], self.converted_states(self.photo_id), "a settle while it is down waits")
        self.assertEqual(self.before, self.read_bytes(self.source), "the original kept")
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertEqual(["done"], self.converted_states(self.photo_id))
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_a_settle_while_only_the_files_of_the_clip_cannot_be_read_waits_and_never_calls_the_clip_changed(self):
        # The share's look at the original and its copy fails (WinError 64) once the copy is carried, while a look at the
        # temp, a name that is not there, still says "not found": a settle then must not conclude from the copy it
        # cannot find that the conversion was never made (a conflict, the original and its copy left beside each other
        # for the next sync). It waits for the look, `writing`; back, it deletes the original. Only the settle's own
        # look at the two files (convert.settle_change) tells this: nothing else of it fails.
        target = clip_convert.target_of(self.source)
        down, real_stat = threading.Event(), os.stat

        def stat(path, *args, **kwargs):
            if down.is_set() and paths.key(str(path)) in (paths.key(self.source), paths.key(target)):
                raise OSError(22, "The specified network name is no longer available", str(path), 64)
            return real_stat(path, *args, **kwargs)
        shares.forget()
        self.addCleanup(shares.forget)
        with mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(
                os.path.normcase(self.folder))), mock.patch.object(os, "stat", stat), \
                mock.patch.object(convert, "_reached", side_effect=lambda step: step == "carried" and down.set()):
            result = self.convert(self.source)
            self.assertEqual(1, result.changed, result.message())
            self.assertEqual(["writing"], self.converted_states(self.photo_id), "left for the next settle")
            file_changes.settle(self.vl.library, EXIFTOOL)
            self.assertEqual(["writing"], self.converted_states(self.photo_id), "a settle while it is down waits")
            down.clear()
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertEqual(["done"], self.converted_states(self.photo_id))
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_its_original_changed_by_another_program_after_its_copy_is_carried_is_kept_said_and_the_conversion_done(self):
        # Another program writes the original between the carry and its delete: never deleted (it is not the file the
        # conversion found), the conversion done, and the owner told both are kept. Was: done in silence, the changed
        # original beside its copy for the next sync to find.
        def rewritten(step):
            if step == "carried":
                with open(self.source, "ab") as handle:
                    handle.write(b"another program's bytes")
        with mock.patch.object(convert, "_reached", side_effect=rewritten):
            result = self.convert(self.source)
        self.assertFalse(result.ok, "the original kept is said, never in silence")
        said = convert.ORIGINAL_CHANGED
        self.assertEqual((1, said, said), (result.changed, result.details.get("original_kept"), result.message()))
        self.assertEqual(["done"], self.converted_states(self.photo_id), "finished: no settle would ever delete it")
        self.assertEqual([said], self.convert_notes(self.photo_id), "noted in its change")
        self.assertTrue(self.read_bytes(self.source).endswith(b"another program's bytes"), "the changed original kept")
        target = clip_convert.target_of(self.source)
        self.assertEqual(paths.key(target), paths.key(self.row_of(self.photo_id)[0]), "the row at the copy")

    def test_its_original_deleted_by_another_program_while_its_share_answers_is_taken_as_gone(self):
        # The same "not found" of the original once the copy is carried, while the share answers: it is gone, the
        # conversion done, the share not taken as away.
        under = os.path.normcase(self.folder)
        shares.forget()
        self.addCleanup(shares.forget)
        with mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(under)), \
                mock.patch.object(convert, "_reached", side_effect=lambda step: step == "carried" and os.remove(self.source)):
            result = self.convert(self.source)
        self.assertEqual((1, True), (result.changed, result.ok), result.message())
        self.assertEqual(["done"], self.converted_states(self.photo_id))
        self.assertIsNone(shares.away_since(shares.share_of(self.folder)), "a file deleted took its share as away")
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_an_mp4_of_its_own_name_killed_between_its_delete_and_its_rename_is_put_in_place_by_the_settle(self):
        source, photo_id = self.clip("dock.mp4")
        self.killed_at("original deleted", source)
        self.assertEqual(["dock.mp4" + clip_convert.SUFFIX], self.leftovers(), "the copy in its temp, the original deleted")
        self.assertFalse(os.path.exists(source))
        self.settled()
        self.assert_converted(source, photo_id, TITLE)


class Refused(AnOldClipInTheLibrary):

    def setUp(self):
        super().setUp()
        self.source, self.photo_id = self.clip("quay.wmv")
        self.before = self.read_bytes(self.source)

    def test_ffmpeg_failing_leaves_the_original_and_no_temp(self):
        with mock.patch.object(clip_convert, "VIDEO", ("-c:v", "no_such_encoder")):
            result = self.convert(self.source)
        self.assertEqual(0, result.changed)
        self.assertIn("ffmpeg could not convert it", result.message())
        self.assertEqual(self.before, self.read_bytes(self.source))
        self.assertEqual([], self.leftovers())
        self.assertFalse(os.path.exists(clip_convert.target_of(self.source)))
        self.assertEqual([], [(op, status) for _id, op, status in self.changes() if op == file_journal.CONVERT],
                         "a conversion that wrote nothing leaves no change")

    def test_a_clip_opened_in_a_player_after_it_was_planned_keeps_its_original_and_copy_and_the_next_settle_deletes_it(self):
        # Refused open before the plan (HELD_OPEN), it is opened as ffmpeg ends: a handle that does not share delete, as a
        # player's, so the delete of the original, last, fails.
        held = []
        self.addCleanup(lambda: [handle.close() for handle in held])
        with mock.patch.object(convert, "_reached", side_effect=lambda step: step == "temp verified" and held.append(
                open(self.source, "rb"))):
            result = self.convert(self.source)
        self.assertEqual(1, result.changed, "the copy is in place")
        self.assertFalse(result.ok)
        self.assertTrue(result.details["original_kept"].startswith(convert.ORIGINAL_KEPT.split("%s")[0]), result.details)
        self.assertEqual(self.before, self.read_bytes(self.source), "the original kept")
        target = clip_convert.target_of(self.source)
        self.assertEqual(paths.key(target), paths.key(self.row_of(self.photo_id)[0]), "the row at the MP4")
        self.assertEqual(("h264", "aac"), tuple(getattr(video_frames.streams(TOOLS, target), k) for k in ("video", "audio")),
                         "the verified copy kept")
        self.assertEqual(["writing"], self.converted_states(self.photo_id))
        held.pop().close()
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertFalse(os.path.exists(self.source), "the next settle deleted it")
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_an_mp4_of_its_own_name_whose_original_cannot_be_deleted_is_not_converted(self):
        source, photo_id = self.clip("dock.mp4")
        before = self.read_bytes(source)
        with mock.patch.object(convert, "_removed_for_good", return_value="it is open in another program"):
            result = self.convert(source)
        self.assertEqual((0, convert.NOT_DELETED % "it is open in another program"), (result.changed, result.message()))
        self.assertEqual(before, self.read_bytes(source), "the original kept: its copy would take its name")
        self.assertEqual([], self.leftovers())

    def test_a_clip_another_program_writes_while_it_is_read_is_refused_before_anything_is_planned(self):
        # A player's or an editor's save lands while the conversion reads the clip (ExifTool, its XMP, ffprobe: seconds),
        # before it plans: the clip is not the one it read, so nothing is planned, converted or deleted.
        streams, written = video_frames.streams, []

        def written_meanwhile(tools, path, *args, **kwargs):
            if not written:
                written.append(path)
                with open(self.source, "ab") as handle:
                    handle.write(b"another program's bytes")
            return streams(tools, path, *args, **kwargs)

        with mock.patch.object(video_frames, "streams", side_effect=written_meanwhile):
            result = self.convert(self.source)
        self.assertEqual((0, convert.CHANGED), (result.changed, result.message()))
        self.assertEqual(self.before + b"another program's bytes", self.read_bytes(self.source), "the clip as it is")
        self.assertEqual([], [op for _id, op, _status in self.changes() if op == file_journal.CONVERT], "nothing planned")
        self.assertEqual([], self.leftovers())
        self.assertEqual(paths.key(self.source), paths.key(self.row_of(self.photo_id)[0]), "the row where it was")

    def test_an_mp4_of_its_own_name_changed_while_it_was_converted_is_never_deleted(self):
        source, _photo_id = self.clip("dock.mp4")

        def changed(step):
            if step == "temp verified":
                with open(source, "ab") as handle:
                    handle.write(b"another program's bytes")
        with mock.patch.object(convert, "_reached", side_effect=changed):
            result = self.convert(source)
        self.assertEqual((0, convert.CHANGED), (result.changed, result.message()))
        self.assertTrue(self.read_bytes(source).endswith(b"another program's bytes"), "the changed original kept")
        self.assertEqual([], self.leftovers())

    def test_a_copy_that_could_not_be_removed_is_said_left_never_said_removed(self):
        # Something holds the copy open as the conversion is let go (a virus scanner, a preview), so its removal fails:
        # the refusal says the copy is left, after a sentence that claims no removal of it (#2884). Each way of being
        # let go with its copy in the temp: the original changed meanwhile, the copy not put in its place (the same
        # handle stops the rename), an .mp4 of its own name whose original a player holds.
        def held_at_verified(source, also_source=False, append=False):
            temp = clip_convert.temp_of(clip_convert.target_of(source))
            handles = []
            self.addCleanup(lambda: [handle.close() for handle in handles])

            def hold(step):
                if step != "temp verified":
                    return
                if append:
                    with open(source, "ab") as handle:
                        handle.write(b"another program's bytes")
                handles.append(open(temp, "rb"))
                if also_source:
                    handles.append(open(source, "rb"))
            return temp, hold

        cases = (("changed", "inlet.wmv", convert.CHANGED, {"append": True}),
                 ("not renamed", "estuary.wmv", convert.NOT_RENAMED, {}),
                 ("not deleted", "lagoon.mp4", convert.NOT_DELETED, {"also_source": True}))
        for label, name, sentence, how in cases:
            with self.subTest(label):
                source, _photo_id = self.clip(name)
                temp, hold = held_at_verified(source, **how)
                with mock.patch.object(convert, "_reached", side_effect=hold):
                    result = self.convert(source)
                said = result.message()
                first, _stop, rest = said.partition(". ")
                self.assertEqual(0, result.changed, said)
                self.assertTrue(first.startswith(sentence.split("%s")[0]), said)
                self.assertNotIn("removed", first, "a removal claimed, then denied")
                self.assertRegex(said, r"as it (is|was)\. Its unfinished copy could not be removed just now \(.+\); it is "
                                       r"removed when the change is settled\.$", "two whole sentences")
                self.assertTrue(os.path.exists(temp), "the copy is there, as said")

    def test_a_share_that_does_not_answer_at_its_converted_name_is_said_away_not_taken(self):
        real, target = shares.stamp_of, clip_convert.target_of(self.source)

        def stamp_of(path, *args, **kwargs):
            return shares.UNANSWERED if paths.same(path, target) else real(path, *args, **kwargs)
        with mock.patch.object(shares, "stamp_of", side_effect=stamp_of), mock.patch.object(clip_convert, "convert") as ffmpeg:
            result = self.convert(self.source)
        self.assertEqual((0, convert.AWAY), (result.changed, result.message()))
        ffmpeg.assert_not_called()
        self.assertEqual(self.before, self.read_bytes(self.source))


class TwoLibrariesHoldingTheFolder(AnOldClipInTheLibrary):
    """A second library holding the folder, its rows recorded as its indexer reads the clips. The two share the files
    alone: the second's journal knows nothing of a conversion the first has under way, nor of one it finished."""

    def setUp(self):
        super().setUp()
        self.other = ViewLibrary(self, "other", home=self.home)

    def held_there(self, path):
        """The id of the second library's row of `path`, recorded as its indexer reads it."""
        (record,) = photo_rows.read_whole(EXIFTOOL, [path])
        photo_rows.added(self.other.conn, record["path"])   # the index adds the folder it is given first
        photos.record_indexed(self.other.conn, record["path"], record)
        photo_id = photos.ensure_row(self.other.conn, record["path"])
        self.other.conn.commit()
        return photo_id

    def asked_there_meanwhile(self, source, moment):
        """The first library's conversion of `source`, the second asked to convert it as the first reaches clip_convert's
        `moment` ("convert": its temp claimed and empty; "verified": its copy made, nothing holding the clip open): (the
        first's Result, the second's)."""
        temp, real, seconds = clip_convert.temp_of(clip_convert.target_of(source)), getattr(clip_convert, moment), []

        def meanwhile(*args, **kwargs):
            size = os.path.getsize(temp)
            seconds.append(convert.convert_clip(self.other.library, EXIFTOOL, TOOLS, source))
            self.assertEqual(size, os.path.getsize(temp), "the first's temp as it was")
            return real(*args, **kwargs)
        with mock.patch.object(clip_convert, moment, side_effect=meanwhile):
            first = self.convert(source)
        (second,) = seconds
        return first, second

    def test_the_second_asked_to_convert_the_clip_meanwhile_is_refused_and_leaves_the_firsts_copy(self):
        # The second's ffmpeg, refused by the first's temp there, removed it (#2858): the claim refuses it first.
        for n, moment in enumerate(("convert", "verified")):
            with self.subTest(moment):
                source, photo_id = self.clip("quay%d.wmv" % n, taken=WMV_TAKEN)
                self.held_there(source)
                first, second = self.asked_there_meanwhile(source, moment)
                self.assertEqual((0, convert.TEMP_TAKEN), (second.changed, second.message()))
                self.assertIsNone(second.details["change"], "its change wrote nothing and is erased (#2974): none to name")
                self.assertEqual(1, first.changed, first.message())
                self.assert_dated(self.assert_converted(source, photo_id, TITLE), WMV_TAKEN, WMV_TAKEN)

    def test_the_second_finds_the_clip_at_its_mp4_by_the_name_it_kept(self):
        source, photo_id = self.clip("quay.wmv")
        there = self.held_there(source)
        result = self.convert(source)
        self.assertEqual(1, result.changed, result.message())
        target = self.assert_converted(source, photo_id, TITLE)
        synced = sync.sync(self.other.library, apply=True, exiftool_path=EXIFTOOL, roots=[self.vl.pictures])
        self.assertTrue(synced.ok, synced.message())
        self.assertEqual([(there, paths.key(target))],
                         [(i, paths.key(path)) for i, path in self.other.rows("SELECT id, path FROM photos")],
                         "its one row, with its faces and names, at the MP4")


class AClipHoldingWindowsTags(AnOldClipInTheLibrary):
    """A clip tagged long ago in Windows Photo Gallery: its Tags in the WM/Category of its Xtra (fields.CLIP_CATEGORY),
    which the library never reads as tags (DECISIONS 2026-11-07). Convert and Make smaller carry each into the copy's
    tags, beside the tags it holds, when the tag tree holds it (under a people root, as a person): the rule of the one-time
    move of 2026-11-12; the copy holds no Xtra, and is read back holding what it carries before a renamed original is
    deleted for good. A Windows tag it cannot carry refuses the clip, naming the tag, before anything is planned.

    The live shapes (photo_index, read-only, 2026-11-12): twelve clips still hold Windows Tags, nine of them XMP tags too;
    one an .mp4 of MPEG-4 Part 2 (Convert, under its own name), eleven an H.264 .mov (Make smaller, an .mp4 of another
    name); two all carryable, ten holding a tag the tree does not hold, none a non-person under People."""

    #: The live shapes: (name, the clip's button, its title as made).
    SHAPES = (("dock.mp4", convert.OLD_FORMAT, TITLE), ("harbour.mov", convert.SMALLER, ""))

    def setUp(self):
        super().setUp()
        self.vl.tree(patched.WINDOWS_TAGS[0], face_root="People")   # the base's tree holds Trips/Coast, Harbour, Pier

    def windows_clip(self, name, texts=patched.WINDOWS_TAGS):
        """A clip `name` (an .mp4 of MPEG-4 Part 2, or a large H.264 .mov) given an Xtra holding `texts` as Windows writes
        one, recorded as the indexer reads it, then tagged Trips/Harbour in its XMP through the journal: (its path, its
        id)."""
        path = os.path.join(self.folder, name)
        path = make_mov(path, 0) if name.endswith(".mov") else make_clip(path, TITLE)
        patched.with_windows_tags(path, texts)
        photo_id = self.index(path)[0]
        saved = file_changes.write_fields(self.vl.library, "save", EXIFTOOL, [path], list(tagging.KEYWORD_READ),
                                          tagging.keywords_planner(self.vl.library, lambda _p, _h: (["Trips/Harbour"], ())))
        self.assertEqual(1, saved.changed, saved.message())
        self.assertEqual((["Trips/Harbour"], list(texts) or None), patched.windows_tags_read(path))
        return path, photo_id

    def assert_carried(self, source, photo_id, title):
        """The clip converted (a renamed original deleted for good), its Windows Tags in its copy's tags after the one it
        held, and no Windows Tags left."""
        target = self.assert_converted(source, photo_id, title)
        carried = ["Trips/Harbour"] + list(patched.WINDOWS_TAGS)
        self.assertEqual((carried, None), patched.windows_tags_read(target), "its tags in its XMP, no Windows Tags")
        with open(target, "rb") as handle:
            self.assertIsNone(containers.read(handle).xtra, "the copy holds no Xtra")
        self.assertEqual(sorted(carried), sorted(self.row_of(photo_id)[1]), "the row reads them")

    def test_its_windows_tags_the_tree_holds_are_carried_into_its_copys_tags_and_the_copy_holds_no_xtra(self):
        for name, reason, title in self.SHAPES:
            with self.subTest(name):
                source, photo_id = self.windows_clip(name)
                result = self.convert(source, reason=reason)
                self.assertEqual((1, True), (result.changed, result.ok), result.message())
                self.assert_carried(source, photo_id, title)

    def test_a_windows_tag_naming_a_level_of_a_tag_it_holds_is_carried_as_a_read_gives_it_back_and_finishes(self):
        # "Trips" beside its "Trips/Harbour": a read of the copy leaves the level out (vocabulary.extract_tags), so a carry
        # planned to hold it would never read back as carried, and the conversion would never finish.
        source, photo_id = self.windows_clip("harbour.mov", ("Trips", "Trips/Coast"))
        result = self.convert(source, reason=convert.SMALLER)
        self.assertEqual((1, True), (result.changed, result.ok), result.message())
        target = self.assert_converted(source, photo_id, "")
        self.assertEqual(["Trips/Harbour", "Trips/Coast"], self.row_of(photo_id)[1], "the row reads its tags")
        self.assertIsNone(patched.windows_tags_read(target)[1], "no Windows Tags")
        self.assertEqual(["done"], self.converted_states(photo_id))

    def test_convert_of_a_clip_holding_a_windows_tag_it_cannot_carry_is_refused_naming_it_before_anything_is_planned(self):
        self.vl.forget("Trips/Pier")
        self.vl.tree("People/Cousins/Ash Pell", face_root="People")
        for name, texts, cannot in (
                ("pier.mp4", patched.WINDOWS_TAGS, "Trips/Pier"),                      # the ten live: not in the tree
                ("cousins.mp4", ("Trips/Coast", "People/Cousins"), "People/Cousins")):  # under People, no person
            with self.subTest(cannot):
                source, photo_id = self.windows_clip(name, texts)
                before = self.read_bytes(source)
                with mock.patch.object(clip_convert, "convert") as ffmpeg:
                    said = self.ended(self.ask(photo_id, "convert", source))
                ffmpeg.assert_not_called()
                self.assertEqual((False, convert.CANNOT_CARRY % cannot), (said["success"], said["error"]))
                self.assertEqual(before, self.read_bytes(source), "the clip as it was")
                self.assertEqual([], self.vl.rows("SELECT id FROM changes WHERE operation = ?", file_journal.CONVERT))

    def test_killed_before_its_carry_the_settle_carries_its_windows_tags_from_the_journal(self):
        for name, reason, title in self.SHAPES:
            with self.subTest(name):
                source, photo_id = self.windows_clip(name)
                with mock.patch.object(convert, "_reached", side_effect=crash_at("rows followed")):
                    with self.assertRaises(Crash):
                        self.convert(source, reason=reason)
                with mock.patch.object(file_changes, "SCRATCH_STALE", 0):
                    file_changes.settle(self.vl.library, EXIFTOOL)
                self.assert_carried(source, photo_id, title)

    def test_a_renamed_copy_that_does_not_read_back_holding_its_carry_keeps_the_original_for_the_next_settle(self):
        # A carry that answers ok while the copy holds none of it: the original, the one other holder of its tags, is
        # kept. (An .mp4 converted under its own name has its original deleted before its copy takes the name: what it
        # carries is in the journal's plan until the settle writes it, test_killed_before_its_carry_...)
        source, photo_id = self.windows_clip("harbour.mov")
        before = self.read_bytes(source)
        with mock.patch.object(file_changes, "write_fields", return_value=Result()):
            result = self.convert(source, reason=convert.SMALLER)
        self.assertEqual(0, result.changed, result.message())
        self.assertIn("its tags, title or kept name could not be written into its converted copy", result.message())
        self.assertEqual(before, self.read_bytes(source), "the original kept")
        self.assertEqual(["writing"], self.converted_states(photo_id))
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assert_carried(source, photo_id, "")


class AClipHoldingXmpOfOtherPrograms(AnOldClipInTheLibrary):
    """A clip whose XMP other programs wrote (#2938; owner 2026-11-13: every XMP property is kept, those TagPup does not
    use too): its packet carried whole into its converted copy, what the conversion carries written over it. The live
    shapes (test_xmp_packet_edits_what_it_owns: Premiere's packet, 40 live; one grown by many saves, as the 1 over 64 KB
    is; Adobe XMP Core's unwrapped one, 39 live), in a top-level uuid box after moov, appended to an ffmpeg-made clip
    as a save in place appends one. An MPEG-4 Part 2 .mp4, converted under its own name: the conversion that deletes its
    original before its copy is in place. A clip holding none, and an old format (which the box walk does not read, so
    holds none to carry), convert as before: TheConversion."""

    def xmp_clip(self, name, *packets):
        """A clip `name` in the folder with an XMP box after its moov for each of `packets`, recorded as the indexer reads
        it: (its path, its id)."""
        path = make_clip(os.path.join(self.folder, name), TITLE)
        with open(path, "ab") as handle:
            for packet in packets:
                handle.write(patched.xmp_box(packet))
        return path, self.index(path)[0]

    def packet_of(self, path):
        """The packet of the one XMP box of the clip at `path`, read by the box walk alone."""
        with open(path, "rb") as handle:
            (_, box), = containers.read(handle).xmp
            handle.seek(box.payload)
            return handle.read(box.end - box.payload)

    def assert_as_it_was(self, source, photo_id, before):
        self.assertEqual(before, self.read_bytes(source), "the original as it was")
        self.assertEqual([], self.leftovers(), "no temp left")
        self.assertEqual(paths.key(source), paths.key(self.row_of(photo_id)[0]), "the row where it was")

    def test_every_property_its_packet_held_is_in_its_copy_with_what_it_carries_written_over_it(self):
        attribute = re.compile(rb'([\w]+:[\w]+)="([^"]*)"')
        for n, packet in enumerate((shapes.PREMIERE, shapes.premiere(), shapes.BARE)):
            with self.subTest(len(packet)):
                source, photo_id = self.xmp_clip("dock%d.mp4" % n, packet)
                result = self.convert(source)
                self.assertEqual(1, result.changed, result.message())
                target = self.assert_converted(source, photo_id, TITLE)
                copied = self.packet_of(target)
                self.assertEqual([], [pair for pair in dict.fromkeys(attribute.findall(packet))
                                      if b'%s="%s"' % pair not in copied], "every property it held is kept")
                for element in (b"<xmpMM:History>", b"<xmpMM:Pantry>"):
                    self.assertEqual(packet.count(element), copied.count(element))
                self.assertEqual(packet.count(b"stEvt:action="), copied.count(b"stEvt:action="))

    def test_a_clip_whose_xmp_no_patch_reads_is_refused_before_ffmpeg_and_left_as_it_was(self):
        source, photo_id = self.xmp_clip("dock.mp4", shapes.PREMIERE, shapes.BARE)
        before = self.read_bytes(source)
        with mock.patch.object(clip_convert, "convert", side_effect=AssertionError("ffmpeg ran")):
            result = self.convert(source)
        self.assertEqual(0, result.changed)
        self.assertEqual("its XMP is held in a way TagPup does not read (two XMP boxes, or one it does not know), so it "
                         "could not be carried into its converted copy; nothing was converted", result.message())
        self.assert_as_it_was(source, photo_id, before)
        self.assertEqual([], [op for _id, op, _status in self.changes() if op == file_journal.CONVERT], "nothing planned")

    def test_killed_once_its_copy_holds_the_packet_the_settle_removes_the_copy_and_keeps_the_original(self):
        source, photo_id = self.xmp_clip("dock.mp4", shapes.PREMIERE)
        before = self.read_bytes(source)
        temp, held = clip_convert.temp_of(clip_convert.target_of(source)), []

        def reached(step):
            if step == "temp verified":
                held.append(self.packet_of(temp))
                raise Crash(step)

        with mock.patch.object(convert, "_reached", side_effect=reached):
            with self.assertRaises(Crash):
                self.convert(source)
        self.assertEqual([shapes.PREMIERE], held, "its copy held the original's packet when it was verified")
        self.settled()
        self.assert_as_it_was(source, photo_id, before)
        self.assertEqual([], self.converted_states(photo_id), "taken out of the change")


def make_mov(path, crf, created=CREATED, apple=None):
    """A one-second H.264 and AAC clip that plays at `path`, a .mov (or an .mp4, by its extension), at x264's quality
    `crf`: 0 (lossless, far larger than the conversion's MP4) or 51 (smaller than it); dated as a camera dates it, by its
    container's creation time `created` (CREATED), and `apple` an iPhone's local time with its zone
    ("2018-03-02T09:15:00-0700", its Apple CreationDate, read before the creation time)."""
    container = "mp4" if path.endswith(".mp4") else "mov"
    phone = ["-metadata", "com.apple.quicktime.creationdate=" + apple, "-movflags", "use_metadata_tags"] if apple else []
    return corpus.clip(path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "1", "-c:v", "libx264",
                              "-preset", "ultrafast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac",
                              "-metadata", "creation_time=" + created] + phone + ["-f", container], TOOLS.ffmpeg)


class ThePanelButtons(AnOldClipInTheLibrary):
    """The clip panel's Convert and Make smaller (MP4), by id (POST /api/clip/convert): a batch of one on a thread of the
    server, the request answered at once with its ticket, how it ended read by the ticket (/api/clip/converted)."""

    def mov(self, name, crf, apple=None):
        path = make_mov(os.path.join(self.folder, name), crf, apple=apple)
        return path, self.index(path)[0]

    def held_up(self):
        """clip_convert.convert held until `go` is set (or the conversion is asked to stop), then run: (the patch, go,
        entered)."""
        real, go, entered = clip_convert.convert, threading.Event(), threading.Event()

        def converting(tools, source, temp, *args, **kwargs):
            entered.set()
            while not go.is_set() and not kwargs["stop"].is_set():
                go.wait(0.05)
            return real(tools, source, temp, *args, **kwargs)
        return mock.patch.object(clip_convert, "convert", side_effect=converting), go, entered

    def test_convert_by_id_answers_at_once_and_its_ticket_says_where_the_clip_went(self):
        source, photo_id = self.clip("quay.wmv")
        size = os.path.getsize(source)
        said = self.ended(self.ask(photo_id, "convert", source))
        target = clip_convert.target_of(source)
        self.assertEqual((True, True, paths.key(target)), (said["success"], said["renamed"], paths.key(said["new_path"])))
        self.assertEqual((size, os.path.getsize(target), os.path.getsize(target)),
                         (said["size_before"], said["size_after"], said["size"]), "the sizes and the MP4's stamp")
        self.assert_converted(source, photo_id, TITLE)

    def test_a_large_mov_made_smaller_is_an_mp4_and_the_mov_is_gone_for_good(self):
        source, photo_id = self.mov("harbour.mov", 0)
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertLess(said["size_after"], said["size_before"])
        target = clip_convert.target_of(source)
        self.assertEqual(("h264", "aac"), tuple(getattr(video_frames.streams(TOOLS, target), k) for k in ("video", "audio")))
        self.assertEqual(paths.key(target), paths.key(self.row_of(photo_id)[0]), "the row followed to the MP4")
        self.assertFalse(os.path.exists(source), "the .mov deleted for good")
        self.assertEqual([], self.leftovers())

    def taken(self, photo_id):
        (taken,), = self.vl.rows("SELECT taken FROM photos WHERE id = ?", photo_id)
        return taken

    def test_a_dated_mov_made_smaller_keeps_its_date_taken_in_the_row_its_xmp_and_every_header(self):
        source, photo_id = self.mov("harbour.mov", 0)
        taken = self.taken(photo_id)
        self.assertTrue(taken, "the .mov is dated by its QuickTime:CreateDate")
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertFalse(os.path.exists(source), "the .mov deleted for good")
        self.assertEqual(taken, self.taken(photo_id), "the row's Date Taken is the .mov's")
        # Its UTC creation time read as local, written as if UTC (DECISIONS 2026-11-13): ffmpeg's copy of it is cleared.
        self.assert_dated(clip_convert.target_of(source), taken, taken)

    def test_an_iphone_mov_dated_with_its_zone_keeps_the_zone_in_its_xmp_and_its_digits_in_every_header(self):
        source, photo_id = self.mov("harbour.mov", 0, apple="2018-03-02T09:15:00-0700")
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "dated by its Apple CreationDate")
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "the row's Date Taken is the .mov's")
        self.assert_dated(clip_convert.target_of(source), XMP_TAKEN, "2018:03:02 09:15:00")

    def test_an_mp4_dated_in_its_xmp_made_smaller_keeps_that_date_and_its_headers_take_it(self):
        # Its packet, carried whole, holds the date while ffmpeg left every header zero: the carry writes them.
        source, photo_id = self.mov("dock.mp4", 0)
        dated = file_changes.write_fields(self.vl.library, "save", EXIFTOOL, [source], ["XMP:DateTimeOriginal"],
                                          lambda _path, _held: file_changes.Plan(after={"XMP:DateTimeOriginal": XMP_TAKEN}))
        self.assertEqual(1, dated.changed, dated.message())
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "dated by its XMP, read before its QuickTime date")
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "the row's Date Taken is the one its XMP held")
        self.assert_dated(source, XMP_TAKEN, "2018:03:02 09:15:00")

    def test_an_old_wmv_is_dated_by_its_xmp_alone_never_by_its_creation_time(self):
        # A .wmv's ASF creation time is when its file was made, not its Date Taken (DECISIONS 2026-11-09, item 6).
        for name, taken in (("quay.wmv", None), ("slipway.wmv", WMV_TAKEN)):
            with self.subTest(name):
                source = make_clip(os.path.join(self.folder, name), TITLE, created=CREATED, taken=taken)
                photo_id = self.index(source)[0]
                self.assertEqual(taken, self.taken(photo_id))
                said = self.ended(self.ask(photo_id, "convert", source))
                self.assertTrue(said["success"], said)
                self.assertEqual(taken, self.taken(photo_id), "an undated old clip stays undated")
                self.assertEqual({field: [taken or ZERO] for field in fields.CLIP_HEADER_DATES},
                                 in_place.headers_held(clip_convert.target_of(source)), "its headers")

    def test_the_convert_replies_the_page_reads_are_written_for_its_test(self):
        # The panel's button as the page sends it (web/tagpup/media.js): {"id", "mode", "stamp"} (POST /api/clip/convert),
        # then the ticket asked until it ended (GET /api/clip/converted?ticket=). `start`: `started` (its ticket),
        # `stale` (a stamp the clip no longer has: 409, `changed_on_disk`). `end`: `under_way`, `converted` (a .wmv to its
        # MP4, renamed), `not_smaller` (a .mov whose MP4 is no smaller, left as it is), `not_its_plan` (Make smaller asked
        # of a clip that does not play), `original_kept` (converted, its original written by another program after its
        # copy was carried: both kept, the sentence in `original_kept`). The test's home is HOME; each stamp 1 and 1, the
        # ticket 1, each size before 2 and after 1 (after 2 when it was no smaller).
        found = {"start": {}, "end": {}}
        both = lambda reply: {"status": reply.status_code, "reply": reply.get_json()}  # noqa: E731
        source, photo_id = self.clip("quay.wmv")
        found["start"]["stale"] = both(self.ask(photo_id, "convert", source, size=1))
        held, go, entered = self.held_up()
        with held:
            started = self.ask(photo_id, "convert", source)
            self.assertTrue(entered.wait(60), "ffmpeg reached")
            asked = self.client.get("/library/api/clip/converted?ticket=%d" % started.get_json()["ticket"])
            go.set()
            found["end"]["converted"] = {"status": 200, "reply": self.ended(started)}
        found["start"]["started"], found["end"]["under_way"] = both(started), both(asked)
        mov, mov_id = self.mov("harbour.mov", 51)
        found["end"]["not_smaller"] = {"status": 200, "reply": self.ended(self.ask(mov_id, "shrink", mov))}
        old, old_id = self.clip("slipway.wmv")
        found["end"]["not_its_plan"] = {"status": 200, "reply": self.ended(self.ask(old_id, "shrink", old))}
        kept, kept_id = self.clip("breakwater.wmv")

        def rewritten(step):
            if step == "carried":
                with open(kept, "ab") as handle:
                    handle.write(b"another program's bytes")
        with mock.patch.object(convert, "_reached", side_effect=rewritten):
            found["end"]["original_kept"] = {"status": 200, "reply": self.ended(self.ask(kept_id, "convert", kept))}
        self.assertEqual(convert.ORIGINAL_CHANGED, found["end"]["original_kept"]["reply"].get("original_kept"))
        self.assertEqual(({"stale": 409, "started": 200}, [True, False, False], {"done": False}),
                         ({name: each["status"] for name, each in found["start"].items()},
                          [found["end"][name]["reply"]["success"] for name in ("converted", "not_smaller", "not_its_plan")],
                          found["end"]["under_way"]["reply"]))
        for name, each in found["end"].items():
            reply = each["reply"]
            reply.update({key: 1 for key in ("mtime", "size") if key in reply})
            if "size_before" in reply:
                reply.update(size_before=2, size_after=2 if name == "not_smaller" else 1)
        found["start"]["started"]["reply"]["ticket"] = 1
        text = json.dumps(found).replace(json.dumps(self.home.root)[1:-1], "HOME")
        self.assertNotIn(json.dumps(os.path.basename(self.home.root))[1:-1], text, "a path of the test's home left in")
        text = json.dumps(json.loads(text), sort_keys=True, indent=1) + "\n"
        page_fixtures.hold_to(self, CONVERT_REPLIES, text, "tests.test_a_clip_converted", "the clip panel's Convert")

    def test_a_mov_whose_mp4_is_no_smaller_is_left_as_it_is(self):
        source, photo_id = self.mov("harbour.mov", 51)
        before = self.read_bytes(source)
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertEqual((False, convert.NOT_SMALLER), (said["success"], said["error"]))
        self.assertGreaterEqual(said["size_after"], said["size_before"])
        self.assertEqual(before, self.read_bytes(source), "the original as it was")
        self.assertFalse(os.path.exists(clip_convert.target_of(source)), "no MP4")
        self.assertEqual([], self.leftovers())
        self.assertEqual(paths.key(source), paths.key(self.row_of(photo_id)[0]))

    def test_a_mode_that_is_not_the_clips_plan_is_refused_before_ffmpeg(self):
        old, old_id = self.clip("quay.wmv")
        mov, mov_id = self.mov("harbour.mov", 0)
        with mock.patch.object(clip_convert, "convert") as ffmpeg:
            for path, photo_id, mode, why in ((old, old_id, "shrink", convert.DOES_NOT_PLAY),
                                              (mov, mov_id, "convert", convert.PLAYS_ALREADY)):
                with self.subTest(mode):
                    before = self.read_bytes(path)
                    said = self.ended(self.ask(photo_id, mode, path))
                    self.assertEqual((False, why), (said["success"], said["error"]))
                    self.assertEqual(before, self.read_bytes(path))
        ffmpeg.assert_not_called()

    def test_a_clip_changed_by_another_program_while_it_converts_is_left_as_that_program_left_it(self):
        source, photo_id = self.clip("quay.wmv")

        def changed(step):
            if step == "temp verified":
                with open(source, "ab") as handle:
                    handle.write(b"another program's bytes")
        with mock.patch.object(convert, "_reached", side_effect=changed):
            said = self.ended(self.ask(photo_id, "convert", source))
        self.assertEqual((False, convert.CHANGED), (said["success"], said["error"]))
        self.assertTrue(self.read_bytes(source).endswith(b"another program's bytes"), "the changed original kept")
        self.assertFalse(os.path.exists(clip_convert.target_of(source)), "no MP4")
        self.assertEqual([], self.leftovers(), "no temp left")
        self.assertEqual(paths.key(source), paths.key(self.row_of(photo_id)[0]), "the row where it was")
        self.assertEqual(["conflict"], self.converted_states(photo_id), "History says it changed")

    def test_a_stale_stamp_is_refused_at_once(self):
        source, photo_id = self.clip("quay.wmv")
        reply = self.ask(photo_id, "convert", source, size=1)
        self.assertEqual(409, reply.status_code)
        self.assertTrue(reply.get_json()["changed_on_disk"])
        self.assertEqual([], [row for row in self.changes() if row[1] == file_journal.CONVERT], "nothing planned")

    def test_a_second_click_is_refused_while_the_first_converts_and_the_strip_lists_it(self):
        source, photo_id = self.clip("quay.wmv")
        held, go, entered = self.held_up()
        with held:
            first = self.ask(photo_id, "convert", source)
            self.assertTrue(entered.wait(60))
            second = self.ask(photo_id, "convert", source)
            strip = self.client.get("/api/activity/batches").get_json()["batches"]
            go.set()
            said = self.ended(first)
        self.assertEqual(409, second.status_code)
        self.assertIn(convert.ASKED_ALREADY, second.get_json()["error"])
        self.assertEqual([(first.get_json()["ticket"], file_journal.CONVERT, "running")],
                         [(row["ticket"], row["operation"], row["state"]) for row in strip])
        self.assertTrue(said["success"], said)
        self.assert_converted(source, photo_id, TITLE)

    def test_cancel_on_its_ticket_stops_ffmpeg_and_leaves_the_original(self):
        source, photo_id = self.clip("quay.wmv")
        before = self.read_bytes(source)
        held, _go, entered = self.held_up()
        with held:
            first = self.ask(photo_id, "convert", source)
            self.assertTrue(entered.wait(60))
            cancel = self.client.post("/api/activity/batches/cancel", json={
                "library": first.get_json()["library"], "ticket": first.get_json()["ticket"]})
            self.assertEqual(200, cancel.status_code, cancel.get_json())
            said = self.ended(first)
        self.assertEqual((False, clip_convert.STOPPED), (said["success"], said["error"]))
        self.assertEqual(before, self.read_bytes(source))
        self.assertEqual([], self.leftovers())
        self.assertFalse(os.path.exists(clip_convert.target_of(source)))

    def test_a_conversion_under_way_keeps_its_ticket_however_many_are_asked_after_it(self):
        # The server keeps the last MOST_KEPT conversions that ended; one still converting is never dropped for them.
        first, first_id = self.clip("quay.wmv")
        second, second_id = self.clip("slipway.wmv")
        go, entered = threading.Event(), threading.Event()

        def converting(library, exiftool_path, tools, source, **kwargs):
            if paths.same(source, first) and not go.is_set():
                entered.set()
                go.wait(60)
            result = Result(attempted=1)
            result.refuse("stood in for")
            return result
        with mock.patch.object(convert, "MOST_KEPT", 1), mock.patch.object(convert, "convert_clip", side_effect=converting):
            under_way = self.ask(first_id, "convert", first)
            self.assertTrue(entered.wait(60))
            after_it = self.ask(second_id, "convert", second)
            go.set()
            said = [self.ended(under_way), self.ended(after_it)]
            later = self.ended(self.ask(first_id, "convert", first))
        self.assertEqual([(False, "stood in for")] * 3, [(s["success"], s["error"]) for s in said + [later]])
        asked = [reply.get_json()["ticket"] for reply in (under_way, after_it)]
        self.assertEqual([404, 200], [self.client.get("/library/api/clip/converted?ticket=%d" % ticket).status_code
                                      for ticket in asked], "the oldest that ended dropped once a later one is asked")

    def test_a_bulk_edit_asked_meanwhile_waits_behind_it(self):
        # One batch at a time in a process (batches._line): decided, the strip shows its place.
        source, photo_id = self.clip("quay.wmv")
        held, go, entered = self.held_up()
        bulk_began = threading.Event()

        def bulk():
            with batches.claim(self.vl.library, "bulk tags", 30):
                bulk_began.set()
        with held:
            first = self.ask(photo_id, "convert", source)
            self.assertTrue(entered.wait(60))
            waiter = threading.Thread(target=bulk)
            waiter.start()
            while len(batches.listing(self.vl.library)) < 2:
                waiter.join(0.05)
            line = [(row["operation"], row["state"], row["position"]) for row in batches.listing(self.vl.library)]
            self.assertFalse(bulk_began.is_set(), "the bulk edit began while the conversion ran")
            go.set()
            self.ended(first)
            waiter.join(60)
        self.assertEqual([(file_journal.CONVERT, "running", 0), ("bulk tags", "queued", 1)], line)
        self.assertTrue(bulk_began.is_set())


if __name__ == "__main__":
    unittest.main()

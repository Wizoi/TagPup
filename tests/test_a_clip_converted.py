"""A clip of an old format converted to H.264 and AAC in an MP4 (F19; tagpup.services.convert, DECISIONS 2026-11-09, 'A
CLIP IS CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'): its row followed to the MP4, what it carried written into the
copy, the original deleted for good last (never to a Recycle Bin: owner 2026-11-12), one change of History that is
never undone.

What the owner lives through: the one-time convert of the old formats (.wmv, .mpg, .avi; and an .mp4 whose picture is
MPEG-4 Part 2, converted under its own name), each clip one change; the run cut short at any step and settled at the next
start; a clip open in a player, a full disk, ffmpeg failing, an original that cannot be deleted. The panel's
Convert and Make smaller (MP4) by id: answered at once with a ticket, a second click refused, Cancel on the ticket, a bulk
edit waiting behind, a shrink no smaller left as it is, a button that is not the clip's plan refused (ThePanelButtons).

Real one-second clips made by the machine's ffmpeg in each old format, recorded by the indexer's own read
(test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip), in a home of its own; a conversion that reached for a Recycle Bin
fails the test (AnOldClipInTheLibrary). Skipped, saying so, without ffmpeg or
ExifTool. Fictional names only.
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_clip_tagged_through_the_journal import EXIFTOOL, TOOLS, ALibraryHoldingAClip  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import fields, paths, processes  # noqa: E402
from tagpup.files import clip_convert, field_values, metadata, recycle_bin, video_frames  # noqa: E402
from tagpup.services import batches, convert, file_changes, tagging  # noqa: E402
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


def make_clip(path, title=None, created=None):
    """A one-second clip at `path` in the old format its extension names (FORMATS), with sound, titled `title`; `created`,
    its container's creation time (a .wmv's ASF one is when its file was made: never its Date Taken)."""
    video, audio, container = FORMATS[os.path.splitext(path)[1]]
    titled = ["-metadata", "title=" + title] if title else []
    titled += ["-metadata", "creation_time=" + created] if created else []
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "1", "-c:v", video,
                          "-c:a", audio] + titled + ["-f", container, path], capture_output=True)
    assert made.returncode == 0, made.stderr
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

    def clip(self, name, title=TITLE):
        """A clip `name` in the folder, made and recorded as the indexer reads it: (its path, its id)."""
        path = make_clip(os.path.join(self.folder, name), title)
        return path, self.index(path)[0]

    def convert(self, path, **kwargs):
        return convert.convert_clip(self.vl.library, EXIFTOOL, TOOLS, path, **kwargs)

    def held(self, path, *wanted):
        with field_values.session(EXIFTOOL) as et:
            return field_values.read_one(et, path, list(wanted))

    def row_of(self, photo_id):
        (path, tags, raw), = self.vl.rows("SELECT path, tags, raw_metadata FROM photos WHERE id = ?", photo_id)
        return path, json.loads(tags or "[]"), json.loads(raw or "{}")

    def changes(self):
        return self.vl.rows("SELECT id, operation, status FROM changes ORDER BY id")

    def leftovers(self):
        return sorted(name for name in os.listdir(self.folder) if name.endswith(clip_convert.SUFFIX))

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

    def settled(self):
        """The settle of the next start, a while later: a temp the killed run left is older than the scratch rule's
        minutes by then (SCRATCH_STALE, 0 here: a temp's own modified time set back would make it older than its
        original, which the rule reads as the original changed since)."""
        with mock.patch.object(file_changes, "SCRATCH_STALE", 0):
            file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertEqual([], [row for row in self.changes() if row[2] == "planned"], "every change finished: %s" % (
            self.vl.rows("SELECT change_id, state, note FROM change_files"),))

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
                source, photo_id = self.clip("slipway%d.wmv" % n)
                self.killed_at(step, source)
                self.settled()
                self.assertEqual([("done",)], self.vl.rows(
                    "SELECT state FROM change_files WHERE photo_id = ? AND change_id IN "
                    "(SELECT id FROM changes WHERE operation = ?)", photo_id, file_journal.CONVERT),
                    "the conversion done, never a conflict")
                self.assertFalse(os.path.exists(source), "the original deleted")
                self.assert_converted(source, photo_id, TITLE)

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

    def test_a_copy_its_disk_will_not_take_is_removed_the_original_kept_and_nothing_recorded(self):
        with mock.patch("os.fsync", side_effect=OSError(5, "Access is denied")):
            result = self.convert(self.source)
        self.assertEqual((0, clip_convert.NOT_FLUSHED % "Access is denied"), (result.changed, result.message()))
        self.assert_as_it_was()
        self.assertEqual([], [after for (after,) in self.vl.rows("SELECT fields_after FROM change_files")
                              if "made" in json.loads(after or "{}")], "no copy recorded as made")

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
        self.assertEqual([(file_journal.CONVERT, "failed")],
                         [(op, status) for _id, op, status in self.changes() if op == file_journal.CONVERT])

    def test_an_original_that_cannot_be_deleted_keeps_it_and_its_copy_and_the_next_settle_deletes_it(self):
        with mock.patch.object(convert, "_removed_for_good", return_value="it is open in another program"):
            result = self.convert(self.source)
        self.assertEqual(1, result.changed, "the copy is in place")
        self.assertFalse(result.ok)
        self.assertIn("its original could not be deleted", result.details["original_kept"])
        self.assertIn("it is open in another program", result.details["original_kept"])
        self.assertEqual(self.before, self.read_bytes(self.source), "the original kept")
        target = clip_convert.target_of(self.source)
        self.assertEqual(paths.key(target), paths.key(self.row_of(self.photo_id)[0]), "the row at the MP4")
        self.assertEqual(("h264", "aac"), tuple(getattr(video_frames.streams(TOOLS, target), k) for k in ("video", "audio")),
                         "the verified copy kept")
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

    def test_a_second_library_holding_the_folder_converting_the_clip_meanwhile_is_refused_and_leaves_the_temp(self):
        # Two libraries hold the folder; while the first converts the clip, the second is asked to convert it too.
        other = ViewLibrary(self, "other", home=self.home)
        for record in metadata.MetadataExtractor(EXIFTOOL).batch_read([self.source]):
            photos.record_indexed(other.conn, record["path"], record)
            photos.ensure_row(other.conn, record["path"])
        other.conn.commit()
        real, seconds = clip_convert.convert, []

        def converting(tools, source, temp, *args, **kwargs):
            seconds.append(convert.convert_clip(other.library, EXIFTOOL, TOOLS, self.source))
            self.assertTrue(os.path.exists(temp), "the first's temp is where it was")
            return real(tools, source, temp, *args, **kwargs)

        with mock.patch.object(clip_convert, "convert", side_effect=converting):
            result = self.convert(self.source)
        self.assertEqual(1, result.changed, result.message())
        (second,) = seconds
        self.assertEqual((0, convert.TEMP_TAKEN), (second.changed, second.message()))
        self.assert_converted(self.source, self.photo_id, TITLE)


def make_mov(path, crf, created=CREATED):
    """A one-second H.264 and AAC clip that plays at `path`, a .mov (or an .mp4, by its extension), at x264's quality
    `crf`: 0 (lossless, far larger than the conversion's MP4) or 51 (smaller than it); dated as a camera dates it, by its
    container's creation time `created` (CREATED)."""
    container = "mp4" if path.endswith(".mp4") else "mov"
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "1", "-c:v", "libx264",
                          "-preset", "ultrafast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac",
                          "-metadata", "creation_time=" + created, "-f", container, path], capture_output=True)
    assert made.returncode == 0, made.stderr
    return path


class ThePanelButtons(AnOldClipInTheLibrary):
    """The clip panel's Convert and Make smaller (MP4), by id (POST /api/clip/convert): a batch of one on a thread of the
    server, the request answered at once with its ticket, how it ended read by the ticket (/api/clip/converted)."""

    def ask(self, photo_id, mode, path, **changed):
        stamp = photo_actions.file_stamp(path)
        body = {"id": photo_id, "mode": mode, "stamp": {"mtime": stamp[0], "size": stamp[1] + changed.get("size", 0)}}
        return self.post("clip/convert", body)

    def ended(self, reply):
        self.assertEqual(200, reply.status_code, reply.get_json())
        ticket = reply.get_json()["ticket"]
        convert.outcome(self.vl.library, ticket, wait=120)
        said = self.client.get("/library/api/clip/converted?ticket=%d" % ticket).get_json()
        self.assertTrue(said["done"], said)
        return said

    def mov(self, name, crf):
        path = make_mov(os.path.join(self.folder, name), crf)
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

    def test_a_dated_mov_made_smaller_keeps_its_date_taken_in_the_row_and_in_the_mp4(self):
        source, photo_id = self.mov("harbour.mov", 0)
        taken = self.taken(photo_id)
        self.assertTrue(taken, "the .mov is dated by its QuickTime:CreateDate")
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertFalse(os.path.exists(source), "the .mov deleted for good")
        self.assertEqual(taken, self.taken(photo_id), "the row's Date Taken is the .mov's")
        target = clip_convert.target_of(source)
        self.assertEqual(["2019:05:04 17:00:00"], fields.field_values(
            self.held(target, "QuickTime:CreateDate").get("QuickTime:CreateDate")),
            "the MP4 keeps the creation time other programs read ('Media created')")

    def test_an_mp4_dated_in_its_xmp_made_smaller_keeps_that_date(self):
        source, photo_id = self.mov("dock.mp4", 0)
        dated = file_changes.write_fields(self.vl.library, "save", EXIFTOOL, [source], ["XMP:DateTimeOriginal"],
                                          lambda _path, _held: file_changes.Plan(after={"XMP:DateTimeOriginal": XMP_TAKEN}))
        self.assertEqual(1, dated.changed, dated.message())
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "dated by its XMP, read before its QuickTime date")
        said = self.ended(self.ask(photo_id, "shrink", source))
        self.assertTrue(said["success"], said)
        self.assertEqual(XMP_TAKEN, self.taken(photo_id), "the row's Date Taken is the one its XMP held")
        self.assertEqual([XMP_TAKEN], fields.field_values(
            self.held(source, "XMP:DateTimeOriginal").get("XMP:DateTimeOriginal")), "the MP4 holds it")

    def test_a_wmv_holding_a_creation_time_is_converted_undated(self):
        # A .wmv's ASF creation time is when its file was made, not its Date Taken (DECISIONS 2026-11-09, item 6).
        source = make_clip(os.path.join(self.folder, "quay.wmv"), TITLE, created=CREATED)
        photo_id = self.index(source)[0]
        self.assertIsNone(self.taken(photo_id))
        said = self.ended(self.ask(photo_id, "convert", source))
        self.assertTrue(said["success"], said)
        self.assertIsNone(self.taken(photo_id), "an undated old clip stays undated")
        self.assertEqual([], [value for value in fields.field_values(self.held(
            clip_convert.target_of(source), "QuickTime:CreateDate").get("QuickTime:CreateDate"))
            if not str(value).startswith("0000")], "the MP4 holds no creation time")

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

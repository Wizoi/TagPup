"""A clip of an old format converted to H.264 and AAC in an MP4 (F19; tagpup.services.convert, DECISIONS 2026-11-09, 'A
CLIP IS CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'): its row followed to the MP4, what it carried written into the
copy, the original sent to the Recycle Bin last, one change of History that is never undone.

What the owner lives through: the one-time convert of the old formats (.wmv, .mpg, .avi; and an .mp4 whose picture is
MPEG-4 Part 2, converted under its own name), each clip one change; the run cut short at any step and settled at the next
start; a clip open in a player, a full disk, ffmpeg failing, a Recycle Bin that will not take the original.

Real one-second clips made by the machine's ffmpeg in each old format, recorded by the indexer's own read
(test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip), in a home of its own; the Recycle Bin is stood in for (the
original is moved into a folder of the home), nothing reaches the owner's Bin. Skipped, saying so, without ffmpeg or
ExifTool. Fictional names only.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_clip_tagged_through_the_journal import EXIFTOOL, TOOLS, ALibraryHoldingAClip  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import fields, paths, processes  # noqa: E402
from tagpup.files import clip_convert, field_values, metadata, recycle_bin, video_frames  # noqa: E402
from tagpup.services import convert, file_changes, tagging  # noqa: E402
from tagpup.store import file_journal, photos  # noqa: E402

#: The old formats as the library holds them: (extension, ffmpeg's picture and sound encoders, its container).
FORMATS = {
    ".wmv": ("wmv2", "wmav2", "asf"),
    ".mpg": ("mpeg1video", "mp2", "mpeg"),
    ".avi": ("mpeg4", "pcm_s16le", "avi"),
    ".mp4": ("mpeg4", "aac", "mp4"),       # MPEG-4 Part 2 in an MP4: converted under its own name
}
TITLE = "Gulls over the slipway"


def make_clip(path, title=None):
    """A one-second clip at `path` in the old format its extension names (FORMATS), with sound, titled `title`."""
    video, audio, container = FORMATS[os.path.splitext(path)[1]]
    titled = ["-metadata", "title=" + title] if title else []
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", "1", "-c:v", video,
                          "-c:a", audio] + titled + ["-f", container, path], capture_output=True)
    assert made.returncode == 0, made.stderr
    return path


class AnOldClipInTheLibrary(ALibraryHoldingAClip):
    """The library of ALibraryHoldingAClip; the Recycle Bin a folder of the home (`binned`)."""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.home.root, "Bin")
        os.makedirs(self.bin)
        self.binned = []
        for patcher in (mock.patch.object(recycle_bin, "no_bin_reason", return_value=None),
                        mock.patch.object(recycle_bin, "send_to_recycle_bin", side_effect=self.recycle)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def recycle(self, path):
        self.binned.append(os.path.basename(path))
        shutil.move(path, os.path.join(self.bin, os.path.basename(path)))
        return True

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
        self.assertIn(name, os.listdir(self.bin), "the original in the Recycle Bin")
        return target


class TheConversion(AnOldClipInTheLibrary):

    def test_each_old_format_is_converted_its_row_followed_its_title_and_name_carried_the_original_binned(self):
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
        self.assertIn("not undone, the original is in the Recycle Bin", json.dumps(reply.get_json()))


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
        self.assertEqual([], self.binned)

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

    def test_an_mp4_of_its_own_name_killed_between_its_delete_and_its_rename_is_put_in_place_by_the_settle(self):
        source, photo_id = self.clip("dock.mp4")
        self.killed_at("original deleted", source)
        self.assertEqual(["dock.mp4" + clip_convert.SUFFIX], self.leftovers(), "the copy in its temp, the original binned")
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

    def test_a_recycle_bin_that_will_not_take_the_original_keeps_it_and_the_next_settle_deletes_it(self):
        with mock.patch.object(recycle_bin, "send_to_recycle_bin", return_value=False):
            result = self.convert(self.source)
        self.assertEqual(1, result.changed, "the copy is in place")
        self.assertFalse(result.ok)
        self.assertIn("its original could not be deleted", result.details["original_kept"])
        self.assertEqual(self.before, self.read_bytes(self.source), "the original kept")
        target = clip_convert.target_of(self.source)
        self.assertEqual(paths.key(target), paths.key(self.row_of(self.photo_id)[0]), "the row at the MP4")
        file_changes.settle(self.vl.library, EXIFTOOL)
        self.assertFalse(os.path.exists(self.source), "the next settle deleted it")
        self.assert_converted(self.source, self.photo_id, TITLE)

    def test_an_mp4_of_its_own_name_whose_original_the_bin_will_not_take_is_not_converted(self):
        source, photo_id = self.clip("dock.mp4")
        before = self.read_bytes(source)
        with mock.patch.object(recycle_bin, "send_to_recycle_bin", return_value=False):
            result = self.convert(source)
        self.assertEqual(0, result.changed, result.message())
        self.assertEqual(before, self.read_bytes(source), "the original kept: its copy would take its name")
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


if __name__ == "__main__":
    unittest.main()

"""A clip of an old format converted to H.264 and AAC in an MP4 (F18; tagpup.services.convert, DECISIONS 2026-11-09, 'A
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

from tagpup.core import fields, paths, processes  # noqa: E402
from tagpup.files import clip_convert, field_values, recycle_bin, video_frames  # noqa: E402
from tagpup.services import convert, file_changes, tagging  # noqa: E402
from tagpup.store import file_journal  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()

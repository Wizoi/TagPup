"""A clip's tags and caption saved through the journal (DECISIONS 2026-11-05, M7): field_values.write patches a clip's
XMP in place (in_place.patch_clip) and never has ExifTool write it, and only while field_values.CLIP_PATCH_IN_PLACE is on
(off by default, until the owner's trial on 20 copies).

What the owner lives through: a clip's card opens the photo panel and its tags are saved, or a selection of a day holding
photos and clips is tagged. With writing clips off, every write of a clip is refused with the sentence; on, a keyword or
caption save is patched into its XMP, and a turn, a Date Taken and a rename stay refused. A clip the patch cannot place
(a tail, two XMP boxes) is refused, ExifTool never asked to rewrite it; one whose share fails part-way through its read
says it could not be read, never that it cannot be patched; one whose keywords are held in QuickTime too keeps its last.

Real clips made by the machine's ffmpeg (tests/test_a_clip_patched_in_place.FfmpegClips) and the real ExifTool, whose
write command (field_values._command) fails the test if asked. No ExifTool writes a clip's QuickTime Subject (14 live
.mp4 clips hold one, counted 2026-11-05), so the read answering it is ExifTool's with that tag added. Skipped, saying so,
without ffmpeg or ExifTool. Fictional names only.
"""
import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_patched_in_place as patched  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import containers, field_values, images, segment_edits  # noqa: E402

EXIFTOOL = patched.EXIFTOOL
TOOLS = patched.TOOLS
keywords = patched.keywords


def writing_clips(on=True):
    return mock.patch.object(field_values, "CLIP_PATCH_IN_PLACE", on)


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipWrittenByTheOneWriter(patched.FfmpegClips):
    """field_values.write of a real clip, the journal's `saving` a stand-in that keeps the record."""

    def setUp(self):
        self.saved = []
        rewrite = mock.patch.object(field_values, "_command", side_effect=AssertionError("ExifTool asked to write a clip"))
        rewrite.start()
        self.addCleanup(rewrite.stop)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.et = stack.enter_context(field_values.session(EXIFTOOL))

    def saving(self, record, before):
        self.saved.append((record, before))

    def write(self, path, values, journaled=True):
        return field_values.write(self.et, path, values, saving=self.saving if journaled else None)

    def bytes_of(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def assert_refused(self, sentence, path, values, journaled=True):
        before = self.bytes_of(path)
        with self.assertRaises(Refused) as refused:
            self.write(path, values, journaled)
        self.assertEqual(sentence, str(refused.exception))
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)), "nothing saved, nothing written")

    def test_with_writing_clips_off_every_write_of_a_clip_is_refused_and_exiftool_never_writes_it(self):
        path = self.clip("clip.mov")
        for values in (keywords(["Trips/Coast"]), fields.caption_fields("Jetty at dusk"),
                       fields.date_taken_fields("2020:01:01 12:00:00"), {fields.ORIENTATION: ["6"]}):
            with self.subTest(sorted(values)[0]):
                self.assert_refused(images.CLIP_NOT_EDITED, path, values)

    def test_with_writing_clips_on_a_journaled_tag_save_is_patched_into_its_xmp_and_nothing_else_is_written(self):
        path = self.clip("first.mp4")
        with writing_clips():
            self.assert_refused(images.CLIP_NOT_EDITED, path, keywords(["Trips/Coast"]), journaled=False)
            for values in (fields.date_taken_fields("2020:01:01 12:00:00"), {fields.ORIENTATION: ["6"]},
                           {**keywords(["Trips/Coast"]), **fields.date_taken_fields("2020:01:01 12:00:00")}):
                with self.subTest(sorted(values)[0]):
                    self.assert_refused(images.CLIP_NOT_EDITED, path, values)
            values = {**keywords(["Trips/Coast"]), **fields.caption_fields("Jetty at dusk")}
            self.assertEqual(segment_edits.clip_stale_of(values), self.write(path, values))
        self.assertEqual(1, len(self.saved), "one patch, its record saved before its bytes")
        self.read_back(path, ("Trips/Coast",))

    def test_a_clip_the_patch_cannot_place_is_refused_and_exiftool_is_not_asked_to_rewrite_it(self):
        path = self.clip("last.mp4")
        with open(path, "ab") as handle:
            handle.write(patched.xmp_box(patched.OLD) + patched.xmp_box(patched.OLD))
        with writing_clips():
            self.assert_refused(images.CLIP_NOT_PATCHED, path, keywords(["Trips/Coast"]))

    def test_a_clip_whose_share_fails_part_way_through_its_read_could_not_be_read_and_is_not_refused_as_unpatchable(self):
        path = self.clip("first.mp4")
        before = self.bytes_of(path)
        gone = OSError(64, "The specified network name is no longer available")
        with writing_clips(), mock.patch.object(containers, "_read_video", side_effect=gone), \
                self.assertRaises(field_values.Unreadable) as raised:
            self.write(path, keywords(["Trips/Coast"]))
        self.assertIn("could not be read", str(raised.exception))
        self.assertNotIn(images.CLIP_NOT_PATCHED, str(raised.exception))
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)))

    def test_the_last_keyword_of_a_clip_holding_its_keywords_in_quicktime_too_is_not_taken_off(self):
        path = self.clip("first.mp4")
        answer = self.et.get_tags

        def with_quicktime(files, tags, **options):
            rows = answer(files, tags=tags, **options)
            for row in rows:
                if field_values.QUICKTIME_KEYWORDS in tags:
                    row[field_values.QUICKTIME_KEYWORDS] = "Trips/Coast"
            return rows

        with writing_clips(), mock.patch.object(self.et, "get_tags", side_effect=with_quicktime):
            self.write(path, keywords(["Trips/Coast"]))
            self.saved = []
            self.assert_refused(segment_edits.KEPT_IN_QUICKTIME, path, keywords([]))
            self.write(path, keywords(["Trips/Harbour"]))
        self.read_back(path, ("Trips/Harbour",))


if __name__ == "__main__":
    unittest.main()

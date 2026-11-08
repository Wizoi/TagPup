"""A clip's tags and caption saved through the journal (DECISIONS 2026-11-05, M7): field_values.write patches a clip's
XMP in place (in_place.patch_clip) and never has ExifTool write it, and only while field_values.CLIP_PATCH_IN_PLACE is on
(on since the owner's trial on 20 copies, DECISIONS 2026-11-07; the owner's off switch).

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
import json
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_patched_in_place as patched  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.core import fields, paths, processes  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import containers, field_values, images, in_place, metadata, segment_edits  # noqa: E402
from tagpup.services import file_only  # noqa: E402
from tagpup.store import photos  # noqa: E402

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
        with writing_clips(False):
            for values in (keywords(["Trips/Coast"]), fields.title_fields("Jetty at dusk"),
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
            values = {**keywords(["Trips/Coast"]), **fields.title_fields("Jetty at dusk")}
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
                if fields.CLIP_KEYWORDS in tags:
                    row[fields.CLIP_KEYWORDS] = "Trips/Coast"
            return rows

        with writing_clips(), mock.patch.object(self.et, "get_tags", side_effect=with_quicktime):
            self.write(path, keywords(["Trips/Coast"]))
            self.saved = []
            self.assert_refused(segment_edits.KEPT_IN_QUICKTIME, path, keywords([]))
            self.write(path, keywords(["Trips/Harbour"]))
        self.read_back(path, ("Trips/Harbour",))


def with_quicktime_subject(path, words):
    """Give the .mp4 at `path` (ffmpeg's: its moov the last box) a QuickTime Subject holding `words`, as the 14 live clips
    hold theirs: a Microsoft Xtra atom in the moov's udta, ExifTool's family-1 group 'Microsoft', one value each (counted
    read-only 2026-11-05). ExifTool does not write it, so the test lays it as Windows does (ExifTool's Microsoft.pm,
    ProcessXtra: entry size, name length, name, a 4-byte count, then each value's size, type 8 and its UTF-16LE text)."""
    values = b""
    for word in words:
        text = (word + "\0").encode("utf-16-le")
        values += struct.pack(">IH", 6 + len(text), 8) + text
    entry = struct.pack(">I", len(b"Subject")) + b"Subject" + struct.pack(">I", len(words)) + values
    entry = struct.pack(">I", 4 + len(entry)) + entry
    xtra = struct.pack(">I", 8 + len(entry)) + b"Xtra" + entry
    udta = struct.pack(">I", 8 + len(xtra)) + b"udta" + xtra
    with open(path, "rb") as handle:
        data = handle.read()
    at = 0
    while True:
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        if at + size == len(data):
            break
        at += size
    assert kind == b"moov", "ffmpeg's .mp4 ends with its moov"
    with open(path, "wb") as handle:
        handle.write(data[:at] + struct.pack(">I", size + len(udta)) + data[at + 4:] + udta)


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class ALibraryHoldingAClip(unittest.TestCase):
    """A library holding a photo and a real ffmpeg clip, each row recorded by the indexer's own read
    (metadata.MetadataExtractor.batch_read), saved through the pages (Flask's test client, a home of its own) with writing
    clips on. No test of its own: the scenarios of a clip in a library are its subclasses (here, test_a_clip_tag_undone,
    test_a_clip_patch_cut_short)."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        for patcher in (mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL), writing_clips()):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast", "Trips/Harbour", "Trips/Pier")
        self.folder = os.path.join(self.vl.pictures, "Harbour")
        os.makedirs(self.folder, exist_ok=True)
        self.photo_path = os.path.join(self.folder, "pier.jpg")
        make_jpeg(self.photo_path, size=(32, 32))
        self.clip_path = os.path.join(self.folder, "jetty.mp4")
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", self.clip_path], capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)

    def index(self, *photo_paths):
        """Record each of `photo_paths` as the indexer reads it now; their ids."""
        ids = []
        for record in metadata.MetadataExtractor(EXIFTOOL).batch_read(list(photo_paths)):
            photos.record_indexed(self.vl.conn, record["path"], record)
            ids.append(photos.ensure_row(self.vl.conn, record["path"]))
        self.vl.conn.commit()
        return ids

    def tags_of(self, photo_id):
        return json.loads(self.vl.rows("SELECT tags FROM photos WHERE id = ?", photo_id)[0][0] or "[]")

    def read_now(self, path):
        """The tags the index reads of `path` now, and its XMP:Subject."""
        record = metadata.MetadataExtractor(EXIFTOOL).batch_read([path])[0]
        return record["tags"], fields.field_values(record["raw_metadata"].get("XMP:Subject"))

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def change_states(self, photo_id):
        return [state for state, in self.vl.rows("SELECT state FROM change_files WHERE photo_id = ?", photo_id)]

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()


class AClipInTheLibrary(ALibraryHoldingAClip):

    def test_a_tag_added_to_a_clip_whose_tags_are_in_its_quicktime_subject_keeps_them(self):
        with_quicktime_subject(self.clip_path, ["Trips/Coast", "Trips/Pier"])
        clip, = self.index(self.clip_path)
        self.assertEqual(["Trips/Coast", "Trips/Pier"], self.tags_of(clip), "the index reads its QuickTime Subject")
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "add_tags": ["Trips/Harbour"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        both = ["Trips/Coast", "Trips/Pier", "Trips/Harbour"]
        self.assertEqual((both, both), self.read_now(self.clip_path), "its XMP holds the old tags and the new")
        self.assertEqual(both, self.tags_of(clip), "and so does its row")

    def test_a_tag_taken_off_a_clip_that_holds_it_in_its_quicktime_subject_stays_off(self):
        with_quicktime_subject(self.clip_path, ["Trips/Coast", "Trips/Pier"])
        clip, = self.index(self.clip_path)
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "remove_tags": ["Trips/Coast"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((["Trips/Pier"], ["Trips/Pier"]), self.read_now(self.clip_path),
                         "the index reads its XMP, not the QuickTime copy TagPup does not write")
        self.assertEqual(["Trips/Pier"], self.tags_of(clip))
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "remove_tags": ["Trips/Pier"]})
        self.assertIn(segment_edits.KEPT_IN_QUICKTIME, reply.get_json()["error"])
        self.assertEqual((["Trips/Pier"], ["Trips/Pier"]), self.read_now(self.clip_path), "the last is kept")

    def test_a_tag_save_from_the_photo_panel_writes_the_clips_xmp_and_journals_it_done(self):
        clip, = self.index(self.clip_path)
        reply = self.post("photo/save-metadata", {"path": self.clip_path, "tags": ["Trips/Coast"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((["Trips/Coast"], ["Trips/Coast"]), self.read_now(self.clip_path))
        self.assertEqual(["done"], self.change_states(clip), "one journaled file, done")
        self.assertEqual(["Trips/Coast"], self.tags_of(clip))

    def test_a_clip_saved_is_stamped_with_its_new_size_and_the_same_save_again_writes_nothing(self):
        clip, = self.index(self.clip_path)
        size = os.path.getsize(self.clip_path)
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "add_tags": ["Trips/Coast"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertNotEqual(size, os.path.getsize(self.clip_path), "the patch appended an XMP box")
        self.assertEqual([(os.path.getsize(self.clip_path),)], self.vl.rows("SELECT size FROM photos WHERE id = ?", clip),
                         "the row describes the file as written, so the next scan does not read it again")
        scanned = metadata.MetadataExtractor(EXIFTOOL).batch_read([self.clip_path])[0]["raw_metadata"]
        recorded = json.loads(self.vl.rows("SELECT raw_metadata FROM photos WHERE id = ?", clip)[0][0])
        self.assertEqual(scanned, recorded, "and says what a scan reads of it")
        written = self.read_bytes(self.clip_path)
        reply = self.post("photo/save-metadata", {"path": self.clip_path, "tags": ["Trips/Coast"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(["done"], self.change_states(clip), "the same save again journals nothing")
        self.assertEqual(written, self.read_bytes(self.clip_path), "and writes nothing")

    def test_a_selection_of_a_photo_and_a_clip_tagged_writes_both(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        reply = self.post("library/bulk-edit", {"op": "tags", "selection": {"ids": [photo, clip]},
                                                "params": {"add": ["Trips/Coast"]}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        done = reply.get_json()
        self.assertEqual((2, 0), (done["changed"], done["error_count"]), done["errors"])
        self.assertEqual(["Trips/Coast"], self.read_now(self.clip_path)[1])
        self.assertEqual(["Trips/Coast"], self.read_now(self.photo_path)[1])

    def test_add_to_all_selected_with_the_clip_open_in_a_player_names_it_and_writes_the_photo_after_it(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        with open(self.clip_path, "rb"):
            before = self.read_bytes(self.clip_path)
            reply = self.post("photos/bulk-tags", {"paths": [self.clip_path, self.photo_path], "add_tags": ["Trips/Coast"]})
        body = reply.get_json()
        self.assertIn(in_place.OPEN_ELSEWHERE, body["error"])
        self.assertEqual([paths.key(self.photo_path)], [paths.key(path) for path in body["written"]],
                         "the clip's refusal does not stop the write: the photo after it is written")
        self.assertEqual(["Trips/Coast"], self.read_now(self.photo_path)[1])
        self.assertEqual(before, self.read_bytes(self.clip_path), "the clip is as it was")
        self.assertEqual([], self.tags_of(clip))

    def test_a_write_of_a_folder_the_library_does_not_hold_refuses_a_clip_with_writing_clips_on(self):
        before = self.read_bytes(self.clip_path)

        def plan_one(path, held):
            raise AssertionError("a clip is not planned")

        result = file_only.write_fields(EXIFTOOL, [self.clip_path], sorted(fields.CLIP_FIELDS), plan_one)
        self.assertEqual([images.CLIP_NOT_EDITED], [why for _path, why in result.errors],
                         "no journal to settle a patch a crash cut short")
        self.assertEqual(before, self.read_bytes(self.clip_path))


if __name__ == "__main__":
    unittest.main()

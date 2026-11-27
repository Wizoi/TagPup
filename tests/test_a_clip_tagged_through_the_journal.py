"""A clip's tags and caption saved through the journal (DECISIONS 2026-11-05, M7): field_values.write patches a clip's
XMP in place (in_place.patch_clip) and never has ExifTool write it (on since the owner's trial on 20 copies, DECISIONS
2026-11-07).

What the owner lives through: a clip's card opens the photo panel and its tags are saved, or a selection of a day holding
photos and clips is tagged. A keyword or title save is patched into its XMP (a Date Taken too, into its XMP dates:
tests/test_a_clip_dated_through_the_journal.py); a turn, and a photo's EXIF date fields, stay refused. A clip the patch cannot place
(a tail, two XMP boxes) is refused, ExifTool never asked to rewrite it; one whose share fails part-way through its read
says it could not be read, never that it cannot be patched. A clip whose QuickTime Subject is a Microsoft Xtra value that
is not text (14 live .mp4 clips, counted 2026-11-07) is indexed with no tags, and a tag save writes only the tags saved.

Real clips made by the machine's ffmpeg (tests/test_a_clip_patched_in_place.FfmpegClips) and the real ExifTool, whose
write command (field_values._command) fails the test if asked. No ExifTool writes a clip's QuickTime Subject, so the
test lays one as Windows does (with_a_quicktime_subject_that_is_not_text). Skipped, saying so, without ffmpeg or
ExifTool. Fictional names only.
"""
import contextlib
import json
import os
import re
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import test_a_clip_patched_in_place as patched  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tests import page_writes  # noqa: E402
from tagpup.core import fields, paths  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import containers, field_values, images, in_place, segment_edits  # noqa: E402
from tagpup.services import file_only  # noqa: E402
from tagpup.store import photos  # noqa: E402

EXIFTOOL = patched.EXIFTOOL
TOOLS = patched.TOOLS
keywords = patched.keywords


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
        self.et = stack.enter_context(field_values.opened(EXIFTOOL))

    def saving(self, record, before):
        self.saved.append((record, before))

    def write(self, path, values, journaled=True):
        return self.et.write(path, values, saving=self.saving if journaled else None)

    def bytes_of(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def assert_refused(self, sentence, path, values, journaled=True):
        before = self.bytes_of(path)
        with self.assertRaises(Refused) as refused:
            self.write(path, values, journaled)
        self.assertEqual(sentence, str(refused.exception))
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)), "nothing saved, nothing written")

    def test_a_journaled_tag_save_is_patched_into_its_xmp_and_nothing_else_is_written(self):
        path = self.clip("first.mp4")
        self.assert_refused(images.CLIP_NOT_EDITED, path, keywords(["Trips/Coast"]), journaled=False)
        # A photo's Date Taken fields (EXIF's among them) and a turn are refused; a clip's are its XMP dates alone.
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
        self.assert_refused(images.CLIP_NOT_PATCHED, path, keywords(["Trips/Coast"]))

    def test_a_clip_whose_share_fails_part_way_through_its_read_could_not_be_read_and_is_not_refused_as_unpatchable(self):
        path = self.clip("first.mp4")
        before = self.bytes_of(path)
        gone = OSError(64, "The specified network name is no longer available")
        with mock.patch.object(containers, "_read_video", side_effect=gone), \
                self.assertRaises(field_values.Unreadable) as raised:
            self.write(path, keywords(["Trips/Coast"]))
        self.assertIn("could not be read", str(raised.exception))
        self.assertNotIn(images.CLIP_NOT_PATCHED, str(raised.exception))
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)))


#: A QuickTime Subject that is not text: 32 bytes, as each of the 14 live clips holds one (a Microsoft Xtra entry of a type
#: that is not text, ExifTool's family-1 group 'Microsoft'; counted read-only 2026-11-07). The live ones are answered as
#: hex, this one (a VT_VARIANT vector of bytes) as numbers: either is text ExifTool made of bytes, never a keyword.
NOT_TEXT = bytes(range(0xa0, 0xc0))


def with_a_quicktime_subject_that_is_not_text(path):
    """Give the .mp4 at `path` (ffmpeg's: its moov the last box) a QuickTime Subject holding NOT_TEXT: a Microsoft Xtra
    atom in the moov's udta, laid as Windows does (ExifTool's Microsoft.pm, ProcessXtra and ReadXtraValue: entry size,
    name length, name, a 4-byte count, then the value's size, its type -- 65, a VT_VARIANT -- and the variant: a vector
    of unsigned bytes). ExifTool does not write it. What ExifTool answers of it, as the scan reads it."""
    variant = struct.pack("<HHI", 0x1011, 0, len(NOT_TEXT)) + NOT_TEXT
    values = struct.pack(">IH", 6 + len(variant), 65) + variant
    entry = struct.pack(">I", len(b"Subject")) + b"Subject" + struct.pack(">I", 1) + values
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
    held = photo_rows.read_whole(EXIFTOOL, [path])[0]["raw_metadata"].get("QuickTime:Subject")
    assert held, "ExifTool answers the clip's QuickTime Subject"
    return held


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class ALibraryHoldingAClip(unittest.TestCase):
    """A library holding a photo and a real ffmpeg clip (made once and kept, tests/corpus.py), each row recorded by the indexer's own read
    (field_values.Fields.whole), saved through the pages (Flask's test client, a home of its own). No test of its own: the scenarios of a clip in a library are its subclasses (here, test_a_clip_tag_undone,
    test_a_clip_patch_cut_short, test_a_clip_dated_through_the_journal)."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        patcher = mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast", "Trips/Harbour", "Trips/Pier")
        self.folder = os.path.join(self.vl.pictures, "Harbour")
        os.makedirs(self.folder, exist_ok=True)
        self.photo_path = os.path.join(self.folder, "pier.jpg")
        make_jpeg(self.photo_path, size=(32, 32))
        self.clip_path = os.path.join(self.folder, "jetty.mp4")
        corpus.clip(self.clip_path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                     "-t", "1", "-pix_fmt", "yuv420p"], TOOLS.ffmpeg)

    def index(self, *photo_paths):
        """Record each of `photo_paths` as the indexer reads it now; their ids."""
        ids = []
        for record in photo_rows.read_whole(EXIFTOOL, list(photo_paths)):
            photo_rows.added(self.vl.conn, record["path"])   # the index adds the folder it is given first
            photos.record_indexed(self.vl.conn, record["path"], record)
            ids.append(photos.ensure_row(self.vl.conn, record["path"]))
        self.vl.conn.commit()
        return ids

    def tags_of(self, photo_id):
        return json.loads(self.vl.rows("SELECT tags FROM photos WHERE id = ?", photo_id)[0][0] or "[]")

    def read_now(self, path):
        """The tags the index reads of `path` now, and its XMP:Subject."""
        record = photo_rows.read_whole(EXIFTOOL, [path])[0]
        return record["tags"], fields.field_values(record["raw_metadata"].get("XMP:Subject"))

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def change_states(self, photo_id):
        return [state for state, in self.vl.rows("SELECT state FROM change_files WHERE photo_id = ?", photo_id)]

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()


class AClipInTheLibrary(ALibraryHoldingAClip):

    def test_a_clip_whose_quicktime_subject_is_not_text_is_indexed_with_no_tags_and_saved_with_only_the_tags_saved(self):
        with_a_quicktime_subject_that_is_not_text(self.clip_path)
        clip, = self.index(self.clip_path)
        self.assertEqual([], self.tags_of(clip), "the index reads no keyword of it")
        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": ["Trips/Pier"]}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual((["Trips/Pier"], ["Trips/Pier"]), self.read_now(self.clip_path), "its XMP holds the tag saved alone")
        self.assertEqual(["Trips/Pier"], self.tags_of(clip))
        reply = self.post("photos/write", {"op": "tags", "changes": {self.clip_path: {"remove": ["Trips/Pier"]}}})
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual(([], []), self.read_now(self.clip_path), "its last tag comes off, and nothing is read back")
        self.assertEqual([], self.tags_of(clip))
        self.assertEqual(["done", "done"], self.change_states(clip))

    def test_a_tag_save_of_a_clip_whose_description_shows_as_its_title_writes_its_tags_and_title_alone(self):
        # A photo's save clears the Description it showed as its title (tagging._caption_moved); a clip's caption is not
        # written (fields.CLIP_WRITES), so its save asks none and is not refused at its write for it (#2663).
        described = patched.packet(()).replace(
            b"<dc:subject><rdf:Bag></rdf:Bag></dc:subject>",
            b"<dc:description><rdf:Alt><rdf:li xml:lang='x-default'>Jetty at dusk</rdf:li></rdf:Alt></dc:description>")
        with open(self.clip_path, "ab") as handle:
            handle.write(patched.xmp_box(described))
        clip, = self.index(self.clip_path)
        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": ["Trips/Pier"], "title": "Jetty at dusk"}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual((["Trips/Pier"], ["Trips/Pier"]), self.read_now(self.clip_path))
        record = photo_rows.read_whole(EXIFTOOL, [self.clip_path])[0]["raw_metadata"]
        self.assertEqual(("Jetty at dusk", "Jetty at dusk"), (record.get("XMP:Title"), record.get("XMP:Description")),
                         "the title saved, the Description left as it was")
        self.assertEqual(["done"], self.change_states(clip))

    def records(self, ids):
        """{filename: writes} of the records the page is given: a folder's scan (Refresh's: the server keeps the last
        one), and the panel's of each of `ids`."""
        scan = self.client.get("/library/api/folder/scan", query_string={"path": self.folder, "force": "true"})
        self.assertEqual(200, scan.status_code, scan.data)
        listed = scan.get_json()
        listed = listed if isinstance(listed, list) else listed["photos"]
        found = {"scan " + record["filename"]: record["writes"] for record in listed}
        for photo_id in ids:
            reply = self.client.get("/library/api/library/photo", query_string={"id": photo_id})
            self.assertEqual(200, reply.status_code, reply.data)
            record = reply.get_json()["photo"]
            found["panel " + record["filename"]] = record["writes"]
        return found

    def test_the_page_is_told_what_a_save_may_change_of_a_clip_and_of_a_photo(self):
        # The panel offers a clip its tags, title, name and Date Taken, never a turn; a photo all five (field_values.writes).
        ids = self.index(self.photo_path, self.clip_path)
        photo, clip = ["tags", "title", "date", "rotate", "name"], ["tags", "title", "name", "date"]
        self.assertEqual({"scan pier.jpg": photo, "scan jetty.mp4": clip, "panel pier.jpg": photo, "panel jetty.mp4": clip},
                         self.records(ids))

    def test_a_tag_save_from_the_photo_panel_writes_the_clips_xmp_and_journals_it_done(self):
        clip, = self.index(self.clip_path)
        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": ["Trips/Coast"]}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertEqual((["Trips/Coast"], ["Trips/Coast"]), self.read_now(self.clip_path))
        self.assertEqual(["done"], self.change_states(clip), "one journaled file, done")
        self.assertEqual(["Trips/Coast"], self.tags_of(clip))

    def test_a_clip_saved_is_stamped_with_its_new_size_and_the_same_save_again_writes_nothing(self):
        clip, = self.index(self.clip_path)
        size = os.path.getsize(self.clip_path)
        reply = self.post("photos/write", {"op": "tags", "changes": {self.clip_path: {"add": ["Trips/Coast"]}}})
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        self.assertNotEqual(size, os.path.getsize(self.clip_path), "the patch appended an XMP box")
        self.assertEqual([(os.path.getsize(self.clip_path),)], self.vl.rows("SELECT size FROM photos WHERE id = ?", clip),
                         "the row describes the file as written, so the next scan does not read it again")
        scanned = photo_rows.read_whole(EXIFTOOL, [self.clip_path])[0]["raw_metadata"]
        recorded = json.loads(self.vl.rows("SELECT raw_metadata FROM photos WHERE id = ?", clip)[0][0])
        self.assertEqual(scanned, recorded, "and says what a scan reads of it")
        written = self.read_bytes(self.clip_path)
        reply = self.post("photos/write", page_writes.save(self.vl.library, **{"path": self.clip_path, "tags": ["Trips/Coast"]}))
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
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
            reply = self.post("photos/write", {"op": "tags", "changes": {photo: {"add": ["Trips/Coast"]}
                                                               for photo in [self.clip_path, self.photo_path]}})
        body = reply.get_json()
        self.assertIn(in_place.OPEN_ELSEWHERE, page_writes.why(body))
        self.assertEqual([paths.key(self.photo_path)], [paths.key(path) for path in body["written"]],
                         "the clip's refusal does not stop the write: the photo after it is written")
        self.assertEqual(["Trips/Coast"], self.read_now(self.photo_path)[1])
        self.assertEqual(before, self.read_bytes(self.clip_path), "the clip is as it was")
        self.assertEqual([], self.tags_of(clip))

    def test_a_write_of_a_folder_the_library_does_not_hold_refuses_a_clip(self):
        before = self.read_bytes(self.clip_path)

        def plan_one(path, held):
            raise AssertionError("a clip is not planned")

        result = file_only.write_fields(field_values.opened(EXIFTOOL), [self.clip_path], sorted(fields.CLIP_FIELDS), plan_one)
        self.assertEqual([images.CLIP_NOT_EDITED], [why for _path, why in result.errors],
                         "no journal to settle a patch a crash cut short")
        self.assertEqual(before, self.read_bytes(self.clip_path))


class ThePageSpellsWritesAsTheServerDoes(unittest.TestCase):
    """The panel holds a control unless the record's `writes` names its field: the names the page and the frontend
    fixtures spell are the server's (fields.PHOTO_WRITES, CLIP_WRITES), or a rename on one side holds a control for
    every photo with nothing failing."""

    def test_the_names_in_the_page_and_the_fixtures_are_the_servers(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "web", "tagpup", "index.html"), encoding="utf-8") as f:
            named = set(re.findall(r'data-writes="([^"]+)"', f.read()))
        self.assertEqual(set(fields.PHOTO_WRITES), named)
        with open(os.path.join(root, "tests", "frontend", "harness.mjs"), encoding="utf-8") as f:
            harness = f.read()
        for name, expected in (("PHOTO_WRITES", fields.PHOTO_WRITES), ("CLIP_WRITES", fields.CLIP_WRITES)):
            found = re.search(r"export const %s = \[([^\]]*)\]" % name, harness)
            self.assertIsNotNone(found, name)
            self.assertEqual(list(expected), re.findall(r'"([^"]+)"', found.group(1)), name)


if __name__ == "__main__":
    unittest.main()

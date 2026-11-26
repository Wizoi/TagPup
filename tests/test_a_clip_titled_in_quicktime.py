"""A clip's title is its XMP:Title, else its QuickTime Title; a save writes XMP:Title, and frees the QuickTime Title only
when it leaves the clip with no XMP:Title (DECISIONS 2026-11-07, item 3; DECISIONS 2026-11-08; #2594, #2652, #2657).

What the owner lives through: 2,017 photo_index clips carry a title only in their QuickTime Title (counted read-only
2026-11-07; one more holds it in XMP:Title too, the same text; none in kr-track or renton_parkrun).
A clip's card opens the photo panel showing that title, the owner adds a tag and saves: the save is not refused as
changed on disk, the title stays in the QuickTime Title alone (no XMP copy of it is made), and the row says what a scan
reads. A title typed over it goes into XMP:Title and is the title from then on, the QuickTime Title left as it was; a
title cleared clears XMP:Title and frees the QuickTime Title, and the clip has no title anywhere; History's Undo of
that clear gives the QuickTime Title back, the clip byte for byte as it was before the clear, its row as a scan reads it.
A .mov holding its title in an item list (as the live ones do; ffmpeg's own .mov keeps it directly in udta, which a clear
refuses as the rewrite's) is cleared and undone alike.

A real one-second clip made by the machine's ffmpeg with a title (its QuickTime Title, as the live clips hold theirs),
indexed by the indexer's own read, saved through the pages (tests/test_a_clip_tagged_through_the_
journal.ALibraryHoldingAClip: Flask's test client, a home of its own). Skipped, saying so, without ffmpeg or ExifTool.
Fictional names only.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import photo_rows  # noqa: E402
from test_a_clip_tagged_through_the_journal import EXIFTOOL, TOOLS, ALibraryHoldingAClip  # noqa: E402

from tests import page_writes  # noqa: E402
from tagpup.files import containers  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

QUICKTIME_TITLE = "Slipway at dusk"


class AClipTitledInQuickTime(ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        self.clip_path = os.path.join(self.folder, "slipway.mp4")
        corpus.clip(self.clip_path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                     "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "title=" + QUICKTIME_TITLE],
                    TOOLS.ffmpeg)
        self.assertEqual((QUICKTIME_TITLE, None), self.titles(), "the clip holds its title in QuickTime alone")
        self.clip, = self.index(self.clip_path)

    def titles(self):
        """(QuickTime:Title, XMP:Title) the clip's file holds now."""
        with ExifToolSession(executable=EXIFTOOL) as et:
            held = et.get_tags([self.clip_path], tags=["QuickTime:Title", "XMP:Title"])[0]
        return held.get("QuickTime:Title"), held.get("XMP:Title")

    def opened(self):
        reply = self.client.get("/library/api/library/photo", query_string={"id": self.clip})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["photo"]

    def save(self, record, title, tags):
        """The photo panel's save of `record` as the page sends it: the stamp and the base it read."""
        return self.post("photos/write", page_writes.save(self.vl.library, **{
            "path": record["path"], "title": title, "tags": tags,
            "stamp": {"mtime": record["mtime"], "size": record["size"]},
            "base": {"tags": record["tags"], "title": record["title"]}}))

    def assert_row_says_what_a_scan_reads(self):
        scanned = photo_rows.read_whole(EXIFTOOL, [self.clip_path])[0]
        row = self.vl.rows("SELECT raw_metadata, captions FROM photos WHERE id = ?", self.clip)[0]
        self.assertEqual(scanned["raw_metadata"], json.loads(row[0]), "the row holds what a scan reads of the file")
        self.assertEqual(scanned["captions"], json.loads(row[1] or "[]"), "and the captions a scan derives")

    def test_a_tag_saved_from_the_panel_is_not_refused_and_the_title_stays_in_quicktime_alone(self):
        record = self.opened()
        self.assertEqual(QUICKTIME_TITLE, record["title"], "the panel shows the QuickTime Title")
        reply = self.save(record, record["title"], record["tags"] + ["Trips/Coast"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((["Trips/Coast"], ["Trips/Coast"]), self.read_now(self.clip_path), "the tag is in its XMP")
        self.assertEqual((QUICKTIME_TITLE, None), self.titles(),
                         "the QuickTime Title is as it was and no XMP copy of it is made")
        self.assertEqual(["done"], self.change_states(self.clip))
        self.assertEqual(QUICKTIME_TITLE, self.opened()["title"])
        self.assert_row_says_what_a_scan_reads()

    def test_a_title_typed_over_it_is_written_to_xmp_only_and_a_cleared_one_is_gone_from_quicktime_too(self):
        record = self.opened()
        reply = self.save(record, "Slipway at first light", record["tags"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((QUICKTIME_TITLE, "Slipway at first light"), self.titles(),
                         "the new title is in XMP:Title, the QuickTime Title untouched")
        record = self.opened()
        self.assertEqual("Slipway at first light", record["title"], "XMP:Title is the clip's title over QuickTime's")
        self.assert_row_says_what_a_scan_reads()
        reply = self.save(record, "Slipway at first light", record["tags"] + ["Trips/Harbour"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((["Trips/Harbour"], ["Trips/Harbour"]), self.read_now(self.clip_path),
                         "a clip holding both titles saves from the panel too")
        record = self.opened()
        reply = self.save(record, "", record["tags"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((None, None), self.titles(), "a cleared title clears XMP:Title and the QuickTime Title")
        self.assertEqual("", self.opened()["title"], "and the panel shows no title")
        self.assert_row_says_what_a_scan_reads()

    def last_change(self):
        return self.vl.rows("SELECT MAX(change_id) FROM change_files WHERE photo_id = ?", self.clip)[0][0]

    def undone(self, change):
        """History's Undo of `change` as the dialog sends it: its rehearsal, then the undo itself."""
        rehearsal = self.post("history/%d/undo" % change, {})
        self.assertEqual(200, rehearsal.status_code, rehearsal.data)
        self.assertEqual([], rehearsal.get_json()["differences"], "the rehearsal says it can be put back")
        reply = self.post("history/%d/undo" % change, {"apply": True})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((1, []), (reply.get_json()["changed"], reply.get_json()["errors"]))

    def cleared_and_undone(self):
        """Clear the clip's title from the panel, then Undo it from History: each step read back."""
        record = self.opened()
        before = self.read_bytes(self.clip_path)
        reply = self.save(record, "", record["tags"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((None, None), self.titles(), "the clear frees the QuickTime Title")
        with open(self.clip_path, "rb") as handle:
            self.assertEqual((), containers.read(handle).titles, "no title item is left")
        self.assertEqual("", self.opened()["title"])
        self.assert_row_says_what_a_scan_reads()
        self.undone(self.last_change())
        self.assertEqual((QUICKTIME_TITLE, None), self.titles(), "the undo gives the QuickTime Title back alone")
        self.assertEqual(QUICKTIME_TITLE, self.opened()["title"])
        self.assert_row_says_what_a_scan_reads()
        return before

    def test_a_cleared_title_undone_from_history_leaves_the_clip_byte_for_byte_as_before_the_clear(self):
        record = self.opened()
        reply = self.save(record, record["title"], ["Trips/Coast"])
        self.assertEqual(200, reply.status_code, reply.data)
        before = self.cleared_and_undone()
        self.assertEqual(before, self.read_bytes(self.clip_path), "the clip is as it was before the clear")

    def test_a_cleared_title_of_a_clip_with_no_xmp_writes_no_xmp_and_is_undone_to_the_same_bytes(self):
        before = self.cleared_and_undone()
        self.assertEqual(before, self.read_bytes(self.clip_path), "a clear alone writes the 4 title bytes, no XMP box")

    def test_a_movs_title_in_its_item_list_is_cleared_and_undone(self):
        self.clip_path = os.path.join(self.folder, "slipway.mov")
        corpus.clip(self.clip_path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                     "-t", "1", "-pix_fmt", "yuv420p"], TOOLS.ffmpeg)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.clip_path], tags={"ItemList:Title": QUICKTIME_TITLE}, params=["-overwrite_original"])
        with open(self.clip_path, "rb") as handle:
            self.assertIsNotNone(containers.read(handle).title_item, "the title in moov/udta/meta/ilst, as live")
        self.clip, = self.index(self.clip_path)
        before = self.cleared_and_undone()
        after = self.read_bytes(self.clip_path)
        self.assertEqual(before, after[:len(before)])


if __name__ == "__main__":
    unittest.main()

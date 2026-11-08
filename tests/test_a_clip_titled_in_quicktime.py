"""A clip's title is its XMP:Title, else its QuickTime Title, and a save writes XMP:Title only: the QuickTime Title is
never rewritten (DECISIONS 2026-11-07, THE OWNER'S ANSWERS ON THE VIDEO BUILD ORDER, item 3; #2594).

What the owner lives through: 2,017 photo_index clips carry a title only in their QuickTime Title (counted read-only
2026-11-07; one more holds it in XMP:Title too, the same text; none in kr-track or renton_parkrun). With writing clips on,
a clip's card opens the photo panel showing that title, the owner adds a tag and saves: the save is not refused as
changed on disk, the title stays in the QuickTime Title alone (no XMP copy of it is made), and the row says what a scan
reads. A title typed over it goes into XMP:Title and is the title from then on, the QuickTime Title left as it was; a
title cleared clears XMP:Title, and the QuickTime Title is the title again, by the rule.

A real one-second clip made by the machine's ffmpeg with a title (its QuickTime Title, as the live clips hold theirs),
indexed by the indexer's own read, saved through the pages with writing clips on (tests/test_a_clip_tagged_through_the_
journal.ALibraryHoldingAClip: Flask's test client, a home of its own). Skipped, saying so, without ffmpeg or ExifTool.
Fictional names only.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_clip_tagged_through_the_journal import EXIFTOOL, TOOLS, ALibraryHoldingAClip  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.files import metadata  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

QUICKTIME_TITLE = "Slipway at dusk"


class AClipTitledInQuickTime(ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        self.clip_path = os.path.join(self.folder, "slipway.mp4")
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "title=" + QUICKTIME_TITLE, self.clip_path],
                             capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)
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
        return self.post("photo/save-metadata", {
            "path": record["path"], "title": title, "tags": tags,
            "stamp": {"mtime": record["mtime"], "size": record["size"]},
            "base": {"tags": record["tags"], "title": record["title"]}})

    def assert_row_says_what_a_scan_reads(self):
        scanned = metadata.MetadataExtractor(EXIFTOOL).batch_read([self.clip_path])[0]
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

    def test_a_title_typed_over_it_is_written_to_xmp_only_and_a_cleared_one_shows_the_quicktime_title_again(self):
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
        self.assertEqual((QUICKTIME_TITLE, None), self.titles(), "a cleared title clears XMP:Title alone")
        self.assertEqual(QUICKTIME_TITLE, self.opened()["title"], "and the QuickTime Title is the title again")
        self.assert_row_says_what_a_scan_reads()


if __name__ == "__main__":
    unittest.main()

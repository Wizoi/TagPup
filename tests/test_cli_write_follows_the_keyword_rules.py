"""`tagpup_cli write` writes keywords the way the app does, and tells the index.

It had its own ExifTool code: every hierarchical tag was also split into its parts, so
"Activity/Rowing" wrote loose "Activity" and "Rowing" keywords beside it; a person named
by a bare leaf was written bare; and the index was never told, so its rows described
what the photos used to hold. It now writes through tagpup.files.keywords.write_keywords,
people resolved first, and tagpup.store.photos.record_tags, like every other keyword write.
"""
import json
import os
import unittest
from unittest import mock

from tests import photo_rows
from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

from tagpup_cli import write_suggestions_file

from tagpup.files.exiftool_session import ExifToolSession
from tagpup.store import db


@requires_exiftool
class CliWriteFollowsTheKeywordRules(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self.lib = Library(self)
        self.photo = os.path.join(self.lib.photos, "boathouse.jpg")
        Image.new("RGB", (8, 8)).save(self.photo)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo], tags={"XMP:HierarchicalSubject": ["Places/Harbour"],
                                            "XMP:Subject": ["Places/Harbour"]},
                        params=["-overwrite_original"])
        for row in [(1, "People", "People", None, 1),
                    (2, "People/Rowan Thackeray", "Rowan Thackeray", 1, 1)]:
            self.lib.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face)"
                             " VALUES (?, ?, ?, ?, ?)", row)
        # The row as the indexer makes it, describing its file (docs/findings.md, #249).
        conn = db.connect(self.lib.db_path)
        try:
            photo_rows.add_read(conn, self.photo, {"XMP:HierarchicalSubject": ["Places/Harbour"],
                                                   "XMP:Subject": ["Places/Harbour"]})
            conn.commit()
        finally:
            conn.close()
        self.suggestions = os.path.join(self.lib.root, "suggestions.json")
        with open(self.suggestions, "w", encoding="utf-8") as handle:
            json.dump([{"path": self.photo, "suggested_tags": [
                {"tag": "Activity/Rowing", "score": 0.9},
                {"tag": "Rowan Thackeray", "score": 0.9}]}], handle)

    def write(self):
        with mock.patch("builtins.input", return_value="YES"):
            return write_suggestions_file(self.suggestions, self.lib.db_path, EXIFTOOL, live=True)

    def keywords(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([self.photo], tags=["XMP:Subject", "XMP:HierarchicalSubject"])[0]
        as_list = lambda v: v if isinstance(v, list) else ([v] if v else [])  # noqa: E731
        return set(as_list(found.get("XMP:Subject"))), set(as_list(found.get("XMP:HierarchicalSubject")))

    def test_whole_paths_only_and_people_filed(self):
        self.assertTrue(self.write())
        flat, hierarchical = self.keywords()
        self.assertEqual({"Places/Harbour", "Activity/Rowing", "People/Rowan Thackeray"}, hierarchical)
        for loose in ("Activity", "Rowing", "People", "Rowan Thackeray"):
            self.assertNotIn(loose, flat)

    def test_the_index_is_told(self):
        self.write()
        tags, mtime = self.lib.rows("SELECT tags, mtime FROM photos")[0]
        self.assertIn("Activity/Rowing", json.loads(tags))
        self.assertIn("People/Rowan Thackeray", json.loads(tags))
        self.assertEqual(os.stat(self.photo).st_mtime, mtime)


if __name__ == "__main__":
    unittest.main()

"""Saving one photo records its raw_metadata in the shape the indexer records a read in
(docs/findings.md, #251).

The save read the file back whole and recorded ExifTool's grouped names only
(`XMP:Subject`), where the indexer records each under its bare name too (`Subject`,
MetadataExtractor._structure): a saved row and an indexed row of one file differed, and
a saved row holding no bare name looks to the refresh like one never read. One owner of
the shape now. Real ExifTool, on a JPEG made here.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL  # noqa: E402

from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import refresh_rows, tagging  # noqa: E402


class ASavedPhoto(ReadFilesCase):
    def test_its_row_holds_what_the_indexer_would_record(self):
        a = self.make_read("a.jpg")
        result = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"],
                                    "2019-06-15T10:30:00", EXIFTOOL, "")
        self.assertTrue(result.ok, result.message())
        raw_json = self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (a,))[0][0]
        indexed = MetadataExtractor(exiftool_path=EXIFTOOL).batch_read([a])[0]["raw_metadata"]
        self.assertEqual(indexed, json.loads(raw_json))
        self.assertEqual("Quay at dusk", json.loads(raw_json)["Description"])
        self.assertFalse(refresh_rows.never_read(raw_json))


if __name__ == "__main__":
    unittest.main()

"""A file holding a keyword IPTC cut is not planned and written again by every later
keyword edit (docs/findings.md, #278).

IPTC:Keywords keeps 64 bytes of a keyword; XMP:Subject keeps it whole. The cut copy was
read as a tag of its own, so every later edit of the photo's keywords planned it into
every field -- the cut tag written into XMP too -- and a file already holding what the
edit asked for was written again. Real ExifTool, on JPEGs made here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_number_compares_as_read import ReadFilesCase  # noqa: E402
from test_file_journal import keywords_of  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402

LONG = "Places/" + "Harbour" * 13


class AFileHoldingACutKeyword(ReadFilesCase):
    def setUp(self):
        super().setUp()
        self.a = self.make_read("a.jpg")
        self.assertEqual(1, self.add([self.a], [LONG]).changed)

    def test_is_not_written_by_an_edit_it_already_holds(self):
        written = os.stat(self.a).st_mtime_ns
        again = self.add([self.a], ["Beach"])
        self.assertEqual((0, None), (again.changed, again.details["change"]))
        self.assertEqual(written, os.stat(self.a).st_mtime_ns)

    def test_its_cut_copy_is_no_tag_of_its_own(self):
        self.assertEqual(["Beach", LONG], self.indexed(self.a))
        self.add([self.a], ["Relay"])
        self.assertEqual(["Beach", LONG, "Relay"], keywords_of(self.a))
        self.assertEqual(["Beach", LONG, "Relay"], self.indexed(self.a))

    def test_a_tag_that_is_the_cut_of_another_is_kept_when_xmp_holds_it(self):
        cut = LONG[:64]
        meta = {"XMP:Subject": [LONG, cut], "IPTC:Keywords": [cut, cut]}
        self.assertEqual([LONG, cut], vocabulary.extract_tags(meta))
        self.assertEqual([LONG], vocabulary.extract_tags({"XMP:Subject": [LONG], "IPTC:Keywords": [cut]}))


if __name__ == "__main__":
    unittest.main()

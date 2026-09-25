"""A journaled write of a photo's fields is one ExifTool command (docs/findings.md, #271).

It was two: the values set, then the cleared fields (`-FIELD=`) in a second command. A
crash between them left a file holding neither what the plan read nor what it was to
hold, which settling calls a conflict and never finishes. Real ExifTool, on a JPEG made
here: the clears still happen, in the one command that sets the rest. A DocumentID
went in a command of its own too (#282).
"""
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.files import field_values, identity  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AKeywordWrite(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        home = own_home.for_test(self, prefix="one_command_")
        self.photo = os.path.join(home.root, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photo, "JPEG")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo], tags={"XMP:Subject": ["Places/Harbour"], "IPTC:Keywords": ["Places/Harbour"],
                                            "XMP:HierarchicalSubject": ["Places/Harbour"],
                                            "EXIF:XPKeywords": "Places/Harbour"},
                        params=["-overwrite_original"])

    def test_sets_and_clears_in_one_command(self):
        # Every tag taken off: each keyword field cleared, and one caption set with them.
        values = dict(fields.keyword_fields([], []))
        values["XMP:Description"] = "Regatta"
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count, "a write was %d ExifTool commands" % execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertTrue(fields.same_fields(held, values), held)
        self.assertEqual([], held["XMP:HierarchicalSubject"])
        self.assertEqual(["Regatta"], held["XMP:Description"])

    def test_only_clears_is_one_command_too(self):
        values = {"XMP:HierarchicalSubject": [], "EXIF:XPKeywords": ""}
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count)
            held = field_values.read_one(et, self.photo, ["XMP:HierarchicalSubject", "EXIF:XPKeywords", "XMP:Subject"])
        self.assertEqual(([], [], ["Places/Harbour"]),
                         (held["XMP:HierarchicalSubject"], held["EXIF:XPKeywords"], held["XMP:Subject"]))

    def test_an_identity_goes_in_the_same_command(self):
        values = {identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-0001", "XMP:Subject": ["Places/Quay"],
                  "XMP:HierarchicalSubject": []}
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count, "a write was %d ExifTool commands" % execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertTrue(fields.same_fields(held, values), held)

    def test_an_identity_taken_away_goes_in_the_same_command(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values.write(et, self.photo, {identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-0002"})
            values = {identity.DOCUMENT_ID_FIELD: "", "XMP:Subject": ["Places/Quay"]}
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertEqual(([], ["Places/Quay"]), (held[identity.DOCUMENT_ID_FIELD], held["XMP:Subject"]))


if __name__ == "__main__":
    unittest.main()

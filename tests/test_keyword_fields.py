"""The fields a keyword or caption write sets, listed once (tagpup.core.fields), and the
write itself (tagpup.files.keywords).

Saving a photo and the CLI's writer each kept their own list of the caption fields;
now both take caption_fields(). The guard below fails on a third copy.
"""
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import fields  # noqa: E402
from tagpup.files import keywords  # noqa: E402
from shipped_sources import ROOT, python_sources  # noqa: E402


class CaptionFields(unittest.TestCase):
    def test_a_caption_goes_to_every_field_the_pages_and_windows_read(self):
        self.assertEqual(fields.caption_fields("Harbour at dusk"), {
            "XMP:Description": "Harbour at dusk",
            "IPTC:Caption-Abstract": "Harbour at dusk",
            "EXIF:ImageDescription": "Harbour at dusk",
            "EXIF:XPComment": "Harbour at dusk",
        })

    def test_no_caption_clears_them_all(self):
        # An empty string clears a field as ExifTool writes it.
        self.assertEqual(set(fields.caption_fields("").values()), {""})

    def test_no_other_shipped_file_spells_them(self):
        owner = os.path.join("tagpup", "core", "fields.py")
        spelled = []
        for relative in python_sources():
            if relative == owner:
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                text = handle.read()
            if "EXIF:XPComment" in text or "EXIF:ImageDescription" in text:
                spelled.append(relative)
        self.assertEqual(spelled, [], "use tagpup.core.fields.caption_fields")


class WritingKeywords(unittest.TestCase):
    def test_a_photos_last_tag_is_deleted_not_left_in_place(self):
        # ExifTool reads an empty list as "no change".
        et = mock.MagicMock()
        flat, hierarchical = keywords.write_keywords(et, "a.jpg", [])
        self.assertEqual((flat, hierarchical), ([], []))
        et.execute.assert_called_once_with(
            "-XMP:Subject=", "-IPTC:Keywords=", "-XMP:HierarchicalSubject=",
            "-overwrite_original", "a.jpg")
        # The one string field clears by being written empty.
        self.assertEqual(et.set_tags.call_args.kwargs["tags"], {"EXIF:XPKeywords": ""})

    def test_extra_fields_go_in_the_same_write(self):
        et = mock.MagicMock()
        keywords.write_keywords(et, "a.jpg", ["People/Rowan Thackeray"],
                                extra_params=fields.caption_fields("Finish line"))
        written = et.set_tags.call_args.kwargs["tags"]
        self.assertEqual(written["XMP:HierarchicalSubject"], ["People/Rowan Thackeray"])
        self.assertEqual(written["XMP:Description"], "Finish line")
        et.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()

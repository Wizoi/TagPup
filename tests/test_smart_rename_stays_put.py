"""Smart Rename renames photos where they are, and never leaves one half-renamed.

A folder scan includes its subfolders, so a selection can hold photos from several.
Every new name was joined to the top folder, so a photo in a subfolder was moved out
of it. And the rename runs in two passes through temporary names, so a failure part
way left photos called tmp_rename_<hash>_<time>.jpg, which nothing would ever undo.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tagpup_server_finds_native_rows import (  # noqa: E402
    HandlerCase, fake_exiftool, fake_extractor, forward)


class SmartRenameStaysPut(HandlerCase):
    def rename(self, files):
        with patch("exiftool_session.ExifToolSession", fake_exiftool([{}])), \
                patch("metadata.MetadataExtractor", fake_extractor()):
            return self.call("handle_post_folder_rename_photos", {
                "folder_path": forward(self.folder),
                "photo_paths": [forward(p) for p in files],
                "grouping": "Regatta",
            })

    def everything_in(self, folder):
        return sorted(os.path.relpath(os.path.join(root, f), folder)
                      for root, _, names in os.walk(folder) for f in names)

    def test_a_photo_in_a_subfolder_stays_in_it(self):
        os.makedirs(os.path.join(self.folder, "Heats"))
        top = self.make_file("a.jpg")
        inner = self.make_file(os.path.join("Heats", "b.jpg"))

        renamed = self.rename([top, inner])["updated_paths"]

        by_name = {os.path.basename(old): new for old, new in renamed.items()}
        self.assertEqual(os.path.abspath(os.path.join(self.folder, "Heats")),
                         os.path.dirname(os.path.abspath(by_name["b.jpg"])))
        self.assertEqual(os.path.abspath(self.folder),
                         os.path.dirname(os.path.abspath(by_name["a.jpg"])))

    def test_a_failure_part_way_puts_every_photo_back(self):
        files = [self.make_file(n) for n in ("a.jpg", "b.jpg", "c.jpg")]
        before = self.everything_in(self.folder)
        real_rename = os.rename
        calls = []

        def rename(src, dst):
            calls.append((src, dst))
            # The second pass: a temporary name onto its final one. Fail the second.
            if "tmp_rename_" in os.path.basename(src) and sum(
                    "tmp_rename_" in os.path.basename(s) for s, _ in calls) == 2:
                raise PermissionError("the file is open in another program")
            return real_rename(src, dst)

        handler_result = {}
        with patch("os.rename", side_effect=rename):
            try:
                handler_result = self.rename(files)
            except AssertionError as e:  # an error reply is an acceptable answer here
                handler_result = {"error": str(e)}

        leftovers = [f for f in self.everything_in(self.folder) if "tmp_rename_" in f]
        self.assertEqual([], leftovers, "photos were left under temporary names")
        self.assertEqual(len(before), len(self.everything_in(self.folder)))
        self.assertTrue(handler_result, "no reply")


if __name__ == "__main__":
    unittest.main()

"""A bulk write that stops part-way says which photos it wrote.

Add to all selected stops at the first photo it cannot write; the photos before it keep
their tags. The route answered only the error, so the page recorded none of them and
went on showing what the files no longer held. It answers `written`, {path: tags}, on a
failure part-way as on success.

Real ExifTool: a file named as ExifTool names its temporary file, in the way of the
second photo's write, makes it refuse that one.
"""
import os
import unittest

from tests.handler_harness import Library
from tests.test_bulk_writes_start_from_the_file import EXISTING, make_photo, tags_in
from tests.test_taxonomy_lifecycle import requires_exiftool

from tagpup.core import paths


@requires_exiftool
class StoppedPartWay(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.lib.hold(self.lib.photos)
        self.first = make_photo(os.path.join(self.lib.photos, "a.jpg"))
        self.second = make_photo(os.path.join(self.lib.photos, "b.jpg"))
        with open(self.second + "_exiftool_tmp", "wb") as handle:
            handle.write(b"left by another write")

    def test_the_reply_names_the_photos_written(self):
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.first, self.second], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(500, status, reply)
        self.assertFalse(reply["success"])
        self.assertEqual([paths.key(self.first)], [paths.key(p) for p in reply["written"]])
        self.assertEqual(sorted(EXISTING + ["Sunset"]), sorted(list(reply["written"].values())[0]))
        self.assertEqual(sorted(EXISTING + ["Sunset"]), sorted(tags_in(self.first)))

    def test_a_write_that_succeeds_says_what_each_photo_holds(self):
        os.remove(self.second + "_exiftool_tmp")
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.first, self.second], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(200, status, reply)
        self.assertEqual(2, len(reply["written"]))


if __name__ == "__main__":
    unittest.main()

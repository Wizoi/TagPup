"""A bulk write keeps every tag the photo already has.

Add to all selected and Apply All write a photo's whole keyword set. They computed it
from the folder cache, else the index, else nothing -- and after a restart the cache is
empty while the page goes on showing the folder, so a photo the index had no row for
kept only the tags being added. Everything else it carried was erased from the file.

Separately, the writers looked a photo up in the cache under its own directory, while
a scan files its whole recursive walk under the folder that was scanned. A photo in a
subfolder was never found, and its record went on showing what it held before.
"""
import os
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

import tagpup_server
from exiftool_session import ExifToolSession
from tagpup_server import TagPupHTTPRequestHandler

EXISTING = ["Beach", "Holiday/Summer"]


def make_photo(path, tags=EXISTING, title=None):
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (32, 24), (90, 110, 130)).save(path, "JPEG")
    flat, hierarchical = tagpup_server.expand_tag_fields(list(tags))
    fields = {"XMP:Subject": flat, "IPTC:Keywords": flat, "XMP:HierarchicalSubject": hierarchical}
    if title:
        fields["XMP:Description"] = title
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=fields, params=["-overwrite_original"])
    return path


def tags_in(path):
    """The photo's tags, read the way a folder scan reads them."""
    from metadata import clean_metadata_value, extract_tags

    with ExifToolSession(executable=EXIFTOOL) as et:
        meta = et.get_tags([path], tags=["IPTC:Keywords", "XMP:Subject",
                                         "XMP:HierarchicalSubject"])[0]
    return extract_tags({k: clean_metadata_value(v) for k, v in meta.items()})


@requires_exiftool
class TestTheFileIsTheStartingPoint(unittest.TestCase):
    """No cache entry and no index row: the page after a restart, on a new photo."""

    def setUp(self):
        self.lib = Library(self)
        self.photo = make_photo(os.path.join(self.lib.photos, "a.jpg"))
        self.handler = self.lib.handler(EXIFTOOL)

    def test_bulk_add_keeps_the_keywords_already_in_the_file(self):
        status, reply = self.handler.call("handle_post_photos_bulk_tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))

    def test_bulk_remove_takes_away_only_that_tag(self):
        status, reply = self.handler.call("handle_post_photos_bulk_tags", {
            "paths": [self.photo], "add_tags": [], "remove_tags": ["Beach"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(tags_in(self.photo), ["Holiday/Summer"])

    def test_apply_all_keeps_the_keywords_already_in_the_file(self):
        folder_key = tagpup_server.paths.key(self.lib.photos)
        self.lib.suggestion_runs().statuses[folder_key] = {
            "status": "completed",
            "suggestions": {os.path.abspath(self.photo): {
                "tags": [{"tag": "Sunset", "score": 0.9}], "people": []}},
        }
        status, reply = self.handler.call("handle_post_folder_auto_apply", {
            "folder_path": self.lib.photos})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))

    def test_the_file_wins_over_a_stale_cache_entry(self):
        # The cache says the photo holds nothing (it was edited elsewhere since).
        TagPupHTTPRequestHandler.folder_cache[tagpup_server.paths.key(self.lib.photos)] = {
            tagpup_server.paths.key(self.photo): {
                "path": os.path.abspath(self.photo), "tags": [], "raw_metadata": {}}}
        status, reply = self.handler.call("handle_post_photos_bulk_tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))


@requires_exiftool
class TestASubfolderPhotoIsFoundInTheCache(unittest.TestCase):
    """The scan files a subfolder's photos under the folder that was scanned."""

    def setUp(self):
        self.lib = Library(self)
        self.photo = make_photo(os.path.join(self.lib.photos, "Day 1", "a.jpg"))
        self.handler = self.lib.handler(EXIFTOOL)
        status, reply = self.handler.call("handle_get_folder_scan", None,
                                          {"path": [self.lib.photos]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(len(reply), 1)

    def cached(self):
        folder_map = TagPupHTTPRequestHandler.folder_cache[tagpup_server.paths.key(self.lib.photos)]
        return folder_map.get(tagpup_server.paths.key(self.photo))

    def test_save_metadata_updates_its_record(self):
        status, reply = self.handler.call("handle_post_photo_save_metadata", {
            "path": self.photo, "title": "", "tags": ["Beach", "Sunset"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(self.cached()["tags"]), ["Beach", "Sunset"])

    def test_bulk_tags_update_its_record(self):
        status, reply = self.handler.call("handle_post_photos_bulk_tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertIn("Sunset", self.cached()["tags"])

    def test_apply_all_updates_its_record(self):
        folder_key = tagpup_server.paths.key(self.lib.photos)
        self.lib.suggestion_runs().statuses[folder_key] = {
            "status": "completed",
            "suggestions": {os.path.abspath(self.photo): {
                "tags": [{"tag": "Sunset", "score": 0.9}], "people": []}},
        }
        status, reply = self.handler.call("handle_post_folder_auto_apply", {
            "folder_path": self.lib.photos})
        self.assertEqual(status, 200, reply)
        self.assertIn("Sunset", self.cached()["tags"])

    def test_delete_removes_its_record(self):
        # Removed outright here, not sent to this machine's Recycle Bin.
        def remove(path):
            os.remove(path)
            return True
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=remove):
            status, reply = self.handler.call("handle_post_photo_delete", {"path": self.photo})
        self.assertEqual(status, 200, reply)
        self.assertIsNone(self.cached())

    def test_time_shift_of_the_subfolder_updates_the_record_under_the_parent(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo], tags={"EXIF:DateTimeOriginal": "2020:06:01 10:00:00"},
                        params=["-overwrite_original"])
        status, reply = self.handler.call("handle_post_folder_time_shift", {
            "folder_path": os.path.dirname(self.photo), "camera_model": "All Cameras",
            "shift_minutes": 30})
        self.assertEqual(status, 200, reply)
        raw = self.cached()["raw_metadata"]
        self.assertEqual(raw.get("EXIF:DateTimeOriginal"), "2020:06:01 10:30:00")

    def test_the_record_is_found_whichever_folder_was_scanned(self):
        entries = TagPupHTTPRequestHandler.cached_photo_entries(self.photo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]["path"], os.path.abspath(self.photo))


if __name__ == "__main__":
    unittest.main()

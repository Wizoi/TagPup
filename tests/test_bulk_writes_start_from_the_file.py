"""A bulk write keeps every tag the photo already has.

Add to all selected and Apply All write a photo's whole keyword set. They computed it
from the folder cache, else the index, else nothing -- and after a restart the cache is
empty while the page goes on showing the folder, so a photo the index had no row for
kept only the tags being added. Everything else it carried was erased from the file.

Separately, the writers looked a photo up in the cache under its own directory, while
a scan files its whole recursive walk under the folder that was scanned. A photo in a
subfolder was never found, and its record went on showing what it held before.

A bulk write of a folder's photos that stops part way says which photos it wrote, so the page records them; one that
meets a photo whose file is gone writes the rest and lists it.

Lifted: test_a_partial_bulk_write_says_what_it_wrote, test_apply_all_stopped_half_way_says_what_it_wrote (#878) and
test_bulk_tags_missing (#566; its stood-in ExifTool is the real one here, and its route test, which stood in the
service, is the two tests of the route). Each was broken in a scratch copy and this file fails: bulk-tags answering a
failure without `written`, tagpup_routes._refused_after_writing without it (#878), tagging.leave_out_missing keeping every
photo (#566), and taking a file it cannot read for a missing one.
"""
import os
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

from tagpup.core import paths
from tagpup.core.fields import expand_tag_fields
from tagpup.files import shares
from tagpup.files.exiftool_session import ExifToolSession
from tagpup.services import file_changes, tagging
from tagpup.services import roots as roots_service

EXISTING = ["Beach", "Holiday/Summer"]


def make_photo(path, tags=EXISTING, title=None):
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (32, 24), (90, 110, 130)).save(path, "JPEG")
    flat, hierarchical = expand_tag_fields(list(tags))
    fields = {"XMP:Subject": flat, "IPTC:Keywords": flat, "XMP:HierarchicalSubject": hierarchical}
    if title:
        fields["XMP:Description"] = title
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=fields, params=["-overwrite_original"])
    return path


def tags_in(path):
    """The photo's tags, read the way a folder scan reads them."""
    from tagpup.core.vocabulary import extract_tags
    from tagpup.files.metadata import clean_metadata_value

    with ExifToolSession(executable=EXIFTOOL) as et:
        meta = et.get_tags([path], tags=["IPTC:Keywords", "XMP:Subject",
                                         "XMP:HierarchicalSubject"])[0]
    return extract_tags({k: clean_metadata_value(v) for k, v in meta.items()})


@requires_exiftool
class TestTheFileIsTheStartingPoint(unittest.TestCase):
    """No cache entry and no index row: the page after a restart, on a new photo."""

    def setUp(self):
        self.lib = Library(self)
        self.lib.hold(self.lib.photos)
        self.photo = make_photo(os.path.join(self.lib.photos, "a.jpg"))

    def test_bulk_add_keeps_the_keywords_already_in_the_file(self):
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))
        self.assertEqual({paths.key(self.photo): sorted(EXISTING + ["Sunset"])},
                         {paths.key(path): sorted(tags) for path, tags in reply["written"].items()},
                         "a write that succeeds says what each photo holds")

    def test_bulk_remove_takes_away_only_that_tag(self):
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.photo], "add_tags": [], "remove_tags": ["Beach"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(tags_in(self.photo), ["Holiday/Summer"])

    def test_apply_all_keeps_the_keywords_already_in_the_file(self):
        self.lib.save_suggestions({os.path.abspath(self.photo): {
            "tags": [{"tag": "Sunset", "score": 0.9}], "people": []}})
        status, reply = self.lib.post("/api/folder/auto-apply", {"folder_path": self.lib.photos})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))

    def test_the_file_wins_over_a_stale_cache_entry(self):
        # The cache says the photo holds nothing (it was edited elsewhere since).
        self.lib.folders().put(self.lib.photos, {
            paths.key(self.photo): {"path": os.path.abspath(self.photo), "tags": [], "raw_metadata": {}}})
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(tags_in(self.photo)), sorted(EXISTING + ["Sunset"]))


@requires_exiftool
class TestAWriteStoppedPartWay(unittest.TestCase):
    """The page is told which photos were written, whichever way the write stopped."""

    def setUp(self):
        self.lib = Library(self)
        self.lib.hold(self.lib.photos)
        self.first, self.second, self.third = (make_photo(os.path.join(self.lib.photos, name))
                                               for name in ("a.jpg", "b.jpg", "c.jpg"))

    def written(self, reply):
        return [paths.key(p) for p in reply["written"]]

    def test_a_file_exiftool_refuses_stops_the_run_and_the_reply_names_the_photos_before_it(self):
        with open(self.second + "_exiftool_tmp", "wb") as handle:    # in the way of the second photo's write
            handle.write(b"left by another write")
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.first, self.second, self.third], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(500, status, reply)
        self.assertFalse(reply["success"])
        self.assertEqual([paths.key(self.first)], self.written(reply))
        self.assertEqual(sorted(EXISTING + ["Sunset"]), sorted(list(reply["written"].values())[0]))
        self.assertEqual(sorted(EXISTING + ["Sunset"]), sorted(tags_in(self.first)))
        self.assertEqual(sorted(EXISTING), sorted(tags_in(self.third)), "the run stopped at the error")

    def apply_all_stopping_at_the_second(self):
        self.lib.suggestion_runs().looks[paths.key(self.lib.photos)] = {
            path: {"tags": [{"tag": "Sunset", "score": 0.9}], "people": []} for path in (self.first, self.second)}
        real, calls = file_changes._carry, []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")    # the library's roots changed meanwhile
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            return self.lib.post("/api/folder/auto-apply", {"folder_path": self.lib.photos})

    def test_an_apply_all_stopped_because_the_roots_changed_names_the_photos_written_and_records_them(self):
        scanned, _reply = self.lib.get("/api/folder/scan", {"path": self.lib.photos})
        self.assertEqual(200, scanned)
        status, reply = self.apply_all_stopping_at_the_second()
        self.assertIn(status, (400, 409), reply)
        self.assertFalse(reply["success"])
        (path, tags), = reply["written"].items()
        self.assertIn("Sunset", tags)
        self.assertIn("Sunset", tags_in(path))
        self.assertEqual(1, sum("Sunset" in tags_in(p) for p in (self.first, self.second)), "the one named is the one written")
        cached = [record for _held, record in self.lib.folders().entries_for(path)]
        self.assertTrue(cached, "the scan was not cached")
        self.assertTrue(all("Sunset" in (record.get("tags") or []) for record in cached), cached)

    def test_a_photo_whose_file_is_gone_is_listed_and_the_photos_after_it_are_written(self):
        os.remove(self.second)
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.first, self.second, self.third], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(200, status, reply)
        self.assertEqual(sorted([paths.key(self.first), paths.key(self.third)]), sorted(self.written(reply)))
        self.assertEqual(1, reply["skipped_missing"])
        self.assertEqual([paths.key(self.second)], [paths.key(each["path"]) for each in reply["skipped"]])
        self.assertEqual(sorted(EXISTING + ["Sunset"]), sorted(tags_in(self.third)))

    def test_a_selection_of_only_missing_photos_writes_nothing_and_says_so(self):
        os.remove(self.first)
        os.remove(self.second)
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.first, self.second], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual((200, {}, 2), (status, reply["written"], reply["skipped_missing"]), reply)

    def test_a_file_that_cannot_be_read_is_not_taken_for_missing(self):
        with mock.patch.object(shares, "stamp_of", return_value=shares.CANNOT_READ):
            self.assertEqual(([self.first, self.second], []), tagging.leave_out_missing([self.first, self.second]))
        with mock.patch.object(shares, "stamp_of", return_value=shares.UNANSWERED):
            self.assertEqual([], tagging.leave_out_missing([self.first])[1])


@requires_exiftool
class TestASubfolderPhotoIsFoundInTheCache(unittest.TestCase):
    """The scan files a subfolder's photos under the folder that was scanned."""

    def setUp(self):
        self.lib = Library(self)
        self.photo = make_photo(os.path.join(self.lib.photos, "Day 1", "a.jpg"))
        self.lib.hold(os.path.dirname(self.photo))
        status, reply = self.lib.get("/api/folder/scan", {"path": self.lib.photos})
        self.assertEqual(status, 200, reply)
        self.assertEqual(len(reply), 1)

    def cached(self):
        return self.lib.folders().peek(self.lib.photos).get(paths.key(self.photo))

    def test_save_metadata_updates_its_record(self):
        status, reply = self.lib.post("/api/photo/save-metadata", {
            "path": self.photo, "title": "", "tags": ["Beach", "Sunset"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(sorted(self.cached()["tags"]), ["Beach", "Sunset"])

    def test_bulk_tags_update_its_record(self):
        status, reply = self.lib.post("/api/photos/bulk-tags", {
            "paths": [self.photo], "add_tags": ["Sunset"], "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertIn("Sunset", self.cached()["tags"])

    def test_apply_all_updates_its_record(self):
        self.lib.save_suggestions({os.path.abspath(self.photo): {
            "tags": [{"tag": "Sunset", "score": 0.9}], "people": []}})
        status, reply = self.lib.post("/api/folder/auto-apply", {"folder_path": self.lib.photos})
        self.assertEqual(status, 200, reply)
        self.assertIn("Sunset", self.cached()["tags"])

    def test_delete_removes_its_record(self):
        # Removed outright here, not sent to this machine's Recycle Bin.
        def remove(path):
            os.remove(path)
            return True
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=remove):
            status, reply = self.lib.post("/api/photo/delete", {"path": self.photo})
        self.assertEqual(status, 200, reply)
        self.assertIsNone(self.cached())

    def test_time_shift_of_the_subfolder_updates_the_record_under_the_parent(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo], tags={"EXIF:DateTimeOriginal": "2020:06:01 10:00:00"},
                        params=["-overwrite_original"])
        status, reply = self.lib.post("/api/folder/time-shift", {
            "folder_path": os.path.dirname(self.photo), "camera_model": "All Cameras",
            "shift_minutes": 30})
        self.assertEqual(status, 200, reply)
        raw = self.cached()["raw_metadata"]
        self.assertEqual(raw.get("EXIF:DateTimeOriginal"), "2020:06:01 10:30:00")

    def test_the_record_is_found_whichever_folder_was_scanned(self):
        entries = self.lib.folders().entries_for(self.photo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]["path"], os.path.abspath(self.photo))


if __name__ == "__main__":
    unittest.main()

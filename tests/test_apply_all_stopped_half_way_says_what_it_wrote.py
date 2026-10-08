"""An Apply All stopped half-way because the library's roots changed says which photos it wrote.

/api/photos/bulk-tags answers `written` on exactly this case (a refused Result that has written
some files) and records them in the cached scan; /api/folder/auto-apply answered only the refusal,
so the page recorded none of them (and Ctrl+Z took back the operation before) and the cached
records of the written photos were stale (findings #878). One owner of "a write stopped half-way:
the page is told which": tagpup_routes._refused_after_writing.

Real ExifTool; the roots change is raised from the second file's write, as in
test_roots_review_fixes.AWriteStoppedHalfWay.
"""
import os
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_bulk_writes_start_from_the_file import make_photo, tags_in
from tests.test_taxonomy_lifecycle import requires_exiftool

from tagpup.core import paths
from tagpup.services import file_changes
from tagpup.services import roots as roots_service


@requires_exiftool
class AnApplyAllWhoseRootsChanged(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.lib.hold(self.lib.photos)
        self.first = make_photo(os.path.join(self.lib.photos, "a.jpg"))
        self.second = make_photo(os.path.join(self.lib.photos, "b.jpg"))
        self.lib.suggestion_runs().looks[paths.key(self.lib.photos)] = {
            path: {"tags": [{"tag": "Sunset", "score": 0.9}], "people": []} for path in (self.first, self.second)}

    def apply_all_stopping_at_the_second(self):
        real = file_changes._carry
        calls = []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            return self.lib.post("/api/folder/auto-apply", {"folder_path": self.lib.photos, "threshold": 0.0})

    def test_the_reply_names_the_photos_written(self):
        status, reply = self.apply_all_stopping_at_the_second()
        self.assertIn(status, (400, 409), reply)
        self.assertFalse(reply["success"])
        self.assertEqual(1, len(reply["written"]), reply)
        (path, tags), = reply["written"].items()
        self.assertIn("Sunset", tags)
        self.assertIn("Sunset", tags_in(path))
        self.assertEqual(1, sum("Sunset" in tags_in(p) for p in (self.first, self.second)),
                         "the photo the reply names is the one written")

    def test_the_cached_record_of_the_photo_written_says_so(self):
        folder = self.lib.folders()
        scanned, _reply = self.lib.get("/api/folder/scan", {"path": self.lib.photos})
        self.assertEqual(200, scanned)
        status, reply = self.apply_all_stopping_at_the_second()
        written, = reply["written"]
        cached = [record for _held, record in folder.entries_for(written)]
        self.assertTrue(cached, "the scan was not cached")
        self.assertTrue(all("Sunset" in (record.get("tags") or []) for record in cached), cached)


if __name__ == "__main__":
    unittest.main()

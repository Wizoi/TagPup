"""A bulk tag write leaves out a photo whose file is gone and writes the rest (findings #566; phase 9c).

The library still holds the row of a missing photo and a view shows it, so a selection can name one. It is skipped and
listed -- the photos after it ARE written -- while a read or write ERROR of a present file still stops the run, as it
always did. ExifTool is stood in for; the rows and the tag tree are real. Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402
from test_service_replace_tag import keeps_writes  # noqa: E402

from tagpup.services import tagging  # noqa: E402


class LeavingOutMissing(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.a, self.b, self.c = (self.lib.photo(n) for n in ("a.jpg", "b.jpg", "c.jpg"))
        for path in (self.a, self.b, self.c):
            self.lib.add_row(path, tags=[])
        self.files = {self.a: [], self.b: [], self.c: []}
        self.et = mock.MagicMock()
        self.et.get_tags.side_effect = lambda paths, tags=None: [
            {"SourceFile": p, "XMP:Subject": list(self.files.get(p, []))} for p in paths]
        keeps_writes(self.et, self.files)
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = self.et
        session.return_value.__exit__.return_value = False
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_missing_photo_in_the_middle_is_listed_and_the_photos_after_it_are_written(self):
        os.remove(self.b)
        result = tagging.change_tags(self.lib.library, [self.a, self.b, self.c], ["Harbour"], [], "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertEqual((result.attempted, result.changed), (3, 2))
        self.assertEqual(sorted(result.details["written"]), sorted([self.a, self.c]))
        self.assertEqual(self.files[self.a], ["Harbour"])
        self.assertEqual(self.files[self.c], ["Harbour"])
        self.assertEqual(self.files[self.b], [])
        self.assertEqual([what for what, _why in result.skipped], [self.b])
        self.assertEqual(result.details[tagging.SKIPPED_MISSING], 1)

    def test_a_selection_of_only_missing_photos_writes_nothing_and_says_so(self):
        os.remove(self.a)
        os.remove(self.b)
        result = tagging.change_tags(self.lib.library, [self.a, self.b], ["Harbour"], [], "exiftool")
        self.assertTrue(result.ok)
        self.assertEqual((result.attempted, result.changed, result.details["written"]), (2, 0, {}))
        self.assertEqual(result.details[tagging.SKIPPED_MISSING], 2)
        self.et.set_tags.assert_not_called()

    def test_a_write_error_of_a_present_file_still_stops_the_run(self):
        os.remove(self.a)
        keeps = self.et.set_tags.side_effect

        def set_tags(paths, tags=None, params=None):
            if paths == [self.b]:
                raise RuntimeError("the file is locked")
            keeps(paths, tags=tags, params=params)
        self.et.set_tags.side_effect = set_tags
        result = tagging.change_tags(self.lib.library, [self.a, self.b, self.c], ["Harbour"], [], "exiftool")
        self.assertEqual((result.changed, result.message()), (0, "the file is locked"))
        self.assertEqual(self.files[self.c], [], "the run stopped at the error")

    def test_a_file_that_cannot_be_read_is_not_taken_for_missing(self):
        from tagpup.services import damaged_photos
        with mock.patch.object(damaged_photos, "stamp_of", return_value=damaged_photos.CANNOT_READ):
            present, gone = tagging.leave_out_missing([self.a, self.b])
        self.assertEqual((present, gone), ([self.a, self.b], []))
        with mock.patch.object(damaged_photos, "stamp_of", return_value=damaged_photos.UNANSWERED):
            self.assertEqual(tagging.leave_out_missing([self.a])[1], [])


class TheRoute(unittest.TestCase):
    def test_the_reply_lists_the_missing_photo_and_counts_it(self):
        app, home = web_client.app_for(self, "tagpup")
        client = app.test_client()
        lib_path = home.library("library.db")
        paths = [os.path.join(home.root, "p%d.jpg" % n) for n in range(3)]
        for path in paths:
            with open(path, "wb") as handle:
                handle.write(b"x")
        with mock.patch("tagpup.web.tagpup_routes.tagging_actions.change_tags") as change:
            from tagpup.core.result import Result
            result = Result(attempted=3, changed=2)
            result.details.update({"written": {}, "skipped_missing": 1})
            result.skip(paths[1], tagging.MISSING_WHY)
            change.return_value = result
            reply = client.post("/library/api/photos/bulk-tags", json={"paths": paths, "add_tags": ["Harbour"], "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual(1, body["skipped_missing"])
        self.assertEqual([paths[1]], [each["path"] for each in body["skipped"]])
        self.assertTrue(lib_path)


if __name__ == "__main__":
    unittest.main()

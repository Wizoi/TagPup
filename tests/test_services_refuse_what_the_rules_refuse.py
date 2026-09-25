"""Every service that writes a value checks it against tagpup.core.validation first,
and refuses with the rule's message, writing nothing.

The pages ask the same rules before they send, but a page is not the only way in: the
CLI and the MCP tools call the services too, and a page can be bypassed. Before the one
validator, bulk tagging, Apply All, Smart Rename, Shift Date Taken, moving a deleted
tag's photos, the CLI's write, a new library and indexing a folder took what they were
given; the web routes checked some of them, and nothing else did. The rules themselves
are tested with the cases the pages share (test_validation.py).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402
import own_home  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.result import Result  # noqa: E402
from tagpup.services import indexing, libraries, photos, tagging, tags  # noqa: E402

PIPE = 'A tag cannot contain "|": other programs read it as a break between levels. Use "/" instead.'
EMPTY_LEVEL = 'A tag cannot have an empty level, as in "A//B" or "A/".'
CAPTION = "A caption cannot contain a control character other than a tab or a line break."
BELL = chr(7)


def fake_exiftool(fields):
    """An ExifTool session whose file holds `fields`, recording every write."""
    et = mock.MagicMock()
    et.get_tags.return_value = [dict(fields)]
    session = mock.MagicMock()
    session.return_value.__enter__.return_value = et
    session.return_value.__exit__.return_value = False
    return session, et


class PhotoCase(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("IMG_0100.jpg")
        self.lib.add_row(self.photo, tags=["Places/Harbour"])

    def exiftool(self, fields=None):
        session, et = fake_exiftool(fields or {"XMP:Subject": ["Places/Harbour"]})
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", session)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session, et


class Tagging(PhotoCase):
    def test_tags_added_to_many_photos(self):
        session, et = self.exiftool()
        result = tagging.change_tags(self.lib.library, [self.photo], ["Places//Dunes"], [], "exiftool")
        self.assertEqual(result.refused, EMPTY_LEVEL)
        self.assertEqual((result.changed, result.details["written"]), (0, {}))
        session.assert_not_called()

    def test_tags_added_many_photos_are_spelled_once(self):
        # The route spelled them before; the service does now, for every caller.
        _session, et = self.exiftool()
        result = tagging.change_tags(self.lib.library, [self.photo], [" Places / Dunes "], [], "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertIn("Places/Dunes", result.details["written"][self.photo][0])

    def test_taking_a_bad_tag_off_is_still_allowed(self):
        self.exiftool({"XMP:Subject": ["Legacy|Keyword"]})
        result = tagging.change_tags(self.lib.library, [self.photo], [], ["Legacy|Keyword"], "exiftool")
        self.assertIsNone(result.refused)

    def test_apply_all(self):
        session, _et = self.exiftool()
        result = tagging.add_tags(self.lib.library, {self.photo: ["Places/Dunes", "Places|Harbour"]}, "exiftool")
        self.assertEqual(result.refused, PIPE)
        session.assert_not_called()

    def test_a_caption_being_set(self):
        _session, et = self.exiftool()
        result = tagging.save_photo(self.lib.library, self.photo, "Relays" + BELL, ["Places/Harbour"],
                                    None, "exiftool", "{grouping} - {index} - {caption}")
        self.assertEqual(result.refused, CAPTION)
        et.set_tags.assert_not_called()
        et.execute.assert_not_called()

    def test_a_caption_the_file_holds_already_is_not_being_set(self):
        # Written by another program; refusing it would leave the photo unsaveable.
        _session, et = self.exiftool({"XMP:Subject": ["Places/Harbour"], "XMP:Description": "Relays" + BELL})
        with mock.patch("tagpup.files.metadata.sync_title_to_filename", side_effect=lambda p, *rest: p):
            result = tagging.save_photo(self.lib.library, self.photo, "Relays" + BELL, ["Places/Harbour"],
                                        None, "exiftool", "{grouping} - {index} - {caption}")
        self.assertIsNone(result.refused)
        et.set_tags.assert_called()

    def test_the_clis_write(self):
        session, _et = self.exiftool()
        result = tagging.write_suggestions(self.lib.library, [(self.photo, ["Places|Dunes"], "")], "exiftool")
        self.assertEqual((result.refused, result.changed), (PIPE, 0))
        session.assert_not_called()


class Renaming(PhotoCase):
    def test_smart_rename_refuses_a_grouping_that_breaks_its_names(self):
        with mock.patch("tagpup.files.names.read_for_renaming") as read, \
                mock.patch("tagpup.files.names.rename_all") as rename:
            result = photos.smart_rename(self.lib.library, [self.photo], "2019-06 - Summer Camp",
                                         "{grouping} - {index} - {caption}", "exiftool")
        self.assertIn('cannot contain " - "', result.refused or "")
        read.assert_not_called()
        rename.assert_not_called()
        self.assertTrue(os.path.exists(self.photo))

    def test_a_time_shift_is_whole_minutes(self):
        with mock.patch("tagpup.files.times.shift_date_taken") as shift:
            result = photos.shift_date_taken(self.lib.library, [self.photo], "ninety", "exiftool")
        self.assertEqual(result.refused, "A time shift is a whole number of minutes.")
        shift.assert_not_called()


class TheTree(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.rewrites = []
        patcher = mock.patch("tagpup.services.tagging.replace_tag",
                             side_effect=lambda library, paths, old, new, exe: self.rewrites.append(new)
                             or Result(attempted=len(paths), changed=len(paths)))
        patcher.start()
        self.addCleanup(patcher.stop)
        tags.create(self.lib.library, "Activity/Rowing")
        self.photo = self.lib.photo("a.jpg")
        self.lib.add_row(self.photo, tags=["Activity/Rowing"])
        (self.rowing,), = self.lib.rows("SELECT id FROM tag_taxonomy WHERE tag = 'Activity/Rowing'")

    def test_a_deleted_tags_photos_are_not_moved_to_one_that_may_not_be_set(self):
        # It was spelled once and written: "Places|Harbour" became Places/Harbour.
        result = tags.delete(self.lib.library, self.rowing, "move", "Places|Harbour", "exiftool")
        self.assertEqual(result.refused, PIPE)
        self.assertEqual(self.rewrites, [])
        self.assertIn(("Activity/Rowing",), self.lib.rows("SELECT tag FROM tag_taxonomy"))

    def test_an_empty_tag_is_refused_in_the_rules_words(self):
        self.assertEqual(tags.create(self.lib.library, "  ").refused, "A tag cannot be empty.")

    def test_a_person_renamed_to_one_of_tagtuners_lists(self):
        result = tags.rename_person(self.lib.library, "Rowan Thackeray", "Unmatched", "exiftool")
        self.assertEqual(result.refused, "'Unmatched' is the name of one of TagTuner's lists, not a person's.")


class ANewLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)

    def test_a_name_the_urls_route_is_not_made(self):
        path = self.home.library("api.db")
        result = libraries.create(path)
        self.assertEqual(result.refused, "'api' is the name of one of the app's own pages; choose another")
        self.assertFalse(os.path.exists(path))

    def test_a_plain_name_is_made_and_says_so(self):
        path = self.home.library("harbour_2026.db")
        self.assertEqual((libraries.create(path).changed, libraries.create(path).changed), (1, 0))
        self.assertTrue(os.path.exists(path))

    def test_the_picker_answers_with_the_rules_words(self):
        app, _home = web_client.app_for(self, "tuner")
        client = app.test_client()
        for typed, said in (("API", "'API' is the name of one of the app's own pages; choose another"),
                            ("kr track", "A library's name can hold only letters, numbers, underscores and hyphens."),
                            ("", "A library needs a name.")):
            with self.subTest(name=typed):
                reply = client.post("/api/databases/create", json={"db_name": typed})
                self.assertEqual((reply.status_code, reply.get_json()["error"]), (400, said))
        reply = client.post("/api/databases/create", json={"db_name": "harbour_2026"})
        self.assertEqual(reply.get_json(), {"success": True, "db_name": "harbour_2026"})


class IndexingAFolder(unittest.TestCase):
    def test_a_folder_not_named_by_its_full_path_runs_nothing(self):
        with mock.patch("tagpup.core.processes.start") as start:
            result = indexing.index_folder(TempLibrary(self).library, "Photos/2020", os.getcwd())
        self.assertEqual(result.refused, "A folder is named by its full path, as in D:/Photos.")
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()

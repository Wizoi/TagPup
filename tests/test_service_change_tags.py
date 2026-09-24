"""tagpup.services.tagging.change_tags and add_tags: tags added to and taken off a
selection, and Apply All on a folder's suggestions.

ExifTool is stood in for, answering each read with what the file holds; the index rows
and the tag tree are real.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import tagging  # noqa: E402


class ChangingTags(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.a, self.b, self.c = (self.lib.photo(n) for n in ("a.jpg", "b.jpg", "c.jpg"))
        for path in (self.a, self.b, self.c):
            self.lib.add_row(path, tags=["Stale Index Tag"])
        # What each file really holds, which is not what its row says.
        self.files = {self.a: ["Beach"], self.b: ["Beach", "Relay"], self.c: []}
        self.lib.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('People', 'People', 1)")
        self.lib.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES"
                         " ('People/Rowan Thackeray', 'Rowan Thackeray', 1)")
        self.et = mock.MagicMock()
        self.et.get_tags.side_effect = lambda paths, tags=None: [
            {"SourceFile": p, "XMP:Subject": list(self.files.get(p, []))} for p in paths]
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = self.et
        session.return_value.__exit__.return_value = False
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def written(self, path):
        for call in self.et.set_tags.call_args_list:
            if call.args[0] == [path]:
                return sorted(call.kwargs["tags"]["XMP:Subject"])
        return None

    def indexed(self, path):
        return sorted(json.loads(self.lib.rows("SELECT tags FROM photos WHERE path = ?", (path,))[0][0]))

    def test_a_change_starts_from_what_each_file_holds(self):
        result = tagging.change_tags(self.lib.library, [self.a, self.b], ["Harbour"], ["Relay"], "exiftool")
        self.assertEqual((result.attempted, result.changed, result.errors), (2, 2, []))
        self.assertEqual(self.written(self.a), ["Beach", "Harbour"])
        self.assertEqual(self.written(self.b), ["Beach", "Harbour"])
        self.assertEqual(self.indexed(self.b), ["Beach", "Harbour"])

    def test_a_bare_name_is_written_as_the_tag_the_person_is_filed_under(self):
        tagging.change_tags(self.lib.library, [self.c], ["Rowan Thackeray"], [], "exiftool")
        self.assertEqual(self.written(self.c), ["People/Rowan Thackeray"])

    def test_apply_all_adds_each_photo_its_own_and_leaves_the_rest(self):
        result = tagging.add_tags(self.lib.library, {self.a: ["Harbour"], self.b: [], self.c: ["Relay"]},
                                  "exiftool")
        self.assertEqual((result.attempted, result.changed), (2, 2))
        self.assertEqual(self.written(self.a), ["Beach", "Harbour"])
        self.assertIsNone(self.written(self.b))
        self.assertEqual(self.written(self.c), ["Relay"])

    def test_it_stops_at_the_first_photo_it_cannot_write(self):
        def set_tags(paths, tags=None, params=None):
            if paths == [self.b]:
                raise RuntimeError("the file is locked")
        self.et.set_tags.side_effect = set_tags
        result = tagging.change_tags(self.lib.library, [self.a, self.b, self.c], ["Harbour"], [], "exiftool")
        self.assertEqual((result.changed, result.message()), (1, "the file is locked"))
        self.assertEqual(list(result.details["written"]), [self.a])
        self.assertEqual(self.indexed(self.c), ["Stale Index Tag"], "went on past the failure")


if __name__ == "__main__":
    unittest.main()

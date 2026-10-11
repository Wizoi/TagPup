"""Two chips the search page calls one (web/tagpup/search-model.js memberKey) are one set of photos to the server.

The page does not add a chip that is already in a list, by memberKey, which lower-cases a tag and a person; the server reads
a union's members by exact value (tagpup.services.library_view._union_of) and finds a tag or a person without case. The
page may keep two chips the server reads alike; it must never call two chips one that the server reads apart. The pairs are
tests/fixtures/chip_keys.json, which tests/frontend/search-page.test.mjs holds memberKey to.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import view_library  # noqa: E402

from tagpup.services import library_view  # noqa: E402

TABLE = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "chip_keys.json"), encoding="utf-8"))


class TheServerReadsSameChipsAlike(unittest.TestCase):
    def setUp(self):
        self.vl = view_library.ViewLibrary(self)
        self.vl.tree("Trips/Coast", "People/Wren Halloway", face_root="People")
        self.vl.photo("A", "a.jpg", taken="2024:01:01 00:00:00", tags=["Trips/Coast", "People/Wren Halloway"])
        self.vl.photo("A", "b.jpg", taken="2024:02:01 00:00:00", tags=["Trips/Coast"])
        self.vl.photo("B", "c.jpg", taken="2024:02:02 00:00:00", tags=["Other"])
        self.folder = os.path.join(self.vl.pictures, "A")

    def member(self, source):
        value = self.folder if source["value"] == "{folder}" else source["value"]
        return {"kind": source["kind"], "value": value, "recursive": source.get("recursive", False)}

    def ids(self, source):
        member = self.member(source)
        found = library_view.ids(self.vl.library, member["kind"], member["value"], member["recursive"])
        return found["ids"], found["total"]

    def test_the_table_has_what_it_says(self):
        self.assertGreater(sum(1 for each in TABLE["pairs"] if each["same_chip"]), 4)
        self.assertGreater(sum(1 for each in TABLE["pairs"] if not each["same_chip"]), 3)

    def test_a_pair_the_page_calls_one_chip_holds_the_same_photos(self):
        for each in TABLE["pairs"]:
            if not each["same_chip"]:
                continue
            with self.subTest(each["name"]):
                first, second = self.ids(each["a"]), self.ids(each["b"])
                self.assertTrue(first[0], "the pair names nothing the library holds: the table proves nothing")
                self.assertEqual(first, second)
                both = library_view.ids(self.vl.library, "any_of", [self.member(each["a"]), self.member(each["b"])])
                self.assertEqual(first, (both["ids"], both["total"]), "a union of the two lists a photo twice or not at all")

    def test_a_pair_the_page_keeps_apart_is_not_always_the_same_photos(self):
        """Not a rule: the page may be finer than the server. Here only that the table's `false` rows are not all the same
        photos, so that a page that called every pair one chip would fail the table's other half."""
        differing = [each["name"] for each in TABLE["pairs"] if not each["same_chip"] and self.ids(each["a"]) != self.ids(each["b"])]
        self.assertTrue(differing)


if __name__ == "__main__":
    unittest.main()

"""V28: somebody marks rows of the navigator in Filter mode, and the server reads the search the rows are as the owner meant it.

The page turns its rows' marks into a search (web/tagpup/navigator-model.js specFromRowStates) and sends its value as
library-source.js searchText writes it; tests/frontend/scenario-filtering-the-library-by-rows.test.mjs holds the page to the
values in tests/fixtures/filter_searches.json, and this holds the server (POST /api/library/ids, services.library_view._search_of)
to the photos the table says each finds: two people ANDed, one NOT another, a tab's any of ANDed with a year, a folder with its
subfolders, a tag with a month, a year without the photos with no tag, and nothing found. A lone `+` and a lone `&` of a tab are
two values that find the same photos (a union of one is its member).

Fictional names only: the libraries are photographs of real people, many of them minors.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

TABLE = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "filter_searches.json"), encoding="utf-8"))

ROWAN, ADA, MINA = "People/Rowan Thackeray", "People/Ada Lindqvist", "People/Mina Oyelaran"


class TheServerReadsAFilterAsItsRowsSay(unittest.TestCase):
    def setUp(self):
        self.app, home = web_client.app_for(self, "tagpup")
        self.vl = ViewLibrary(self, "library", home=home)
        self.vl.tree(ROWAN, ADA, MINA, face_root="People")
        self.vl.tree("Places/Beach")
        self.ids = {
            "Trips/together.jpg": self.vl.photo("Trips", "together.jpg", taken="2019:07:10 10:00:00", tags=[ROWAN, ADA, "Places/Beach"]),
            "Trips/Coast/rowan.jpg": self.vl.photo(os.path.join("Trips", "Coast"), "rowan.jpg", taken="2019:08:02 10:00:00",
                                                   tags=[ROWAN, "Places/Beach"]),
            "Home/ada.jpg": self.vl.photo("Home", "ada.jpg", taken="2020:01:05 10:00:00", tags=[ADA]),
            "Home/mina.jpg": self.vl.photo("Home", "mina.jpg", taken="2020:02:11 10:00:00", tags=[MINA]),
            "Home/plain.jpg": self.vl.photo("Home", "plain.jpg", taken="2020:03:01 10:00:00"),
        }
        self.client = self.app.test_client()

    def placed(self, value):
        """The table's value with {pictures} made this library's Pictures folder, as the navigator's folder row names it."""
        def member(each):
            if each["kind"] == "any_of":
                return dict(each, value=[member(one) for one in each["value"]])
            if each["kind"] == "folder":
                return dict(each, value=os.path.join(self.vl.pictures, *each["value"].split("/")[1:]))
            return each
        return {part: [member(each) for each in members] for part, members in value.items()}

    def found(self, value):
        reply = self.client.post("/library/api/library/ids", json={"kind": "search", "value": self.placed(value)})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()

    def test_each_filter_finds_the_photos_its_rows_say(self):
        self.assertGreaterEqual(len(TABLE["cases"]), 8)
        for case in TABLE["cases"]:
            with self.subTest(case["name"]):
                reply = self.found(case["value"])
                self.assertEqual(sorted(self.ids[name] for name in case["photos"]), sorted(reply["ids"]))
                self.assertEqual(len(case["photos"]), reply["total"])

    def test_a_lone_any_of_and_a_lone_must_find_the_same_photos(self):
        rowan = {"kind": "person", "value": ROWAN}
        alone = self.found({"all_of": [{"kind": "any_of", "value": [rowan]}]})
        must = self.found({"all_of": [rowan]})
        self.assertEqual((sorted(must["ids"]), must["total"]), (sorted(alone["ids"]), alone["total"]))
        self.assertEqual(2, must["total"])


if __name__ == "__main__":
    unittest.main()

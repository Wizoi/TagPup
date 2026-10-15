"""The tally's order and cap, and the plan it reads by (POST /api/library/selection/tally; tagpup.services.selection.tally;
docs/ARCHITECTURE.md, phase 9d-1). What the panel counts of a selection (the tags, people and folders, the caps) is
tests/test_selection_scenarios.py (V7).

Photos are rows as the indexer records them (tests/view_library.py). Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db  # noqa: E402


class Tally(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "Activity/Sailing", "People/Wren Halloway", "People/Rowan Thackeray",
                     face_root="People")
        wren, rowan = "People/Wren Halloway", "People/Rowan Thackeray"
        self.a = self.vl.photo("2024 Coast", "a.jpg", taken="2024:06:01 10:00:00", tags=["Trips/Coast", wren])
        self.b = self.vl.photo("2024 Coast", "b.jpg", taken="2024:06:02 10:00:00", tags=["Trips/Coast", "Activity/Sailing", wren, rowan])
        self.c = self.vl.photo("2023 Lakes", "c.jpg", taken="2023:01:02 10:00:00", tags=["Trips/Lakes"])
        self.d = self.vl.photo("Misc", "d.jpg", tags=[rowan])
        self.e = self.vl.photo("Misc", "e.jpg")

    def tally(self, selection_body):
        reply = self.client.post("/library/api/library/selection/tally", json={"selection": selection_body})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()


class OrderAndCap(Tally):
    def test_the_lists_are_in_the_shared_alphabetical_order(self):
        found = self.tally({"ids": [self.a, self.b, self.c, self.d, self.e]})
        for listing, name in ((found["tags"], "tag"), (found["people"], "name")):
            names = [each[name] for each in listing]
            self.assertEqual(sorted(names, key=vocabulary.tag_sort_key), names)

    def test_the_lists_are_cut_at_the_cap_keeping_the_most_used_and_counting_the_rest(self):
        with mock.patch.object(selection, "MAX_TALLIED", 2):
            found = self.tally({"ids": [self.a, self.b, self.c, self.d, self.e]})
        self.assertEqual(2, len(found["tags"]))
        self.assertEqual(1, found["more_tags"])
        self.assertEqual(0, found["more_people"])
        self.assertEqual("Trips/Coast", found["tags"][0]["tag"] if found["tags"][0]["count"] == 2 else found["tags"][1]["tag"])

    def test_people_tags_do_not_eat_the_slots_the_cut_keeps_for_keywords(self):
        # Two person tags as used as the keywords: the cut keeps the 2 most used, and it would have kept the people (#865).
        with mock.patch.object(selection, "MAX_TALLIED", 2):
            found = self.tally({"ids": [self.a, self.b, self.d]})
        self.assertEqual({"Trips/Coast", "Activity/Sailing"}, {each["tag"] for each in found["tags"]})
        self.assertEqual(0, found["more_tags"], "what the page really cuts")
        self.assertEqual({"Wren Halloway", "Rowan Thackeray"}, {each["name"] for each in found["people"]})


class Plans(Tally):
    def test_a_list_of_ids_seeks_each_photo_by_key_and_never_scans_the_library(self):
        seen = []
        real = library_view.opened

        def opened(library):
            conn = real(library)
            conn.set_trace_callback(seen.append)
            return conn
        with mock.patch.object(library_view, "opened", opened):
            self.tally({"ids": [self.a, self.b]})
        selects = [text for text in seen if text.lstrip().upper().startswith("SELECT") and "CROSS JOIN photos p" in text]
        self.assertTrue(selects)
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            conn.execute("CREATE TEMP TABLE sel (id INTEGER PRIMARY KEY)")
            for text in selects:
                plan = [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + text)]
                with self.subTest(statement=text[:60]):
                    self.assertFalse([line for line in plan if line.startswith("SCAN p")], plan)
                    self.assertTrue([line for line in plan if line.startswith("SEARCH p USING INTEGER PRIMARY KEY")], plan)
        finally:
            conn.close()

    def test_the_folders_of_a_list_of_ids_are_one_grouped_seek_of_photo_folder(self):
        seen = []
        real = library_view.opened

        def opened(library):
            conn = real(library)
            conn.set_trace_callback(seen.append)
            return conn
        with mock.patch.object(library_view, "opened", opened):
            self.tally({"ids": [self.a, self.c]})
        grouped = [text for text in seen if "FROM photo_folder" in text]
        self.assertEqual(1, len(grouped), seen)
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            conn.execute("CREATE TEMP TABLE sel (id INTEGER PRIMARY KEY)")
            plan = [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + grouped[0])]
        finally:
            conn.close()
        self.assertFalse([line for line in plan if line.startswith("SCAN photo_folder") or line.startswith("SCAN p ")], plan)
        self.assertTrue([line for line in plan if "photo_folder USING INTEGER PRIMARY KEY" in line], plan)


if __name__ == "__main__":
    unittest.main()

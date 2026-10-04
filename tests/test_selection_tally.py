"""The tags and people of a selection, counted by the server (POST /api/library/selection/tally; tagpup.services.selection.tally;
docs/ARCHITECTURE.md, phase 9d-1): what the selection panel shows for a library view, whose page holds only the cards near the
window.

Photos are rows as the indexer records them (tests/view_library.py), so photo_tags and photo_people are what the writes keep.
Fictional names only.
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
from tagpup.services import selection  # noqa: E402
from tagpup.store import db  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}


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
        self.d = self.vl.photo("Misc", "d.jpg", tags=[rowan, "Not In The Tree"])
        self.e = self.vl.photo("Misc", "e.jpg")

    def ask(self, selection_body, **kwargs):
        return self.client.post("/library/api/library/selection/tally", json={"selection": selection_body}, **kwargs)

    def tally(self, selection_body):
        reply = self.ask(selection_body)
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def counts(self, found):
        return ({each["tag"]: each["count"] for each in found["tags"]}, {each["name"]: each["count"] for each in found["people"]})


class Counting(Tally):
    def test_a_list_of_ids_counts_each_tag_and_person_by_its_photos(self):
        found = self.tally({"ids": [self.a, self.b, self.c, self.d, self.e]})
        self.assertEqual(5, found["total"])
        tags, people = self.counts(found)
        self.assertEqual({"Trips/Coast": 2, "Trips/Lakes": 1, "Activity/Sailing": 1, "People/Wren Halloway": 2,
                          "People/Rowan Thackeray": 2}, tags)
        self.assertEqual({"Wren Halloway": 2, "Rowan Thackeray": 2}, people)

    def test_only_the_selected_photos_are_counted(self):
        found = self.tally({"ids": [self.a, self.c]})
        self.assertEqual(2, found["total"])
        self.assertEqual(({"Trips/Coast": 1, "Trips/Lakes": 1, "People/Wren Halloway": 1}, {"Wren Halloway": 1}),
                         self.counts(found))

    def test_a_source_counts_what_it_holds_and_the_excluded_are_not_in_it(self):
        found = self.tally({"source": {"kind": "year", "value": "2024"}})
        self.assertEqual(2, found["total"])
        self.assertEqual({"Trips/Coast": 2, "Activity/Sailing": 1, "People/Wren Halloway": 2, "People/Rowan Thackeray": 1},
                         self.counts(found)[0])
        found = self.tally({"source": {"kind": "year", "value": "2024"}, "excluded": [self.b, self.e, 9999]})
        self.assertEqual(1, found["total"])
        self.assertEqual(({"Trips/Coast": 1, "People/Wren Halloway": 1}, {"Wren Halloway": 1}), self.counts(found))

    def test_the_whole_library_minus_all_of_it_is_nothing(self):
        found = self.tally({"source": {"kind": "all"}, "excluded": [self.a, self.b, self.c, self.d, self.e]})
        self.assertEqual((0, [], []), (found["total"], found["tags"], found["people"]))

    def test_a_source_and_the_same_photos_as_a_list_give_one_answer(self):
        for named in ({"kind": "all"}, {"kind": "keyword", "value": "Trips"}, {"kind": "person", "value": "Rowan Thackeray"},
                      {"kind": "folder", "value": os.path.join(self.vl.pictures, "Misc"), "recursive": True}, {"kind": "month", "value": "2024-06"}):
            with self.subTest(source=named):
                ids = self.client.get("/library/api/library/ids", query_string={
                    "kind": named["kind"], "value": named.get("value"), "folder": named.get("value"),
                    "recursive": "1" if named.get("recursive") else ""}).get_json()["ids"]
                self.assertEqual(self.tally({"ids": ids}), self.tally({"source": named}))

    def test_duplicates_and_ids_nobody_has_count_once_and_not_at_all(self):
        found = self.tally({"ids": [self.a, self.a, 9999, self.a]})
        self.assertEqual(1, found["total"])
        self.assertEqual(({"Trips/Coast": 1, "People/Wren Halloway": 1}, {"Wren Halloway": 1}), self.counts(found))

    def test_a_tag_no_node_holds_is_not_tallied(self):
        found = self.tally({"ids": [self.d]})
        self.assertEqual(({"People/Rowan Thackeray": 1}, {"Rowan Thackeray": 1}), self.counts(found))

    def test_a_person_spelled_two_ways_is_one_entry_counting_each_photo_once(self):
        self.vl.conn.execute("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, 5, ?, 'keyword')",
                             (self.a, "wren halloway"))
        self.vl.conn.execute("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, 5, ?, 'keyword')",
                             (self.e, "WREN HALLOWAY"))
        self.vl.conn.commit()
        found = self.tally({"ids": [self.a, self.b, self.e]})
        self.assertEqual({"Wren Halloway": 3, "Rowan Thackeray": 1}, self.counts(found)[1])

    def test_the_lists_are_in_the_shared_alphabetical_order(self):
        found = self.tally({"ids": [self.a, self.b, self.c, self.d, self.e]})
        for listing, name in ((found["tags"], "tag"), (found["people"], "name")):
            names = [each[name] for each in listing]
            self.assertEqual(sorted(names, key=vocabulary.tag_sort_key), names)

    def test_an_empty_selection_is_a_tally_of_nothing(self):
        found = self.tally({"ids": []})
        self.assertEqual((0, [], [], 0, 0), (found["total"], found["tags"], found["people"], found["more_tags"], found["more_people"]))

    def test_the_lists_are_cut_at_the_cap_keeping_the_most_used_and_counting_the_rest(self):
        with mock.patch.object(selection, "MAX_TALLIED", 2):
            found = self.tally({"ids": [self.a, self.b, self.c, self.d, self.e]})
        self.assertEqual(2, len(found["tags"]))
        self.assertEqual(3, found["more_tags"])
        self.assertEqual(0, found["more_people"])
        self.assertTrue(all(each["count"] == 2 for each in found["tags"]))

    def test_a_selection_above_what_a_job_takes_may_still_be_tallied(self):
        with mock.patch.object(selection, "MAX_SELECTED", 2):
            self.assertEqual(5, self.tally({"source": {"kind": "all"}})["total"])


class Plans(Tally):
    def test_a_list_of_ids_seeks_each_photo_by_key_and_never_scans_the_library(self):
        from tagpup.services import library_view
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


class Asking(Tally):
    def test_this_pc_only(self):
        self.assertEqual(403, self.ask({"ids": [self.a]}, environ_overrides=REMOTE).status_code)

    def test_what_means_nothing_is_a_400_with_a_sentence(self):
        for body in (None, {}, {"selection": None}, {"selection": {"ids": "x"}}, {"selection": {"source": {"kind": "nope"}}}):
            with self.subTest(body=body):
                reply = self.client.post("/library/api/library/selection/tally", json=body)
                self.assertEqual(400, reply.status_code)
                self.assertNotIn("Traceback", reply.get_json()["error"])

    def test_a_library_behind_is_a_409_and_not_a_500(self):
        with mock.patch("tagpup.services.library_view.store.ready", return_value=False):
            self.assertEqual(409, self.ask({"ids": [self.a]}).status_code)


if __name__ == "__main__":
    unittest.main()

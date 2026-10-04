"""A union of sources and the orders a source is read in (tagpup.services.library_view over tagpup.store.library_view;
docs/ARCHITECTURE.md, phase 9, the owner's review: #672 the navigator's rows selected together, #671 the sort).

A union (`any_of`) is the photos of any of a list of sources -- the rows a person selected in the navigator -- in one
statement: a photo in two of them is one photo, counted once; it pages by the keyset, its order is a source's, and a
selection by source (Select all, the tally, a bulk edit) takes it as it takes any source. The orders are Date Taken or
file name, either way, ties by id; photos with no Date Taken come after the dated ones in both directions.

Photos are rows as the indexer records them (tests/view_library.py), tags nodes made by taxonomy.add_path. Fictional names.
"""
import itertools
import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

WREN, ROWAN = "Wren Halloway", "Rowan Thackeray"


def walk(vl, kind, value=None, order=None, limit=2):
    """Every id in the order the keyset pages gave them, and the totals they said."""
    token, ids, totals = None, [], set()
    while True:
        found = library_view.view(vl.library, kind, value, False, token, limit, order)
        ids += found["ids"]
        totals.add(found["total"])
        token = found["next"]
        if token is None:
            return ids, totals


def union(*members):
    return json.dumps([{"kind": kind, "value": value, "recursive": recursive} for kind, value, recursive in members])


class Library(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("Trips/Coast/Harbour", "Trips/Lakes", "Activity/Sailing", "People/" + WREN, "People/" + ROWAN,
                face_root="People")
        self.july_20 = vl.photo("2020", "IMG_0002.jpg", taken="2020:07:04 10:00:00", tags=["Trips/Coast", "People/" + WREN])
        self.july_21 = vl.photo("2021", "img_0001.jpg", taken="2021:07:01 09:00:00", tags=["Trips/Coast/Harbour"])
        self.june_21 = vl.photo("2021", "IMG_0003.jpg", taken="2021:06:30 09:00:00", tags=["Trips/Lakes", "Activity/Sailing"])
        self.july_22 = vl.photo(os.path.join("2021", "Later"), "b.jpg", taken="2022:07:09 12:00:00",
                                tags=["Activity/Sailing", "People/" + ROWAN])
        self.dashed = vl.photo("2021", "dashed.jpg", taken="2021-03-01 10:00:00")   # a date written with dashes: no month
        self.undated_a = vl.photo("Misc", "Z.jpg")
        self.undated_b = vl.photo("Misc", "a.jpg")
        self.folder = os.path.join(vl.pictures, "2021")

    def ids(self, kind, value=None, order=None):
        return library_view.ids(self.vl.library, kind, value, order=order)


class TheUnion(Library):
    def test_twelve_julys_are_one_list_in_date_order_and_one_count(self):
        months = union(*[("month", "%d-07" % year, False) for year in range(2015, 2027)])
        found = self.ids("any_of", months)
        self.assertEqual([self.july_20, self.july_21, self.july_22], found["ids"])
        self.assertEqual(3, found["total"])
        self.assertEqual(["any_of"] + ["month"] * 12, [found["source"]["kind"]] + [m["kind"] for m in found["source"]["value"]])
        ids, totals = walk(self.vl, "any_of", months)
        self.assertEqual(found["ids"], ids, "the keyset pages are the list")
        self.assertEqual({3}, totals)

    def test_a_photo_in_two_selected_keywords_is_one_photo_counted_once(self):
        found = self.ids("any_of", union(("keyword", "Trips", False), ("keyword", "Activity/Sailing", False)))
        self.assertEqual([self.july_20, self.june_21, self.july_21, self.july_22], found["ids"])
        self.assertEqual(4, found["total"])
        self.assertEqual(len(found["ids"]), len(set(found["ids"])))

    def test_kinds_mix_and_a_folder_with_its_subfolders_takes_every_folder_under_it(self):
        found = self.ids("any_of", union(("folder", self.folder, True), ("person", WREN.upper(), False)))
        self.assertEqual({self.july_20, self.july_21, self.june_21, self.july_22, self.dashed}, set(found["ids"]))
        alone = self.ids("any_of", union(("folder", self.folder, False), ("person", ROWAN, False)))
        self.assertEqual({self.july_21, self.june_21, self.dashed, self.july_22}, set(alone["ids"]))
        only = self.ids("any_of", union(("folder", self.folder, False), ("month", "2099-01", False)))
        self.assertEqual({self.july_21, self.june_21, self.dashed}, set(only["ids"]), "the folder alone, not Later under it")

    def test_a_folder_named_alone_before_its_recursive_parent_keeps_the_parents_subfolders(self):
        # findings #695: the walk under a recursive folder skipped a folder already gathered, and so never went under it.
        deeper = self.vl.photo(os.path.join("2021", "Later", "Deeper"), "c.jpg", taken="2022:08:01 10:00:00")
        later = os.path.join(self.folder, "Later")
        whole = {self.july_21, self.june_21, self.dashed, self.july_22, deeper}
        members = [("folder", later, False), ("folder", self.folder, True), ("month", "2099-01", False)]
        for order in itertools.permutations(members):
            with self.subTest(order=[m[0] + ("+" if m[2] else "") for m in order]):
                self.assertEqual(whole, set(self.ids("any_of", union(*order))["ids"]))

    def test_a_keyword_alone_is_its_node_without_the_nodes_under_it(self):
        self.assertEqual([self.july_20], self.ids("keyword_only", "Trips/Coast")["ids"])
        self.assertEqual([self.july_20, self.july_21], self.ids("keyword", "Trips/Coast")["ids"])
        self.assertEqual([], self.ids("keyword_only", "Trips")["ids"], "no photo carries Trips itself")

    def test_a_year_is_its_months_and_its_other(self):
        other = self.ids("year_other", "2021")["ids"]
        self.assertEqual([self.dashed], other, "a date written with dashes names no month")
        months = [self.ids("month", "2021-%02d" % month)["ids"] for month in range(1, 13)]
        self.assertEqual(sorted(self.ids("year", "2021")["ids"]), sorted(other + sum(months, [])))
        rebuilt = self.ids("any_of", union(("year_other", "2021", False), *[("month", "2021-%02d" % m, False) for m in range(1, 13)]))
        self.assertEqual(sorted(self.ids("year", "2021")["ids"]), sorted(rebuilt["ids"]))

    def test_a_deleted_keyword_or_person_holds_nothing_and_the_rest_still_shows(self):
        found = self.ids("any_of", union(("keyword", "Gone/Long ago", False), ("person", "Nobody Atall", False),
                                         ("month", "2022-07", False)))
        self.assertEqual([self.july_22], found["ids"])
        empty = self.ids("any_of", union(("keyword", "Gone/Long ago", False), ("person", "Nobody Atall", False)))
        self.assertEqual(([], 0), (empty["ids"], empty["total"]))

    def test_the_undated_photos_come_after_the_dated_and_a_union_of_all_is_all(self):
        found = self.ids("any_of", union(("folder", os.path.join(self.vl.pictures, "Misc"), False), ("month", "2020-07", False)))
        self.assertEqual([self.july_20, self.undated_a, self.undated_b], found["ids"])
        everything = self.ids("any_of", union(("all", None, False), ("month", "2020-07", False)))
        self.assertEqual(self.ids("all")["ids"], everything["ids"])

    def test_a_union_of_one_is_that_source_and_a_repeat_is_once(self):
        found = self.ids("any_of", union(("month", "2020-07", False), ("month", "2020-07", False)))
        self.assertEqual({"kind": "month", "value": "2020-07", "recursive": False}, found["source"])

    def test_a_thousand_months_are_one_statement_sqlite_can_read(self):
        # A chain of ORs is nested as deep as it is long and SQLite refuses 1,000: the union's are a balanced tree.
        months = union(*[("month", "%04d-%02d" % (1950 + n // 12, n % 12 + 1), False) for n in range(library_view.MAX_MEMBERS)])
        found = self.ids("any_of", months)
        self.assertEqual({self.july_20, self.july_21, self.june_21, self.july_22}, set(found["ids"]))

    def test_what_cannot_be_a_union_is_refused_with_a_sentence(self):
        for value in ("not json", json.dumps({"kind": "month"}), "[]", json.dumps(["month"]),
                      union(("any_of", "[]", False), ("month", "2020-07", False)), union(("month", "July", False)),
                      union(*[("month", "2020-%02d" % (n % 12 + 1), n > 12) for n in range(library_view.MAX_MEMBERS + 1)])):
            with self.subTest(value=value[:60]):
                with self.assertRaises(Refused):
                    self.ids("any_of", value)

    def test_a_selection_by_a_union_source_resolves_and_tallies_it(self):
        named = {"kind": "any_of", "value": [{"kind": "month", "value": "2020-07"}, {"kind": "month", "value": "2021-07"},
                                             {"kind": "keyword", "value": "Trips/Coast"}]}
        chosen = selection.read(self.vl.library, {"source": named, "excluded": [self.july_21]})
        self.assertEqual([self.july_20], selection.resolve(self.vl.library, chosen).ids)
        tallied = selection.tally(self.vl.library, chosen)
        self.assertEqual(1, tallied["total"])
        self.assertEqual([WREN], [each["name"] for each in tallied["people"]])


class TheOrders(Library):
    def test_date_taken_newest_first_puts_the_undated_after_by_id_from_the_highest(self):
        found = self.ids("all", order="taken-desc")["ids"]
        # "2021-03-01" sorts as text does: after 2020, before "2021:..."
        self.assertEqual([self.july_22, self.july_21, self.june_21, self.dashed, self.july_20, self.undated_b, self.undated_a],
                         found)
        self.assertEqual([self.july_20, self.dashed, self.june_21, self.july_21, self.july_22, self.undated_a, self.undated_b],
                         self.ids("all")["ids"])

    def test_by_name_without_case_either_way_ties_by_id(self):
        twin = self.vl.photo("Other", "img_0001.JPG", taken="2010:01:01 00:00:00")
        by_name = self.ids("all", order="name")["ids"]
        self.assertEqual([self.undated_b, self.july_22, self.dashed, self.july_21, twin, self.july_20, self.june_21,
                          self.undated_a], by_name)
        self.assertEqual([self.undated_a, self.june_21, self.july_20, twin, self.july_21, self.dashed, self.july_22,
                          self.undated_b], self.ids("all", order="name-desc")["ids"])

    def test_every_order_pages_by_its_keyset_as_its_list(self):
        sources = (("all", None), ("folder", self.folder), ("keyword", "Trips"), ("any_of", union(("month", "2020-07", False),
                   ("folder", os.path.join(self.vl.pictures, "Misc"), False))))
        for order in store.ORDERS:
            for kind, value in sources:
                with self.subTest(order=order, kind=kind):
                    listed = library_view.ids(self.vl.library, kind, value, recursive=kind == "folder", order=order)
                    token, paged = None, []
                    while True:
                        page = library_view.view(self.vl.library, kind, value, kind == "folder", token, 2, order)
                        paged += page["ids"]
                        token = page["next"]
                        if token is None:
                            break
                    self.assertEqual(listed["ids"], paged)
                    self.assertEqual(order, page["order"])

    def test_without_the_name_index_the_order_by_name_is_the_same(self):
        with_index = self.ids("any_of", union(("year", "2021", False), ("month", "2020-07", False)), order="name")["ids"]
        conn = db.connect(self.vl.path)
        conn.execute("DROP INDEX %s" % store.NAME_INDEX)
        conn.commit()
        conn.close()
        self.assertEqual(with_index, self.ids("any_of", union(("year", "2021", False), ("month", "2020-07", False)),
                                              order="name")["ids"])

    def test_a_token_is_read_only_in_its_own_order_and_an_unknown_order_is_refused(self):
        first = library_view.view(self.vl.library, "all", limit=2, order="name")
        with self.assertRaises(Refused):
            library_view.view(self.vl.library, "all", after=first["next"], limit=2)
        with self.assertRaises(Refused):
            library_view.view(self.vl.library, "all", after=first["next"], limit=2, order="taken-desc")
        with self.assertRaises(Refused):
            self.ids("all", order="size")
        dated = library_view.view(self.vl.library, "all", limit=2)
        self.assertEqual(dated["next"], library_view.encode(library_view.decode(dated["next"])), "the old token is the same")


class ThePlans(Library):
    def plans(self, kind, value, order=None):
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            statements, _elapsed = store.id_plans(conn, library_view.source_of(self.vl.library, kind, value), 1000,
                                                  order or store.TAKEN)
        finally:
            conn.close()
        return "\n".join("\n".join(lines) for _sql, lines in statements)

    def test_a_union_seeks_an_index_for_each_kind_and_scans_no_table(self):
        text = self.plans("any_of", union(("keyword", "Trips", False), ("person", WREN, False), ("year", "2021", False),
                                          ("month", "2020-07", False), ("folder", self.folder, True)))
        self.assertIn("idx_photo_tags_tag (tag_id=?)", text)
        self.assertIn("idx_photo_people_name (name=?)", text)
        self.assertIn("idx_photo_folder_folder (folder_id=?)", text)
        self.assertNotIn("SCAN p\n", text + "\n")
        self.assertNotRegex(text, r"SCAN (p|photos)( |$)(?!USING COVERING INDEX)")

    def test_by_name_the_name_index_is_walked_in_order_and_nothing_sorted(self):
        for kind, value in (("all", None), ("keyword", "Trips"), ("any_of", union(("month", "2020-07", False),
                                                                                    ("person", ROWAN, False)))):
            for order in ("name", "name-desc"):
                with self.subTest(kind=kind, order=order):
                    text = self.plans(kind, value, order)
                    self.assertIn(store.NAME_INDEX, text)
                    self.assertNotIn("TEMP B-TREE", text)

    def test_newest_first_reads_the_date_index_backwards_with_no_sort(self):
        for kind, value in (("all", None), ("year", "2021"), ("month", "2021-07")):
            with self.subTest(kind=kind):
                self.assertNotIn("TEMP B-TREE", self.plans(kind, value, "taken-desc"))


class TheRoutes(unittest.TestCase):
    def test_the_ids_route_takes_a_union_and_an_order(self):
        app, home = web_client.app_for(self, "tagpup")
        vl = ViewLibrary(self, "library", home=home)
        b = vl.photo("A", "b.jpg", taken="2020:07:01 10:00:00")
        a = vl.photo("A", "a.jpg", taken="2021:07:01 10:00:00")
        vl.photo("A", "c.jpg", taken="2021:08:01 10:00:00")
        client = app.test_client()
        reply = client.get("/library/api/library/ids", query_string={
            "kind": "any_of", "value": union(("month", "2020-07", False), ("month", "2021-07", False)), "order": "name"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual(([a, b], 2, "name"), (reply.get_json()["ids"], reply.get_json()["total"], reply.get_json()["order"]))
        # A union longer than an address goes in a body (#696): 1,000 folders on a share, each path long.
        share = chr(92) * 2 + "photo-server-in-the-hall" + chr(92) + "family-pictures-archive"
        long_folders = [{"kind": "folder", "value": share + chr(92) + "%04d %s" % (n, "a long event name " * 6), "recursive": True}
                        for n in range(997)]
        members = long_folders + [{"kind": "month", "value": "2020-07"}, {"kind": "month", "value": "2021-07"},
                                  {"kind": "folder", "value": os.path.join(vl.pictures, "A"), "recursive": True}]
        self.assertGreater(len(urllib.parse.quote(json.dumps(members))), 262144, "longer than waitress reads of an address")
        posted = client.post("/library/api/library/ids", json={"kind": "any_of", "value": members, "order": "name"})
        self.assertEqual(200, posted.status_code, posted.get_data(as_text=True))
        self.assertEqual(3, posted.get_json()["total"])
        page = client.post("/library/api/library/view", json={"kind": "any_of", "value": members, "limit": 2})
        self.assertEqual((200, 2, 3), (page.status_code, len(page.get_json()["ids"]), page.get_json()["total"]))
        folder = client.post("/library/api/library/ids", json={"kind": "folder", "folder": os.path.join(vl.pictures, "A"),
                                                                "recursive": True, "order": "taken-desc"})
        self.assertEqual(3, folder.get_json()["total"])
        too_many = client.post("/library/api/library/ids", json={"kind": "any_of", "value": members + [
            {"kind": "month", "value": "2019-%02d" % m} for m in range(1, 3)]})
        self.assertEqual(400, too_many.status_code)
        refused = client.get("/library/api/library/ids", query_string={"kind": "all", "order": "sideways"})
        self.assertEqual(400, refused.status_code)
        self.assertIn("order must be one of", refused.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

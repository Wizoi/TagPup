"""A search as a source of the library views (tagpup.services.library_view over tagpup.store.library_view; docs/ARCHITECTURE.md,
phase 9e-1): the photos in ALL OF a list of sources, ANY OF another and NONE OF a third -- tags, people, folders, dates, and a
union (the navigator's selection, "within" which the search looks) as one member of `all_of` -- in one statement, paged by the
keyset in every order, and taken by a selection, the tally and a bulk edit as any source is. Its words are
tests/test_search_words.py's.

What each search should hold is worked out here from the members' own sources (library_view.ids of each), as sets, never from
the code under test. Photos are rows as the indexer records them (tests/view_library.py), tags nodes made by taxonomy.add_path,
a rename of a node by the tree's own edit. Fictional names.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_delete  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db, people, taxonomy  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

WREN, ROWAN, ELODIE = "Wren Halloway", "Rowan Thackeray", "Élodie Marchetti"


def member(kind, value=None, recursive=False):
    return {"kind": kind, "value": value, "recursive": recursive}


def search(all_of=(), any_of=(), none_of=(), words=None, text=True):
    value = {"all_of": list(all_of), "any_of": list(any_of), "none_of": list(none_of)}
    if words is not None:
        value["words"] = words
    return json.dumps(value) if text else value


class Library(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("Trips/Coast/Harbour", "Trips/Lakes", "Activity/Sailing", "Activity/Hiking", "People/" + WREN,
                "People/" + ROWAN, "People/" + ELODIE, face_root="People")
        P = "People/"
        self.p = [
            vl.photo("2020", "IMG_0002.jpg", taken="2020:07:04 10:00:00", tags=["Trips/Coast", P + WREN, P + ROWAN],
                     caption="Harbour wall"),
            vl.photo("2021", "img_0001.jpg", taken="2021:07:01 09:00:00", tags=["Trips/Coast/Harbour", P + WREN, "Activity/Sailing"]),
            vl.photo("2021", "IMG_0003.jpg", taken="2021:06:30 09:00:00", tags=["Trips/Lakes", "Activity/Sailing", P + ROWAN]),
            vl.photo(os.path.join("2021", "Later"), "b.jpg", taken="2022:07:09 12:00:00",
                     tags=["Activity/Sailing", P + ROWAN, P + ELODIE], caption="Out on the water"),
            vl.photo("2021", "dashed.jpg", taken="2021-03-01 10:00:00", tags=["Activity/Hiking", P + WREN]),
            vl.photo("Misc", "Z.jpg", tags=["Activity/Sailing", P + WREN]),
            vl.photo("Misc", "a.jpg", tags=["Trips/Lakes"]),
            vl.photo("Misc", "c.jpg"),
        ]
        self.folder = os.path.join(vl.pictures, "2021")
        self.misc = os.path.join(vl.pictures, "Misc")

    def ids(self, kind, value=None, order=None, recursive=False):
        return library_view.ids(self.vl.library, kind, value, recursive, order=order)

    def of(self, kind, value=None, recursive=False):
        """The photos of one source, as a set: what a search's member holds."""
        return set(self.ids(kind, value, recursive=recursive)["ids"])

    def everything(self):
        return set(self.ids("all")["ids"])

    def found(self, value, order=None):
        return self.ids("search", value, order)


class WhatASearchHolds(Library):
    def test_all_of_is_every_member_any_of_one_none_of_not_one(self):
        coast, sailing = self.of("keyword", "Trips/Coast"), self.of("keyword", "Activity/Sailing")
        wren, rowan = self.of("person", WREN), self.of("person", ROWAN)
        y2021 = self.of("year", "2021")
        cases = (
            (search(all_of=[member("keyword", "Trips/Coast"), member("person", WREN)]), coast & wren),
            (search(all_of=[member("keyword", "Activity/Sailing"), member("person", ROWAN), member("year", "2021")]),
             sailing & rowan & y2021),
            (search(any_of=[member("person", WREN), member("person", ROWAN)], none_of=[member("keyword", "Activity/Sailing")]),
             (wren | rowan) - sailing),
            (search(all_of=[member("keyword", "Activity/Sailing")], any_of=[member("person", ELODIE), member("person", WREN)],
                    none_of=[member("folder", self.misc)]), sailing & (self.of("person", ELODIE) | wren) - self.of("folder", self.misc)),
            # Three family members and not a fourth: the owner's own example.
            (search(all_of=[member("person", WREN)], none_of=[member("person", ROWAN)]), wren - rowan),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                found = self.found(value)
                self.assertEqual(expected, set(found["ids"]))
                self.assertEqual(len(expected), found["total"])
                self.assertEqual(len(found["ids"]), len(set(found["ids"])), "a photo once")
                self.assertEqual("search", found["source"]["kind"])

    def test_none_of_alone_is_the_whole_library_less_and_keeps_the_undated(self):
        # NOT of a month's comparison is NULL for a photo with no date: it must be kept, not dropped with the month.
        found = self.found(search(none_of=[member("month", "2020-07"), member("year", "2022")]))
        expected = self.everything() - self.of("month", "2020-07") - self.of("year", "2022")
        self.assertEqual(expected, set(found["ids"]))
        self.assertTrue({self.p[5], self.p[6], self.p[7]} <= set(found["ids"]), "the undated photos are in none of them")
        self.assertEqual(self.everything() - self.of("year_other", "2021"), set(self.found(search(none_of=[member("year_other", "2021")]))["ids"]))
        self.assertEqual([], self.found(search(none_of=[member("all")]))["ids"])
        self.assertEqual(self.everything(), set(self.found(search(none_of=[member("keyword", "Nowhere/At all")]))["ids"]),
                         "a member that names nothing takes nothing away")

    def test_within_the_navigators_selection_is_a_union_in_all_of(self):
        julys = member("any_of", [member("month", "%d-07" % year) for year in range(2015, 2027)])
        found = self.found(search(all_of=[julys, member("keyword", "Activity/Sailing")]))
        julys_set = set(self.ids("any_of", json.dumps(julys["value"]))["ids"])
        self.assertEqual(julys_set & self.of("keyword", "Activity/Sailing"), set(found["ids"]))
        self.assertEqual({self.p[1], self.p[3]}, set(found["ids"]))
        self.assertEqual("any_of", found["source"]["value"]["all_of"][0]["kind"], "the union stays one member")
        # A union in any_of or none_of is its members.
        spread = self.found(search(any_of=[julys], none_of=[member("any_of", [member("person", ROWAN), member("person", ELODIE)])]))
        self.assertEqual(julys_set - self.of("person", ROWAN) - self.of("person", ELODIE), set(spread["ids"]))
        self.assertEqual(12, len(spread["source"]["value"]["any_of"]))

    def test_a_search_inside_a_thousand_member_union(self):
        months = [member("month", "%04d-%02d" % (2000 + n // 12, n % 12 + 1)) for n in range(library_view.MAX_MEMBERS)]
        found = self.found(search(all_of=[member("any_of", months), member("person", WREN)],
                                  any_of=months[:600], none_of=[member("keyword", "Trips/Coast/Harbour")] + months[700:]))
        dated = set().union(*[self.of("month", each["value"]) for each in months[:600]])
        self.assertEqual(dated & self.of("person", WREN) - self.of("keyword", "Trips/Coast/Harbour"), set(found["ids"]))
        self.assertTrue(found["ids"])

    def test_a_folder_with_its_subfolders_and_alone_and_a_keyword_alone(self):
        found = self.found(search(all_of=[member("folder", self.folder, True), member("keyword_only", "Trips/Lakes")]))
        self.assertEqual(self.of("folder", self.folder, True) & self.of("keyword_only", "Trips/Lakes"), set(found["ids"]))
        alone = self.found(search(all_of=[member("folder", self.folder), member("keyword", "Activity/Sailing")]))
        self.assertEqual({self.p[1], self.p[2]}, set(alone["ids"]), "not Later under it")

    def test_a_member_that_names_nothing(self):
        # A keyword with no node of the tree (a photo may carry it: it has no row in photo_tags) and a person nobody is.
        loose = self.vl.photo("Misc", "loose.jpg", taken="2019:01:01 10:00:00", tags=["Unfiled/Odd one", "Activity/Sailing"])
        self.assertEqual([], self.found(search(all_of=[member("keyword", "Unfiled/Odd one"), member("keyword", "Activity/Sailing")]))["ids"])
        self.assertEqual([], self.found(search(any_of=[member("person", "Nobody Atall")], none_of=[member("year", "1999")]))["ids"])
        everything = self.found(search(none_of=[member("keyword", "Unfiled/Odd one")]))
        self.assertIn(loose, everything["ids"])
        self.assertEqual(self.everything(), set(everything["ids"]))

    def test_a_tag_renamed_in_the_tree_after_indexing_is_read_as_the_keyword_source_reads_it(self):
        conn = db.connect(self.vl.path)
        try:
            with people.tree_edit(conn):
                taxonomy.move_branch(conn, "Trips/Lakes", "Trips/Ponds")
            conn.commit()
        finally:
            conn.close()
        for tag in ("Trips/Lakes", "Trips/Ponds", "Trips"):
            with self.subTest(tag=tag):
                self.assertEqual(self.of("keyword", tag),
                                 set(self.found(search(all_of=[member("keyword", tag)], none_of=[member("year", "1999")]))["ids"]))
        # Until the files are rewritten the photos' keyword names no node: under neither name, as the navigator shows it.
        self.assertEqual(set(), self.of("keyword", "Trips/Ponds"))
        # A photo indexed with the new keyword is found by it.
        ponds = self.vl.photo("Misc", "ponds.jpg", tags=["Trips/Ponds"])
        self.assertEqual({ponds}, set(self.found(search(all_of=[member("keyword", "Trips/Ponds")], none_of=[member("year", "1999")]))["ids"]))

    def test_a_person_on_a_branch_tag_is_read_by_name_as_the_person_source(self):
        # People/Rowan Thackeray given a node under it: a branch, never a person (owner, #660), so stage 1 gives no id;
        # the views read people by name, and a search by the same rule as the person row of the navigator.
        self.vl.tree("People/%s/Sailing club" % ROWAN, face_root="People")
        listed = {row[0] for row in self.vl.rows("SELECT photo_id FROM photo_people WHERE name = ?", ROWAN)}
        self.assertTrue(listed, "the photos still list the name")
        found = self.found(search(all_of=[member("person", ROWAN)], none_of=[member("year", "1999")]))
        self.assertEqual(self.of("person", ROWAN), set(found["ids"]))
        self.assertEqual(listed, set(found["ids"]))
        self.assertEqual(self.of("person", ROWAN.upper()), set(found["ids"]), "a name in any case")

    def test_a_name_past_ascii(self):
        found = self.found(search(all_of=[member("person", ELODIE.lower())], none_of=[member("year", "1999")]))
        self.assertEqual({self.p[3]}, set(found["ids"]))


class TheShapeOfASearch(Library):
    def test_a_search_that_says_no_more_than_a_source_is_that_source(self):
        self.assertEqual({"kind": "all", "value": None, "recursive": False}, self.found(search())["source"])
        self.assertEqual({"kind": "all", "value": None, "recursive": False}, self.found(search(words="   "))["source"])
        one = self.found(search(all_of=[member("person", WREN)]))
        self.assertEqual({"kind": "person", "value": WREN, "recursive": False}, one["source"])
        union = self.found(search(any_of=[member("person", WREN), member("person", ROWAN), member("person", WREN)]))
        self.assertEqual(("any_of", 2), (union["source"]["kind"], len(union["source"]["value"])))
        self.assertEqual(self.of("person", WREN) | self.of("person", ROWAN), set(union["ids"]))

    def test_the_value_is_json_text_or_an_object_and_says_itself_back(self):
        value = search(all_of=[member("keyword", "Trips/Coast"), member("person", WREN)], none_of=[member("year", "2021")], text=False)
        as_object = library_view.ids(self.vl.library, "search", value)
        as_text = library_view.ids(self.vl.library, "search", json.dumps(value))
        self.assertEqual(as_object, as_text)
        said = as_object["source"]
        self.assertEqual({"kind": "search", "recursive": False, "value": {
            "all_of": [member("keyword", "Trips/Coast"), member("person", WREN)], "any_of": [],
            "none_of": [member("year", 2021)], "words": ""}}, said)
        again = library_view.ids(self.vl.library, said["kind"], said["value"])
        self.assertEqual(as_object["ids"], again["ids"], "what a reply says is a search that asks the same")

    def test_what_cannot_be_a_search_is_refused_with_a_sentence(self):
        too_many = [member("month", "2020-%02d" % (n % 12 + 1), n > 12) for n in range(library_view.MAX_MEMBERS + 1)]
        bad = ("not json", "[]", json.dumps({"within": []}), json.dumps({"all_of": "x"}), json.dumps({"words": 5}),
               json.dumps({"words": "x" * (library_view.MAX_WORDS + 1)}), json.dumps({"all_of": ["month"]}),
               json.dumps({"all_of": [member("search", {})]}), json.dumps({"any_of": [member("month", "July")]}),
               json.dumps({"none_of": too_many}),
               json.dumps({"all_of": [member("any_of", [member("any_of", [])])]}))
        for value in bad:
            with self.subTest(value=value[:60]):
                with self.assertRaises(Refused):
                    self.found(value)
        with self.assertRaises(Refused):
            library_view.ids(self.vl.library, "any_of", json.dumps([member("search", {})]))

    def test_words_of_a_library_without_the_word_index_are_refused_with_a_sentence(self):
        # A library not brought up to migration 24 (opened to look, or held by another program).
        for table in ("search_words", "search_names"):
            self.vl.conn.execute("DROP TABLE %s" % table)
        self.vl.conn.commit()
        with self.assertRaises(Refused) as caught:
            self.found(search(words="harbour"))
        self.assertIn("word index", str(caught.exception))
        with self.assertRaises(store.NoWordIndex):
            self.found(search(all_of=[member("person", WREN)], words="harbour"))
        self.assertEqual(self.of("person", WREN), set(self.found(search(all_of=[member("person", WREN)], words=" - "))["ids"]),
                         "words that say nothing ask nothing of the index")


class TheOrders(Library):
    def test_every_order_pages_by_its_keyset_as_its_list(self):
        searches = (search(any_of=[member("person", WREN), member("folder", self.misc)], none_of=[member("keyword", "Trips/Lakes")]),
                    search(none_of=[member("month", "2021-07")]),
                    search(all_of=[member("any_of", [member("year", "2021"), member("folder", self.misc)]), member("keyword", "Activity")]))
        for order in store.ORDERS:
            for value in searches:
                with self.subTest(order=order, value=value):
                    listed = self.found(value, order)
                    token, paged = None, []
                    while True:
                        page = library_view.view(self.vl.library, "search", value, False, token, 2, order)
                        paged += page["ids"]
                        self.assertEqual(listed["total"], page["total"])
                        token = page["next"]
                        if token is None:
                            break
                    self.assertEqual(listed["ids"], paged)
                    self.assertEqual(listed["total"], len(paged))

    def test_the_order_is_the_sources_order(self):
        value = search(none_of=[member("folder", self.misc)])
        for order in store.ORDERS:
            with self.subTest(order=order):
                every = [photo_id for photo_id in self.ids("all", order=order)["ids"] if photo_id not in self.of("folder", self.misc)]
                self.assertEqual(every, self.found(value, order)["ids"])

    def test_by_name_a_search_holding_most_of_the_library_walks_the_name_index(self):
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            source = library_view.source_of(self.vl.library, "search", search(none_of=[member("person", ELODIE)]))
            statements, _ms = store.id_plans(conn, source, 1000, store.NAME)
        finally:
            conn.close()
        text = "\n".join("\n".join(lines) for _sql, lines in statements)
        self.assertIn(store.NAME_INDEX, text)
        self.assertNotIn("TEMP B-TREE", text)


class ASelectionOfASearch(Library):
    def test_select_all_less_some_resolves_tallies_and_is_the_list(self):
        named = {"kind": "search", "value": search(any_of=[member("person", WREN), member("person", ROWAN)],
                                                   none_of=[member("keyword", "Trips/Lakes")], text=False)}
        listed = self.found(json.dumps(named["value"]))["ids"]
        chosen = selection.read(self.vl.library, {"source": named, "excluded": [listed[0]]})
        self.assertEqual(listed[1:], list(selection.resolve(self.vl.library, chosen).ids))
        tallied = selection.tally(self.vl.library, chosen)
        self.assertEqual(len(listed) - 1, tallied["total"])
        self.assertNotIn("Trips/Lakes", [each["tag"] for each in tallied["tags"]])

    def test_two_libraries_answer_each_for_its_own(self):
        other = ViewLibrary(self, "lighthouse", home=self.vl.home)
        other.tree("People/" + WREN, face_root="People")
        theirs = other.photo("Elsewhere", "w.jpg", taken="2020:07:04 10:00:00", tags=["People/" + WREN])
        value = search(all_of=[member("person", WREN)], none_of=[member("folder", self.misc)])
        self.assertEqual([theirs], library_view.ids(other.library, "search", value)["ids"])
        self.assertEqual(self.of("person", WREN) - self.of("folder", self.misc), set(self.found(value)["ids"]),
                         "this library's own photos (ids are each library's: the same number is another photo)")


class ADeleteOfASearch(test_bulk_delete.Delete):
    def test_a_search_whose_photos_change_after_the_question_refuses_the_delete_691(self):
        body = {"source": {"kind": "search", "value": search(all_of=[member("folder", self.folder("2024 Coast"), True)],
                                                             none_of=[member("folder", self.folder("2023 Lakes"))], text=False)}}
        asked = self.check(body)
        self.assertEqual(30, asked["total"])
        # A photo of the 2024 Coast folder is indexed between the question and the start.
        self.vl.photo("2024 Coast", "late.jpg", taken="2024:07:01 10:00:00", real=True, size=(16, 16))
        refused = self.start(body, {"token": asked["token"]}, expect=409)
        self.assertIn("changed since you were asked", refused["error"])
        self.assertEqual([], self.sent, "nothing deleted")


class TheRoutes(unittest.TestCase):
    def test_a_search_by_get_and_by_post_and_a_refusal(self):
        app, home = web_client.app_for(self, "tagpup")
        vl = ViewLibrary(self, "library", home=home)
        vl.tree("Trips/Coast", "People/" + WREN, face_root="People")
        b = vl.photo("A", "b.jpg", taken="2020:07:01 10:00:00", tags=["Trips/Coast", "People/" + WREN])
        a = vl.photo("A", "a.jpg", taken="2021:07:01 10:00:00", tags=["Trips/Coast"])
        vl.photo("A", "c.jpg", taken="2021:08:01 10:00:00", tags=["People/" + WREN])
        client = app.test_client()
        value = search(all_of=[member("keyword", "Trips/Coast")], none_of=[member("month", "2019-01")])
        reply = client.get("/library/api/library/ids", query_string={"kind": "search", "value": value, "order": "name"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual(([a, b], 2), (reply.get_json()["ids"], reply.get_json()["total"]))
        posted = client.post("/library/api/library/view", json={"kind": "search", "value": json.loads(value), "limit": 1})
        self.assertEqual(200, posted.status_code, posted.get_data(as_text=True))
        self.assertEqual(([b], 2), (posted.get_json()["ids"], posted.get_json()["total"]))
        after = client.post("/library/api/library/view", json={"kind": "search", "value": json.loads(value), "limit": 1,
                                                                "after": posted.get_json()["next"]})
        self.assertEqual([a], after.get_json()["ids"])
        refused = client.post("/library/api/library/ids", json={"kind": "search", "value": {"within": []}})
        self.assertEqual(400, refused.status_code)
        self.assertIn("no part within", refused.get_json()["error"])
        words = client.post("/library/api/library/ids", json={"kind": "search", "value": {"words": "coast", "none_of": [
            member("month", "2021-07")]}})
        self.assertEqual(200, words.status_code, words.get_data(as_text=True))
        self.assertEqual([b], words.get_json()["ids"])


if __name__ == "__main__":
    unittest.main()

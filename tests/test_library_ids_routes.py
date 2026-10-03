"""The two routes a grid that jumps stands on (tagpup.web.tagpup_routes: /api/library/ids, /api/library/cards;
docs/SPEC_TAGPUP_GUI.md; phase 9b-2): the whole ordered id list of a source and the cards of any ids.

Flask's test client, no port, no thread, no sleep, in a home of its own. Photos are rows as the indexer records them;
fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from test_migrations import at_version  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import library_view  # noqa: E402
from tagpup.store import schema  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import libraries as web_libraries  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}


class Routes(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "People/Wren Halloway", face_root="People")
        self.a = self.vl.photo("2024 Coast", "a.jpg", taken="2024:06:01 10:00:00", tags=["Trips/Coast", "People/Wren Halloway"])
        self.b = self.vl.photo("2024 Coast", "b.jpg", taken="2024:06:02 10:00:00", tags=["Trips/Lakes"])
        self.c = self.vl.photo(os.path.join("2024 Coast", "Day 2"), "c.jpg", taken="2023:01:02 10:00:00")
        self.d = self.vl.photo("Misc", "d.jpg")
        self.e = self.vl.photo("Misc", "e.jpg")   # no date either: after d, by id
        self.folder = os.path.join(self.vl.pictures, "2024 Coast")

    def get(self, path, **kwargs):
        return self.client.get("/library" + path, **kwargs)

    def ids(self, **query):
        reply = self.get("/api/library/ids", query_string=query)
        return reply, reply.get_json()

    def cards(self, text):
        reply = self.get("/api/library/cards", query_string={"ids": text})
        return reply, reply.get_json()


class TheIds(Routes):
    def test_every_source_gives_the_order_the_view_pages_in(self):
        for query in (dict(kind="all"), dict(kind="folder", folder=self.folder), dict(kind="folder", folder=self.folder, recursive="1"),
                      dict(kind="keyword", value="Trips"), dict(kind="person", value="Wren Halloway"),
                      dict(kind="year", value="2024"), dict(kind="month", value="2024-06")):
            with self.subTest(query=query):
                paged, token = [], None
                while True:
                    page = self.get("/api/library/view", query_string=dict(query, limit=2, **({"after": token} if token else {}))).get_json()
                    paged += page["ids"]
                    token = page["next"]
                    if not token:
                        break
                reply, found = self.ids(**query)
                self.assertEqual(200, reply.status_code)
                self.assertEqual(paged, found["ids"])
                self.assertEqual((len(paged), True), (found["total"], found["complete"]))

    def test_all_is_dated_photos_by_taken_then_undated_by_id(self):
        self.assertEqual([self.c, self.a, self.b, self.d, self.e], self.ids(kind="all")[1]["ids"])

    def test_a_cap_cuts_the_list_and_says_so_with_the_real_total(self):
        with mock.patch.object(library_view, "MAX_IDS", 3):
            found = self.ids(kind="all")[1]
        self.assertEqual(([self.c, self.a, self.b], 5, False), (found["ids"], found["total"], found["complete"]))
        with mock.patch.object(library_view, "MAX_IDS", 4):
            self.assertEqual([self.c, self.a, self.b, self.d], self.ids(kind="all")[1]["ids"])
        with mock.patch.object(library_view, "MAX_IDS", 5):
            self.assertTrue(self.ids(kind="all")[1]["complete"])

    def test_a_source_with_nothing_is_an_empty_list_not_an_error(self):
        for query in (dict(kind="keyword", value="No/Such"), dict(kind="person", value="Nobody Atall"),
                      dict(kind="folder", folder=os.path.join(self.vl.pictures, "Nowhere")), dict(kind="year", value="1999")):
            with self.subTest(query=query):
                reply, found = self.ids(**query)
                self.assertEqual((200, [], 0, True), (reply.status_code, found["ids"], found["total"], found["complete"]))

    def test_a_request_that_means_nothing_is_a_400_with_a_sentence(self):
        for query in (dict(kind="nope"), dict(), dict(kind="folder"), dict(kind="year", value="x"), dict(kind="month", value="2024-13"),
                      dict(kind="keyword", value="  ")):
            with self.subTest(query=query):
                reply, found = self.ids(**query)
                self.assertEqual(400, reply.status_code)
                self.assertNotIn("Traceback", found["error"])

    def test_a_value_of_a_megabyte_is_refused_or_empty_never_an_error(self):
        reply, found = self.ids(kind="keyword", value="x" * 1_000_000)
        self.assertIn(reply.status_code, (200, 400, 414))
        if reply.status_code == 200:
            self.assertEqual([], found["ids"])

    def test_this_pc_only(self):
        self.assertEqual(403, self.get("/api/library/ids?kind=all", environ_overrides=REMOTE).status_code)
        self.assertEqual(403, self.get("/api/library/cards?ids=1", environ_overrides=REMOTE).status_code)

    def test_a_json_answer_is_never_kept_by_the_browser(self):
        self.assertIn("no-store", self.get("/api/library/ids?kind=all").headers["Cache-Control"])

    def test_it_reads_ids_only_never_a_card_or_a_blob(self):
        seen = []
        real = library_view.store.all_ids

        def spy(conn, source, cap):
            conn.set_trace_callback(seen.append)
            return real(conn, source, cap)
        with mock.patch.object(library_view.store, "all_ids", spy):
            self.ids(kind="all")
        self.assertTrue(seen)
        for sql in seen:
            self.assertNotIn("raw_metadata", sql)
            self.assertNotIn("faces", sql)


class TheCards(Routes):
    def test_cards_come_in_the_order_asked_and_are_the_views_cards(self):
        _, found = self.cards("%d,%d,%d" % (self.b, self.c, self.a))
        self.assertEqual([self.b, self.c, self.a], [card["id"] for card in found["cards"]])
        shown = {card["id"]: card for card in self.get("/api/library/view?kind=all").get_json()["cards"]}
        for card in found["cards"]:
            # The route's cards are the view's, and say besides (phase 9c) when the file is not as the row says
            # (tests/test_library_cards_stale.py): these rows have no files.
            self.assertEqual(shown[card["id"]], {key: value for key, value in card.items() if key != "stale"})

    def test_an_id_the_library_has_no_photo_of_is_absent_and_a_repeat_is_once(self):
        _, found = self.cards("%d,999999,%d,%d" % (self.a, self.b, self.a))
        self.assertEqual([self.a, self.b], [card["id"] for card in found["cards"]])

    def test_two_hundred_are_answered_and_two_hundred_and_one_are_refused(self):
        self.assertEqual(200, self.cards(",".join(str(n) for n in range(1, 201)))[0].status_code)
        reply, found = self.cards(",".join(str(n) for n in range(1, 202)))
        self.assertEqual(400, reply.status_code)
        self.assertIn("at most 200", found["error"])

    def test_what_is_not_a_list_of_whole_numbers_is_a_400_with_a_sentence(self):
        for text in ("", "abc", "1,,2", "1;2", "-1", "1.5", "1e3", "0x10", "9" * 40, "١٢"):
            with self.subTest(text=text):
                reply, found = self.cards(text)
                self.assertEqual(400, reply.status_code)
                self.assertNotIn("Traceback", found["error"])
        self.assertEqual(400, self.get("/api/library/cards").status_code)

    def test_a_huge_id_is_simply_absent(self):
        reply, found = self.cards("%d,%d" % (self.a, 10 ** 17))
        self.assertEqual((200, [self.a]), (reply.status_code, [card["id"] for card in found["cards"]]))

    def test_one_read_whatever_the_number(self):
        counts = []
        for n in (3, 5):
            seen = []
            real = library_view.store.card_rows

            def spy(conn, ids, seen=seen, real=real):
                conn.set_trace_callback(seen.append)
                return real(conn, ids)
            with mock.patch.object(library_view.store, "card_rows", spy):
                self.cards(",".join(str(n) for n in range(1, n + 1)))
            counts.append(len(seen))
        self.assertEqual(counts[0], counts[1])


class ThePhoto(Routes):
    def photo(self, wanted):
        reply = self.get("/api/library/photo", query_string={"id": wanted})
        return reply, reply.get_json()

    def test_it_is_the_record_a_scan_gives_with_the_id(self):
        reply, found = self.photo(self.a)
        self.assertEqual(200, reply.status_code)
        record = found["photo"]
        self.assertEqual(self.a, record["id"])
        self.assertEqual(self.vl.path_of(self.a), record["path"])
        self.assertEqual("a.jpg", record["filename"])
        self.assertEqual(["Trips/Coast", "People/Wren Halloway"], record["tags"])
        self.assertEqual(["Wren Halloway"], record["people"])
        self.assertEqual("2024:06:01 10:00:00", record["taken"])
        self.assertEqual("", record["title"])
        self.assertIn("raw_metadata", record)
        self.assertTrue(record["missing"], "a row whose file is not there: the row, flagged (the file-and-row-at-odds cases are tests/test_stale_record_precondition.py)")
        self.assertEqual({"path", "filename", "tags", "people", "title", "mtime", "size", "year", "taken", "raw_metadata", "id", "missing", "damaged", "damage"},
                         set(record))

    def test_an_undated_photo_has_no_taken(self):
        self.assertIsNone(self.photo(self.d)[1]["photo"]["taken"])

    def test_an_id_with_no_photo_is_a_404_in_a_sentence_and_a_bad_one_a_400(self):
        for wanted in (999999, 0, 2 ** 70):
            reply, found = self.photo(wanted)
            self.assertEqual(404, reply.status_code, wanted)
            self.assertIn("There is no photo", found["error"])
        for wanted in ("", "abc", "1.5", "-1", "1_0", "+5"):
            self.assertEqual(400, self.photo(wanted)[0].status_code, wanted)
        self.assertEqual(400, self.get("/api/library/photo").status_code)

    def test_this_pc_only_and_a_library_behind_is_a_sentence(self):
        self.assertEqual(403, self.get("/api/library/photo?id=1", environ_overrides=REMOTE).status_code)


class ALibraryThatIsNotReady(unittest.TestCase):
    def test_a_library_behind_is_a_sentence_for_both(self):
        home = own_home.for_test(self)
        path = home.library("behind.db")
        at_version(path, schema.LATEST - 1)
        app = web.create_app("tagpup", startup=Library(path))
        app.testing = True
        client = app.test_client()
        with mock.patch.object(web_libraries.library_actions, "bring_up_to_date", side_effect=RuntimeError("locked")):
            for url in ("/behind/api/library/ids?kind=all", "/behind/api/library/cards?ids=1", "/behind/api/library/photo?id=1"):
                reply = client.get(url)
                self.assertEqual(409, reply.status_code, url)
                self.assertIn("has not been brought up to date", reply.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

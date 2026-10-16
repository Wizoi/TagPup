"""Scenarios V1 and V8: the owner scrolls a library view, and the view is right again after a write made elsewhere.

V1: a library of photos with equal Date Taken is scrolled through the real routes the page uses (/api/library/ids, then
/api/library/cards for the next window of ids; the page's cursor is its place in the id list): no photo twice, none
skipped, the equal dates in id order in the order's direction, the undated after the dated, whichever source and
direction, with a reload in the middle, beside a second library, and at the cap.
V8: a view's cards, its ids and the page's cached scan are read again after the index, a rename, a bulk tag or the CLI
wrote the library: the card says what the disk says, the keyword view lists what the write tagged, the folder scan is
let go. The find route reads a path literally: `_` and `%` are not wildcards.

Lifted from tests/test_library_view_routes.py, test_library_ids_routes.py, test_library_cards_stale.py,
test_library_find.py and test_a_queued_folder_drops_its_scan.py (#341), and what each catches:
  Lifted: the id tie-break dropped from the dated read of all_ids in tagpup/store/library_view.py (`ORDER BY p.taken%s,
          p.id%s` -> `ORDER BY p.taken%s`) fails scrolls_equal_dates_across_page_boundaries (descending, by keyword and by
          folder with its subfolders).
  Lifted: the undated read dropped from all_ids (`if len(ids) <= cap and not scope.dated:` -> `if False:`) fails the same test.
  Lifted: store.photo_id_of matching with `path LIKE ?` instead of paths.sql_equals (`_` and `%` wildcards) fails
          underscore_and_percent_are_not_wildcards.
  Lifted: #341, the page went on showing a scan from before the photos were read: the scan is kept with the photos
          counter (tagpup.web.tagpup_routes.FolderCache), which the indexer's rows move; scan_stamp reading no
          counter fails a_folder_the_queue_indexed_has_no_cached_scan_whoever_queued_it.

Photos are rows as the indexer records them (tests/view_library.py); fictional names only.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import runtime  # noqa: E402
from tagpup.services import indexing, library_view  # noqa: E402
from tagpup.store import photos  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}
SAME = "2024:06:01 10:00:00"
WINDOW = 5   # the cards a page asks for at a time (the real window is up to library_view.MAX_CARDS)


class Views(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast")

    def get(self, path, library="library", **kwargs):
        return self.client.get("/%s%s" % (library, path), **kwargs)

    def ids(self, library="library", **query):
        reply = self.get("/api/library/ids", library, query_string=query)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def cards(self, ids, library="library"):
        reply = self.get("/api/library/cards", library, query_string={"ids": ",".join(str(each) for each in ids)})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["cards"]

    def scroll(self, library="library", start=0, **query):
        """What the page shows scrolling a view from `start`: the id list, then the cards window by window."""
        listed = self.ids(library, **query)["ids"]
        shown = []
        for at in range(start, len(listed), WINDOW):
            shown += [card["id"] for card in self.cards(listed[at:at + WINDOW], library)]
        return listed, shown


class ScrollingALibraryOfEqualDates(Views):
    def setUp(self):
        super().setUp()
        photo = self.vl.photo
        # Seven photos taken in the same second, added folder by folder in turn, so that id order is not folder order.
        self.same = [photo("Lake", "l1.jpg", SAME), photo("Coast", "c1.jpg", SAME), photo("Misc", "m1.jpg", SAME),
                     photo("Coast", "c2.jpg", SAME, tags=["Trips/Coast"]), photo(os.path.join("Coast", "Day 2"), "c3.jpg", SAME),
                     photo("Lake", "l2.jpg", SAME, tags=["Trips/Coast"]), photo("Misc", "m2.jpg", SAME, tags=["Trips/Coast"])]
        self.early = photo("Misc", "early.jpg", "2023:01:02 10:00:00")
        self.late = photo("Lake", "late.jpg", "2024:12:24 10:00:00")
        self.undated = [photo("Misc", "u1.jpg"), self.vl.unread("Misc", "never read.jpg"), photo("Lake", "u2.jpg")]
        self.coast = os.path.join(self.vl.pictures, "Coast")

    def test_scrolls_equal_dates_across_page_boundaries(self):
        dated = [self.early] + self.same + [self.late]
        sources = (dict(kind="all"), dict(kind="keyword", value="Trips"), dict(kind="year", value="2024"),
                   dict(kind="folder", folder=self.coast, recursive="1"))
        for query in sources:
            for order, direction in (("taken", 1), ("taken-desc", -1)):
                with self.subTest(query=query, order=order):
                    listed, shown = self.scroll(order=order, **query)
                    self.assertEqual(listed, shown, "the cards are the ids, in order, none twice, none skipped")
                    self.assertEqual(len(listed), len(set(listed)))
                    self.assertEqual(self.ids(order=order, **query)["total"], len(listed))
                    equal = [each for each in listed if each in self.same]
                    self.assertEqual(sorted(equal, reverse=direction < 0), equal, "equal dates in id order, the order's way")
                    held = [each for each in listed if each in dated]
                    self.assertEqual(sorted(held, key=lambda each: (self.taken(each), each), reverse=direction < 0), held)
        everything = self.ids(kind="all")["ids"]
        self.assertEqual(self.undated, everything[-3:], "the undated after the dated, by id")
        self.assertEqual(sorted(self.undated, reverse=True), self.ids(kind="all", order="taken-desc")["ids"][-3:])
        self.assertEqual(12, len(everything))

    def taken(self, photo_id):
        return self.vl.rows("SELECT taken FROM photos WHERE id = ?", photo_id)[0][0]

    def test_a_page_reloaded_in_the_middle_of_the_scroll_goes_on_where_it_was(self):
        listed, shown = self.scroll(kind="all")
        self.assertEqual(listed, shown)
        # The owner scrolled one window, the index added a photo of the same second, and the page was reloaded.
        first = self.cards(listed[:WINDOW])
        arrived = self.vl.photo("Misc", "arrived.jpg", SAME)
        again, rest = self.scroll(start=WINDOW, kind="all")
        self.assertEqual(set(listed) | {arrived}, set(again))
        self.assertEqual(len(again), len(set(again)))
        self.assertEqual(again[WINDOW:], rest)
        self.assertEqual(listed[:WINDOW], [card["id"] for card in first])
        self.assertEqual(again[:WINDOW], listed[:WINDOW], "the part already seen has not moved")

    def test_a_second_library_scrolls_on_its_own_and_the_cap_says_how_many_there_are(self):
        other = ViewLibrary(self, "meadow", home=self.home)
        mine = other.photo("Coast", "c1.jpg", SAME)
        self.assertIn(mine, self.same + [self.early], "the two libraries number their photos alike")
        listed, shown = self.scroll("meadow", kind="all")
        self.assertEqual(([mine], [mine]), (listed, shown))
        self.assertTrue(self.cards([mine], "meadow")[0]["path"].startswith(other.pictures))
        self.assertTrue(self.cards([mine])[0]["path"].startswith(self.vl.pictures))
        with mock.patch.object(library_view, "MAX_IDS", 4):
            found = self.ids(kind="all")
        self.assertEqual((4, 12, False), (len(found["ids"]), found["total"], found["complete"]))
        self.assertEqual(self.ids(kind="all")["ids"][:4], found["ids"], "a cut list is the head of the whole")

    def test_what_means_nothing_is_a_sentence_and_the_window_is_two_hundred(self):
        for query in (dict(kind="nope"), dict(), dict(kind="folder"), dict(kind="year", value="x"),
                      dict(kind="month", value="2024-13")):
            with self.subTest(query=query):
                reply = self.get("/api/library/ids", query_string=query)
                self.assertEqual(400, reply.status_code)
                self.assertNotIn("Traceback", reply.get_json()["error"])
        for text in ("", "abc", "1,,2", "-1", "1.5", "9" * 40):
            self.assertEqual(400, self.get("/api/library/cards", query_string={"ids": text}).status_code, text)
        self.assertEqual(200, self.get("/api/library/cards", query_string={"ids": ",".join(map(str, range(1, 201)))}).status_code)
        self.assertEqual(400, self.get("/api/library/cards", query_string={"ids": ",".join(map(str, range(1, 202)))}).status_code)
        for section in ("nope", ""):
            reply = self.get("/api/library/navigator", query_string={"section": section})
            self.assertEqual(400, reply.status_code)
            self.assertIn("section must be one of", reply.get_json()["error"])
        for path in ("/api/library/ids?kind=all", "/api/library/cards?ids=1", "/api/library/find?path=x",
                     "/api/library/navigator?section=dates", "/api/library/photo?id=1"):
            self.assertEqual(403, self.get(path, environ_overrides=REMOTE).status_code, path)
        self.assertEqual(([], 0), tuple(self.ids(kind="keyword", value="No/Such/Tag")[key] for key in ("ids", "total")))
        for tag in ("Trips/%", "Trips/_oast", "Trips/O'Brien"):
            self.assertEqual([], self.ids(kind="keyword", value=tag)["ids"], tag)


class FindingAPathLiterally(Views):
    def test_underscore_and_percent_are_not_wildcards(self):
        a = self.vl.photo("Coast", "IMG_0001.jpg", SAME)
        b = self.vl.photo("Coast", "IMGX0001.jpg", SAME)
        c = self.vl.photo("Coast", "100%.jpg", SAME)
        d = self.vl.photo("Coast", "100x.jpg", SAME)
        for wanted, name in ((a, "IMG_0001.jpg"), (b, "IMGX0001.jpg"), (c, "100%.jpg"), (d, "100x.jpg")):
            reply = self.get("/api/library/find", query_string={"path": os.path.join(self.vl.pictures, "Coast", name)})
            self.assertEqual(wanted, reply.get_json()["id"], name)
        for wild in ("IMG_0001", "IMG%", "%.jpg", "100_.jpg"):
            reply = self.get("/api/library/find", query_string={"path": os.path.join(self.vl.pictures, "Coast", wild)})
            self.assertEqual((200, None), (reply.status_code, reply.get_json()["id"]), wild)
        if os.name == "nt":
            spelled = self.get("/api/library/find", query_string={"path": os.path.join(self.vl.pictures, "COAST", "img_0001.JPG")})
            self.assertEqual(a, spelled.get_json()["id"], "a spelling the filesystem treats as the same")
        self.assertEqual(400, self.get("/api/library/find").status_code)


class AfterAWriteElsewhere(Views):
    def setUp(self):
        super().setUp()
        self.a = self.vl.photo("Coast", "a.jpg", "2024:06:01 10:00:00", real=True)
        self.b = self.vl.photo("Coast", "b.jpg", "2024:06:02 10:00:00", real=True)
        self.c = self.vl.photo("Coast", "c.jpg", "2024:06:03 10:00:00", real=True)
        self.folder = os.path.join(self.vl.pictures, "Coast")

    def marks(self):
        return {card["id"]: card.get("stale") for card in self.cards([self.a, self.b, self.c])}

    def test_a_card_says_what_the_disk_says_and_the_index_makes_it_true_again(self):
        self.assertEqual({self.a: None, self.b: None, self.c: None}, self.marks())
        make_jpeg(self.vl.path_of(self.b), shade=(1, 2, 3), size=(1000, 700))   # another program saved it
        os.remove(self.vl.path_of(self.c))
        later = time.time() + 3600
        os.utime(self.vl.path_of(self.a), (later, later))                        # the same size, another time
        self.assertEqual({self.a: "changed", self.b: "changed", self.c: "missing"}, self.marks())
        for photo_id in (self.a, self.b):                                         # the index reads them again
            photo_rows.add_read(self.vl.conn, self.vl.path_of(photo_id), {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"})
        self.vl.conn.commit()
        self.assertEqual({self.a: None, self.b: None, self.c: "missing"}, self.marks())

    def test_a_share_that_did_not_answer_or_a_file_that_cannot_be_read_gives_no_mark(self):
        from tagpup.files import shares
        os.remove(self.vl.path_of(self.c))
        for answer in (shares.UNANSWERED, shares.CANNOT_READ):
            with mock.patch.object(shares, "stamp_of", return_value=answer):
                self.assertEqual({self.a: None, self.b: None, self.c: None}, self.marks())
        read = self.vl.unread("Coast", "never read.jpg")
        make_jpeg(self.vl.path_of(read))
        self.assertIsNone({card["id"]: card.get("stale") for card in self.cards([read])}[read], "a row nobody stamped")
        for card in library_view.cards(self.vl.library, [self.a, self.b]):
            self.assertNotIn("stale", card, "a card read without the disk look has no mark at all")

    def test_a_rename_a_bulk_tag_and_the_cli_are_in_the_next_read(self):
        old = self.vl.path_of(self.a)
        new = os.path.join(self.folder, "renamed.jpg")
        os.replace(old, new)
        photos.move_rows(self.vl.path, {old: new})                                 # the rename's row follows the file
        card = self.cards([self.a])[0]
        self.assertEqual(("renamed.jpg", self.a), (card["name"], card["id"]), "the same photo under its new name")
        self.assertEqual((self.a, None), (self.get("/api/library/find", query_string={"path": new}).get_json()["id"],
                                          self.get("/api/library/find", query_string={"path": old}).get_json()["id"]))
        self.assertEqual([], self.ids(kind="keyword", value="Trips/Coast")["ids"])
        photos.record_tags(self.vl.path, new, ["Trips/Coast"])                     # a bulk tag, or the CLI, in another connection
        self.assertEqual([self.a], self.ids(kind="keyword", value="Trips/Coast")["ids"])
        record = self.get("/api/library/photo", query_string={"id": self.a}).get_json()["photo"]   # the details panel
        self.assertEqual((new, ["Trips/Coast"]), (record["path"], record["tags"]))
        missing = self.get("/api/library/photo", query_string={"id": 999999})
        self.assertEqual(404, missing.status_code)
        self.assertIn("There is no photo", missing.get_json()["error"])
        self.assertEqual([self.a, self.b, self.c], self.ids(kind="all")["ids"])

    def test_a_folder_the_queue_indexed_has_no_cached_scan_whoever_queued_it(self):
        """#341: only the server's own route dropped the folder's scan when indexing ended; the folders sync, a recurring sync
        and "include" went through runtime.index_folder, which did not, and the page showed the folder as scanned before. The
        indexer is a process of its own: what it wrote reaches the scan through the photos counter, even when clustering
        failed after the rows were written."""
        library = self.vl.library
        self.addCleanup(tagpup_routes.folders.forget, library)
        for failing in (None, RuntimeError("clustering failed")):
            def index(*_args, **_kwargs):
                # The indexer read the folder's photos again: its rows, from a connection of its own.
                photo_rows.add_read(self.vl.conn, self.vl.path_of(self.a), {"EXIF:DateTimeOriginal": "2024:06:01 11:00:00"})
                self.vl.conn.commit()
                if failing:
                    raise failing
            with mock.patch.object(indexing, "index_folder", side_effect=index):
                for make in (runtime.index_folder(library, subfolders=False), tagpup_routes._folder_indexer(library)):
                    tagpup_routes.folders.of(library).put(self.folder, {"a": {"path": "a"}})
                    try:
                        make(self.folder, False, None)
                    except RuntimeError:
                        pass
                    self.assertIsNone(tagpup_routes.folders.of(library).get(self.folder), failing)


if __name__ == "__main__":
    unittest.main()

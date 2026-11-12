"""Scenarios V1 and V8: the owner scrolls a library view, and the view is right again after a write made elsewhere.

V1: a library of photos with equal Date Taken is scrolled through the real routes the page uses (/api/library/ids, then
/api/library/cards for the next window of ids; the page's cursor is its place in the id list): no photo twice, none
skipped, the equal dates in id order in the order's direction, the undated after the dated, whichever source and
direction, with a reload in the middle, beside a second library, and at the cap. A clip among the photos of a folder is
one more photo of the view: listed once in Date Taken order by a clip's own date rule (its XMP date first, then its
QuickTime ones, a date of zeros none), carded as a video, counted under
its folder and its month by the navigator, found by its file name, and answered the placeholder thumbnail, not kept.
Every view says how many of its photos are clips (#2569): `videos` beside `total`, the same where in the same read, a clip in
two members of a union once, a view of clips alone, an empty one, and a cut list's clips the whole source's.
The owner looks for the biggest clips (2026-11-12): Videos and File size, largest first, lists the clips by their files' sizes,
equal sizes by id in the order's direction, scrolled window by window with none twice or skipped, a cut list the head of the
whole, and a row whose file was never read (Suggest's) after the rest either way.
V8: a view's cards, its ids and the page's cached scan are read again after the index, a rename, a bulk tag or the CLI
wrote the library: the card says what the disk says, the keyword view lists what the write tagged, the folder scan is
let go. The find route reads a path literally: `_` and `%` are not wildcards.

Lifted from tests/test_library_view_routes.py, test_library_ids_routes.py, test_library_cards_stale.py,
test_library_find.py, test_a_queued_folder_drops_its_scan.py (#341) and test_library_view.py (EqualDates: a folder alone, a
person and a month among the sources; TheOrder's dated-then-undated order), and what each catches:
  Lifted: the id tie-break dropped from the dated read of all_ids in tagpup/store/library_view.py (`ORDER BY p.taken%s,
          p.id%s` -> `ORDER BY p.taken%s`) fails scrolls_equal_dates_across_page_boundaries (descending, by keyword and by
          folder with its subfolders).
  Lifted: the undated read dropped from all_ids (`if len(ids) <= cap and not scope.dated:` -> `if False:`) fails the same test.
  New: on the code before the size order every TheLargestFilesFirst test fails, `order=size` answered 400.
  Lifted: store.photo_id_of matching with `path LIKE ?` instead of paths.sql_equals (`_` and `%` wildcards) fails
          underscore_and_percent_are_not_wildcards.
  Lifted: #341, the page went on showing a scan from before the photos were read: the scan is kept with the photos
          counter (tagpup.web.tagpup_routes.FolderCache), which the indexer's rows move; scan_stamp reading no
          counter fails a_folder_the_queue_indexed_has_no_cached_scan_whoever_queued_it.

Photos are rows as the indexer records them (tests/view_library.py); fictional names only.
"""
import datetime
import json
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
from tagpup.web import tagpup_routes  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}
SAME = "2024:06:01 10:00:00"
WREN = "Wren Halloway"
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
        self.vl.tree("People/" + WREN, face_root="People")
        self.same = [photo("Lake", "l1.jpg", SAME, tags=["People/" + WREN]), photo("Coast", "c1.jpg", SAME),
                     photo("Misc", "m1.jpg", SAME),
                     photo("Coast", "c2.jpg", SAME, tags=["Trips/Coast", "People/" + WREN]),
                     photo(os.path.join("Coast", "Day 2"), "c3.jpg", SAME),
                     photo("Lake", "l2.jpg", SAME, tags=["Trips/Coast"]), photo("Misc", "m2.jpg", SAME, tags=["Trips/Coast", "People/" + WREN])]
        self.early = photo("Misc", "early.jpg", "2023:01:02 10:00:00")
        self.late = photo("Lake", "late.jpg", "2024:12:24 10:00:00")
        self.undated = [photo("Misc", "u1.jpg"), self.vl.unread("Misc", "never read.jpg"), photo("Lake", "u2.jpg")]
        self.coast = os.path.join(self.vl.pictures, "Coast")

    def test_scrolls_equal_dates_across_page_boundaries(self):
        dated = [self.early] + self.same + [self.late]
        sources = (dict(kind="all"), dict(kind="keyword", value="Trips"), dict(kind="year", value="2024"),
                   dict(kind="month", value="2024-06"), dict(kind="person", value="People/" + WREN),
                   dict(kind="folder", folder=self.coast), dict(kind="folder", folder=self.coast, recursive="1"))
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


class TheLargestFilesFirst(Views):
    def file(self, folder, name, size, tags=()):
        """A file of `size` bytes at `folder`/`name` under Pictures, recorded as the indexer records its read."""
        path = os.path.join(self.vl.pictures, folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(bytes(size))
        return self.vl.photo(folder, name, tags=tags)

    def setUp(self):
        super().setUp()
        file = self.file
        self.mid_clip = file("Coast", "swim.mp4", 5000, tags=["Trips/Coast"])
        self.small_photo = file("Coast", "dawn.jpg", 1000, tags=["Trips/Coast"])
        self.big_clip = file("Lake", "regatta.mov", 9000)
        self.big_photo = file("Coast", "pier.jpg", 7000)
        self.twin_clip = file(os.path.join("Coast", "Day 2"), "ferry.mp4", 9000, tags=["Trips/Coast"])   # as big as the regatta
        self.small_clip = file("Lake", "reel.mp4", 2000)
        self.mid_photo = file("Lake", "jetty.jpg", 3000, tags=["Trips/Coast"])
        self.never_read = self.vl.unread("Coast", "never read.jpg")   # the row Suggest makes: no size
        self.size = dict(self.vl.rows("SELECT id, size FROM photos"))
        self.coast = os.path.join(self.vl.pictures, "Coast")

    def ordered(self, held, descending):
        """`held` by size and then id, the order's way, a size never read after the rest."""
        sized = sorted((each for each in held if self.size[each] is not None), key=lambda each: (self.size[each], each),
                       reverse=descending)
        return sized + sorted((each for each in held if self.size[each] is None), reverse=descending)

    def test_videos_largest_first_are_the_biggest_clips_first_in_every_view_across_the_pages(self):
        self.assertEqual(None, self.size[self.never_read])
        listed, shown = self.scroll(kind="all", media="video", order="size-desc")
        self.assertEqual([self.twin_clip, self.big_clip, self.mid_clip, self.small_clip], listed,
                         "the biggest clip first; two as big, the later first")
        self.assertEqual(listed, shown)
        sources = (dict(kind="all"), dict(kind="folder", folder=self.coast, recursive="1"), dict(kind="folder", folder=self.coast),
                   dict(kind="keyword", value="Trips"))
        for query in sources:
            for media in ("", "video", "photo"):
                held = set(self.ids(media=media, **query)["ids"])
                for order, descending in (("size", False), ("size-desc", True)):
                    with self.subTest(query=query, media=media, order=order):
                        listed, shown = self.scroll(media=media, order=order, **query)
                        self.assertEqual(listed, shown, "the cards are the ids, in order, none twice, none skipped")
                        self.assertEqual(self.ordered(held, descending), listed)
        everything = self.ids(kind="all", order="size")["ids"]
        self.assertEqual(([self.small_photo, self.small_clip], self.never_read), (everything[:2], everything[-1]))
        self.assertEqual(self.never_read, self.ids(kind="all", order="size-desc")["ids"][-1], "never read: last either way")

    def test_a_cut_list_is_the_head_of_the_largest_and_a_file_that_grew_moves_in_the_next_read(self):
        whole = self.ids(kind="all", order="size-desc")["ids"]
        with mock.patch.object(library_view, "MAX_IDS", 3):
            cut = self.ids(kind="all", order="size-desc")
        self.assertEqual((whole[:3], 8, False), (cut["ids"], cut["total"], cut["complete"]))
        # The small clip is rewritten bigger than every other file and read again by the index.
        self.file("Lake", "reel.mp4", 12000)
        self.assertEqual(self.small_clip, self.ids(kind="all", media="video", order="size-desc")["ids"][0])

    def test_an_order_by_size_is_named_in_the_reply_and_another_spelling_is_a_sentence(self):
        self.assertEqual("size-desc", self.ids(kind="all", order="size-desc")["order"])
        for order in ("largest", "size-asc", "Size"):
            with self.subTest(order=order):
                reply = self.get("/api/library/ids", query_string={"kind": "all", "order": order})
                self.assertEqual(400, reply.status_code)
                self.assertIn("size, size-desc", reply.get_json()["error"])


class AClipAmongThePhotosOfAFolder(Views):
    def test_a_clip_is_listed_carded_counted_found_and_thumbnailed_with_the_photos_beside_it(self):
        # The clip was taken at ten, local time; QuickTime keeps its CreateDate in UTC, which a clip's rule reads as
        # local time (tagpup.core.dates.CLIP_DATE_FIELDS), and ExifTool gives its bare CreateDate, the same copy, which is
        # never read as a photo's. Read as given, it would fall outside nine to eleven here wherever the machine is more
        # than an hour from UTC.
        ten = datetime.datetime(2024, 6, 1, 10, 0, 0).astimezone()
        utc = ten.astimezone(datetime.timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
        before = self.vl.photo("Coast", "before.jpg", "2024:06:01 09:00:00")
        clip = self.vl.photo("Coast", "Harbour Swim.mov", fields={"QuickTime:CreateDate": utc, "CreateDate": utc})
        after = self.vl.photo("Coast", "after.jpg", "2024:06:01 11:00:00")
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", clip))
        coast = os.path.join(self.vl.pictures, "Coast")
        for query in (dict(kind="all"), dict(kind="folder", folder=coast), dict(kind="month", value="2024-06")):
            for order, shown_order in (("taken", [before, clip, after]), ("taken-desc", [after, clip, before])):
                with self.subTest(query=query, order=order):
                    listed, shown = self.scroll(order=order, **query)
                    self.assertEqual(shown_order, listed, "once each, the clip by its own date")
                    self.assertEqual(listed, shown)
        media = {card["id"]: card.get("media") for card in self.cards([before, clip, after])}
        self.assertEqual({before: "photo", clip: "video", after: "photo"}, media)

        folders = self.get("/api/library/navigator", query_string={"section": "folders"}).get_json()["folders"]
        held = [entry["direct"] for entry in folders if entry["name"] == "Coast"]
        self.assertEqual([3], held, "the navigator counts the clip under its folder")
        years = self.get("/api/library/navigator", query_string={"section": "dates"}).get_json()["years"]
        june = [month["count"] for year in years if str(year["year"]) == "2024"
                for month in year["months"] if str(month["month"]).endswith("6")]
        self.assertEqual([3], june, "and under the month it was taken")

        found = self.ids(kind="search", value=json.dumps({"words": "Swim"}))["ids"]
        self.assertEqual([clip], found, "a search by its file name finds it")

        # Its card asks its poster frame by id (a real clip's: tests/test_a_clip_shows_its_poster.py). This one's bytes
        # are only what a .mov starts with, as a copy that failed leaves: ffmpeg is asked and gives no picture.
        os.makedirs(coast, exist_ok=True)
        with open(self.vl.path_of(clip), "wb") as handle:
            handle.write(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 4096)
        reply = self.get("/api/photo-thumb", query_string={"id": clip})
        self.assertEqual((200, "unreadable"), (reply.status_code, reply.headers["X-TagPup-Thumb"]))
        self.assertIn("no-cache", reply.headers["Cache-Control"], "a placeholder is not kept")

    def test_a_clip_is_ordered_by_its_xmp_date_first_and_one_holding_zeros_by_its_xmp_date_alone(self):
        # A clip's rule (tagpup.core.dates, DECISIONS 2026-11-08, A5 (D) REVISED): its XMP date first -- a Date Taken
        # saved, or the xmp:CreateDate Adobe's tools wrote --, then its QuickTime ones; a camera's date of zeros is no
        # date, its bare CreateDate (QuickTime's copy) never a photo's. A photo's dates are read as before, a bare
        # CreateDate among them.
        zeros = {"QuickTime:CreateDate": "0000:00:00 00:00:00", "CreateDate": "0000:00:00 00:00:00"}
        before = self.vl.photo("Shore", "before.jpg", "2024:06:01 09:00:00")
        saved = self.vl.photo("Shore", "Saved.mov", fields={"QuickTime:CreateDate": "2019:01:01 12:00:00",
                                                            "XMP:DateTimeOriginal": "2024:06:01 10:00:00"})
        adobes = self.vl.photo("Shore", "Wrapped.mp4", fields=dict(zeros, **{"XMP:CreateDate": "2024:06:01 10:30:00"}))
        bare = self.vl.photo("Shore", "bare.jpg", fields={"CreateDate": "2024:06:01 10:45:00"})
        after = self.vl.photo("Shore", "after.jpg", "2024:06:01 11:00:00")
        undated = self.vl.photo("Shore", "Zeros.mp4", fields=zeros)
        listed, shown = self.scroll(kind="all", order="taken")
        self.assertEqual([before, saved, adobes, bare, after, undated], listed,
                         "the XMP date before the QuickTime one, zeros no date, the undated last")
        self.assertEqual(listed, shown)
        self.assertEqual([(2024,), (2024,), (None,)],
                         [self.vl.rows("SELECT year FROM photos WHERE id = ?", each)[0] for each in (saved, adobes, undated)])


class AViewCountsItsClips(Views):
    """#2569: a view's line said "N photos" of a view holding clips; the page could not say them apart, as the reply had one
    total. On the old code the reply has no `videos` and every test here fails."""

    def setUp(self):
        super().setUp()
        self.vl.tree("People/" + WREN, face_root="People")
        june = {"QuickTime:CreateDate": "2024:06:15 12:00:00"}
        self.photos = [self.vl.photo("Coast", "dawn.jpg", "2024:06:14 10:00:00", tags=["Trips/Coast"]),
                       self.vl.photo("Coast", "dusk.jpg", "2024:06:16 10:00:00", tags=["People/" + WREN]),
                       self.vl.photo("Lake", "pier.jpg")]
        self.swim = self.vl.photo("Coast", "Harbour Swim.mov", tags=["Trips/Coast", "People/" + WREN], fields=june)
        self.ferry = self.vl.photo(os.path.join("Coast", "Day 2"), "Ferry.mp4", fields=june)
        self.reel = self.vl.photo("Reels", "Reel.mov", fields=june)
        self.clips = {self.swim, self.ferry, self.reel}
        self.coast = os.path.join(self.vl.pictures, "Coast")

    def counted(self, reply):
        """(photos, clips) as the page shows them, after checking the reply against its own list."""
        self.assertEqual(sorted(reply["videos"]), reply["videos"], "by id")
        self.assertLessEqual(set(reply["videos"]), set(reply["ids"]) if reply["complete"] else self.clips)
        if reply["complete"]:
            self.assertEqual(reply["total"], len(reply["ids"]))
            self.assertEqual(set(reply["videos"]), set(reply["ids"]) & self.clips, "the clips are the list's own")
        return reply["total"] - len(reply["videos"]), len(reply["videos"])

    def test_every_way_of_looking_says_its_photos_and_its_clips_apart(self):
        member = lambda kind, value, recursive=False: {"kind": kind, "value": value, "recursive": recursive}  # noqa: E731
        sources = (
            (dict(kind="all"), (3, 3)),
            (dict(kind="folder", folder=self.coast), (2, 1)),
            (dict(kind="folder", folder=self.coast, recursive="1"), (2, 2)),
            (dict(kind="keyword", value="Trips"), (1, 1)),
            (dict(kind="keyword_only", value="Trips/Coast"), (1, 1)),
            (dict(kind="person", value="People/" + WREN), (1, 1)),
            (dict(kind="year", value="2024"), (2, 3)),
            (dict(kind="month", value="2024-06"), (2, 3)),
            (dict(kind="folder", folder=os.path.join(self.vl.pictures, "Reels")), (0, 1)),
            (dict(kind="month", value="1999-01"), (0, 0)),
            (dict(kind="search", value='{"words": "swim"}'), (0, 1)),
        )
        for query, wanted in sources:
            for order in ("taken", "name-desc", "caption"):
                with self.subTest(query=query, order=order):
                    self.assertEqual(wanted, self.counted(self.ids(order=order, **query)))
        # The clip is in both members of a union, and in the folder and the keyword of a search: once each.
        union = [member("folder", self.coast), member("keyword", "Trips/Coast"), member("person", "People/" + WREN)]
        reply = self.client.post("/library/api/library/ids", json={"kind": "any_of", "value": union}).get_json()
        self.assertEqual((2, 1), self.counted(reply))
        search = {"all_of": [member("folder", self.coast)], "any_of": [member("keyword", "Trips"), member("person", "People/" + WREN)]}
        reply = self.client.post("/library/api/library/ids", json={"kind": "search", "value": search}).get_json()
        self.assertEqual((2, 1), self.counted(reply), "the folder's photos tagged or naming someone: the swim once")
        search["none_of"] = [member("person", "People/" + WREN)]
        reply = self.client.post("/library/api/library/ids", json={"kind": "search", "value": search}).get_json()
        self.assertEqual((1, 0), self.counted(reply), "less those naming the person: no clip is left")

    def test_a_cut_list_counts_the_whole_source_and_a_new_clip_is_in_the_next_read(self):
        with mock.patch.object(library_view, "MAX_IDS", 2):
            cut = self.ids(kind="all")
        self.assertEqual((2, False, 6), (len(cut["ids"]), cut["complete"], cut["total"]))
        self.assertEqual(sorted(self.clips), cut["videos"], "the source's clips, not the head's")
        late = self.vl.photo("Reels", "Late.mov")
        self.clips.add(late)
        self.assertEqual((3, 4), self.counted(self.ids(kind="all")), "the index added a clip: the next read counts it")
        self.assertIn(late, self.ids(kind="all")["videos"])


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
        photo_rows.move(self.vl.path, {old: new})                                 # the rename's row follows the file
        card = self.cards([self.a])[0]
        self.assertEqual(("renamed.jpg", self.a), (card["name"], card["id"]), "the same photo under its new name")
        self.assertEqual((self.a, None), (self.get("/api/library/find", query_string={"path": new}).get_json()["id"],
                                          self.get("/api/library/find", query_string={"path": old}).get_json()["id"]))
        self.assertEqual([], self.ids(kind="keyword", value="Trips/Coast")["ids"])
        photo_rows.tag(self.vl.path, new, ["Trips/Coast"])                     # a bulk tag, or the CLI, in another connection
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
                for make in (runtime.index_folder(library, subfolders=False), runtime.index_folder(library)):
                    tagpup_routes.folders.of(library).put(self.folder, {"a": {"path": "a"}})
                    try:
                        make(self.folder, None)
                    except RuntimeError:
                        pass
                    self.assertIsNone(tagpup_routes.folders.of(library).get(self.folder), failing)


if __name__ == "__main__":
    unittest.main()

"""The library as the views see it (tagpup.services.library_view over tagpup.store.library_view; docs/ARCHITECTURE.md,
phase 9a): which photos a source holds, in what order, a keyset page at a time, the cards of a page, and the
navigator's counts.

Photos are rows as the indexer records them (tests/view_library.py: photo_rows.add_read, then record_indexed, which
keeps photo_people and the derived tables as it does for real), tags are nodes made by taxonomy.add_path. Fictional
names only: the libraries are photographs of real people, many of them minors.
"""
import os
import re
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
from test_migrations import at_version  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import damaged_photos, library_view  # noqa: E402
from tagpup.store import db, derived, schema  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402


def walk(vl, kind, value=None, recursive=False, limit=3):
    """(every id in the order the pages gave them, the pages' sizes, the totals they said)."""
    token, ids, sizes, totals = None, [], [], set()
    while True:
        found = library_view.view(vl.library, kind, value, recursive, token, limit)
        ids += found["ids"]
        sizes.append(len(found["ids"]))
        totals.add(found["total"])
        token = found["next"]
        if token is None:
            return ids, sizes, totals


class Library(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)

    def ids(self, kind, value=None, recursive=False):
        return walk(self.vl, kind, value, recursive, limit=500)[0]


class TheOrder(Library):
    def test_by_date_taken_then_id_and_photos_with_no_date_at_the_end(self):
        late = self.vl.photo("A", "late.jpg", taken="2024:06:03 10:00:00")
        undated_first = self.vl.photo("A", "undated1.jpg")
        early = self.vl.photo("A", "early.jpg", taken="2023:01:01 08:00:00")
        undated_second = self.vl.photo("A", "undated2.jpg")
        tied_a = self.vl.photo("A", "tied_a.jpg", taken="2024:01:01 00:00:00")
        tied_b = self.vl.photo("A", "tied_b.jpg", taken="2024:01:01 00:00:00")
        self.assertEqual([early, tied_a, tied_b, late, undated_first, undated_second], self.ids("all"))

    def test_the_file_time_does_not_order_it(self):
        first = self.vl.photo("A", "z.jpg", taken="2020:01:01 00:00:00", real=True)
        second = self.vl.photo("A", "a.jpg", taken="2019:01:01 00:00:00", real=True)
        os.utime(self.vl.path_of(second), (2_000_000_000, 2_000_000_000))
        self.assertEqual([second, first], self.ids("all"))

    def test_a_date_in_the_future_one_before_1970_and_one_with_a_zone_are_ordered_as_text_is(self):
        future = self.vl.photo("A", "future.jpg", taken="2099:12:31 23:59:59")
        old = self.vl.photo("A", "old.jpg", taken="1950:05:05 05:05:05")
        zoned = self.vl.photo("A", "zoned.jpg", taken="2024:06:01 10:00:00+02:00")
        plain = self.vl.photo("A", "plain.jpg", taken="2024:06:01 10:00:00")
        self.assertEqual([old, plain, zoned, future], self.ids("all"))

    def test_a_row_suggest_made_has_no_date_and_is_listed_last(self):
        dated = self.vl.photo("A", "dated.jpg", taken="2024:01:01 00:00:00")
        unread = self.vl.unread("A", "never read.jpg")
        self.assertEqual([dated, unread], self.ids("all"))
        card = library_view.cards(self.vl.library, [unread])[0]
        self.assertIsNone(card["taken"])

    def test_an_empty_library_is_an_empty_page(self):
        found = library_view.view(self.vl.library, "all")
        self.assertEqual(([], 0, None), (found["ids"], found["total"], found["next"]))


class TheKeyset(Library):
    def setUp(self):
        super().setUp()
        self.made = [self.vl.photo("A", "p%d.jpg" % n, taken="2024:01:%02d 10:00:00" % (n + 1)) for n in range(7)]
        self.made += [self.vl.photo("A", "u%d.jpg" % n) for n in range(2)]

    def test_pages_cover_the_source_once_each_with_the_same_total(self):
        ids, sizes, totals = walk(self.vl, "all", limit=3)
        self.assertEqual(self.made, ids)
        self.assertEqual([3, 3, 3], sizes)
        self.assertEqual({9}, totals)

    def test_the_last_page_has_no_next_and_a_page_ending_exactly_has_none_either(self):
        found = library_view.view(self.vl.library, "all", limit=9)
        self.assertIsNone(found["next"])
        found = library_view.view(self.vl.library, "all", limit=8)
        self.assertIsNotNone(found["next"])
        self.assertEqual([self.made[8]], library_view.view(self.vl.library, "all", after=found["next"], limit=8)["ids"])

    def test_the_page_after_the_dated_ones_goes_on_into_the_undated(self):
        found = library_view.view(self.vl.library, "all", limit=6)
        second = library_view.view(self.vl.library, "all", after=found["next"], limit=6)
        self.assertEqual(self.made[6:], second["ids"])

    def test_a_photo_added_between_pages_is_never_repeated_and_none_is_skipped(self):
        first = library_view.view(self.vl.library, "all", limit=3)
        before_the_cursor = self.vl.photo("A", "early.jpg", taken="2000:01:01 00:00:00")
        after_the_cursor = self.vl.photo("A", "mid.jpg", taken="2024:01:05 12:00:00")
        rest, token = [], first["next"]
        while token:
            page = library_view.view(self.vl.library, "all", after=token, limit=3)
            rest += page["ids"]
            token = page["next"]
        seen = first["ids"] + rest
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(self.made), set(seen) - {after_the_cursor})
        self.assertNotIn(before_the_cursor, seen, "it sorts before the page already read")
        self.assertIn(after_the_cursor, seen)

    def test_a_photo_deleted_between_pages_skips_no_other(self):
        first = library_view.view(self.vl.library, "all", limit=3)
        self.vl.conn.execute("DELETE FROM photos WHERE id = ?", (self.made[1],))   # one already shown
        self.vl.conn.execute("DELETE FROM photos WHERE id = ?", (self.made[4],))   # one still to come
        self.vl.conn.commit()
        rest, token = [], first["next"]
        while token:
            page = library_view.view(self.vl.library, "all", after=token, limit=3)
            rest += page["ids"]
            token = page["next"]
        self.assertEqual([each for each in self.made if each not in (self.made[1], self.made[4])],
                         [each for each in first["ids"] + rest if each != self.made[1]])

    def test_a_photo_redated_between_pages_appears_where_its_new_date_puts_it(self):
        first = library_view.view(self.vl.library, "all", limit=3)
        # an unread photo ahead of the cursor given a date before it, and a read one given a date after
        behind, ahead = self.made[5], self.made[0]
        self.vl.conn.execute("UPDATE photos SET taken = '2000:01:01 00:00:00' WHERE id = ?", (behind,))
        self.vl.conn.execute("UPDATE photos SET taken = '2030:01:01 00:00:00' WHERE id = ?", (ahead,))
        self.vl.conn.commit()
        rest, token = [], first["next"]
        while token:
            page = library_view.view(self.vl.library, "all", after=token, limit=3)
            rest += page["ids"]
            token = page["next"]
        untouched = [each for each in self.made if each not in (behind, ahead)]
        seen = first["ids"] + rest
        self.assertEqual(untouched, [each for each in seen if each in untouched], "no other photo repeated or skipped")
        self.assertNotIn(behind, seen, "its new date is before the page already read: the change explains it")
        self.assertEqual(2, seen.count(ahead), "shown where it was, and again where it is now")

    def test_writes_during_a_walk_do_not_break_it(self):
        errors = []

        def write():
            conn = db.connect(self.vl.path)   # a connection of the thread's own
            try:
                for number in range(30):
                    path = os.path.join(self.vl.pictures, "B", "w%d.jpg" % number)
                    photo_rows.add_read(conn, path, {"EXIF:DateTimeOriginal": "2025:03:%02d 00:00:00" % (number % 28 + 1)})
                    conn.commit()
            except Exception as why:   # the test's own writer: a failure is the test's
                errors.append(why)
            finally:
                conn.close()

        thread = threading.Thread(target=write)
        thread.start()
        seen, token = [], None
        while True:
            page = library_view.view(self.vl.library, "all", after=token, limit=4)
            seen += page["ids"]
            token = page["next"]
            if not token:
                break
        thread.join(30)
        self.assertEqual([], errors)
        self.assertEqual(len(seen), len(set(seen)), "no photo twice")
        self.assertTrue(set(self.made) <= set(seen), "no photo that was there at the start was skipped")


class TheTokenAndTheLimit(Library):
    def setUp(self):
        super().setUp()
        for n in range(4):
            self.vl.photo("A", "p%d.jpg" % n, taken="2024:01:%02d 10:00:00" % (n + 1))

    def refuses(self, **asked):
        with self.assertRaises(Refused) as caught:
            library_view.view(self.vl.library, "all", **asked)
        return str(caught.exception)

    def test_a_token_that_is_not_one_this_server_made_is_refused_in_a_sentence(self):
        for token in ("garbage!", "e30", "W10", "A" * 5000, "%00", "Wzk5LCJ4IiwxXQ",
                      library_view.encode(store.Cursor(0, "2024", 1)) + "!"):
            with self.subTest(token=token[:30]):
                self.assertIn("not one this server made", self.refuses(after=token))

    def test_forged_tokens_of_the_right_shape_are_refused_when_their_parts_are_wrong(self):
        import base64
        import json
        for found in ([0, 5, 1], [2, "x", 1], [0, "x", -1], [0, "x", 2 ** 70], [1, "x", 1], [0, "x" * 100, 1], [0, "x", 1.5],
                      [True, "x", 1], [0, None, 1], "nope", {"a": 1}):
            token = base64.urlsafe_b64encode(json.dumps(found).encode()).decode().rstrip("=")
            with self.subTest(found=found):
                self.assertIn("not one this server made", self.refuses(after=token))

    def test_a_forged_token_of_a_good_shape_is_just_a_place_to_start_from(self):
        token = library_view.encode(store.Cursor(0, "2024:01:02 10:00:00", 0))
        found = library_view.view(self.vl.library, "all", after=token)
        self.assertEqual(3, len(found["ids"]))

    def test_a_token_survives_a_round_trip(self):
        cursor = store.Cursor(0, "2024:01:02 10:00:00+02:00", 41)
        self.assertEqual(cursor, library_view.decode(library_view.encode(cursor)))
        self.assertEqual(store.Cursor(1, None, 7), library_view.decode(library_view.encode(store.Cursor(1, None, 7))))
        self.assertIsNone(library_view.decode(None))
        self.assertIsNone(library_view.decode(""))

    def test_a_limit_below_one_or_not_a_number_is_refused_and_a_huge_one_is_capped(self):
        for limit in (0, -5, "0", "-1", "ten", "1.5", [1]):
            with self.subTest(limit=limit):
                self.assertIn("limit must be", self.refuses(limit=limit))
        self.assertEqual(library_view.MAX_LIMIT, library_view.view(self.vl.library, "all", limit=10 ** 9)["limit"])
        self.assertEqual(library_view.MAX_LIMIT, library_view.view(self.vl.library, "all", limit="99999")["limit"])
        self.assertEqual(library_view.DEFAULT_LIMIT, library_view.view(self.vl.library, "all")["limit"])
        self.assertEqual(2, len(library_view.view(self.vl.library, "all", limit="2")["ids"]))

    def test_a_kind_that_is_none_is_refused(self):
        with self.assertRaises(Refused):
            library_view.view(self.vl.library, "everything")
        with self.assertRaises(Refused):
            library_view.view(self.vl.library, None)
        for kind in ("folder", "keyword", "person", "year", "month"):
            with self.subTest(kind=kind), self.assertRaises(Refused):
                library_view.view(self.vl.library, kind, None)
            with self.subTest(kind=kind, value="blank"), self.assertRaises(Refused):
                library_view.view(self.vl.library, kind, "  ")
        for kind, value in (("year", "20x4"), ("year", "-5"), ("year", "20245"), ("month", "2024-13"), ("month", "2024-6"),
                            ("month", "2024:06"), ("month", "June")):
            with self.subTest(kind=kind, value=value), self.assertRaises(Refused):
                library_view.view(self.vl.library, kind, value)


class AFolder(Library):
    def setUp(self):
        super().setUp()
        self.top = self.vl.photo("Trip", "top.jpg", taken="2024:01:01 00:00:00")
        self.sub = self.vl.photo(os.path.join("Trip", "Day 1"), "sub.jpg", taken="2024:01:02 00:00:00")
        self.deep = self.vl.photo(os.path.join("Trip", "Day 1", "Hike"), "deep.jpg", taken="2024:01:03 00:00:00")
        self.cousin = self.vl.photo("Trip 2", "cousin.jpg", taken="2024:01:04 00:00:00")
        self.folder = os.path.join(self.vl.pictures, "Trip")

    def test_a_folder_alone_holds_only_what_is_in_it(self):
        self.assertEqual([self.top], self.ids("folder", self.folder))

    def test_with_its_subfolders_it_holds_what_is_under_it_and_not_a_folder_that_starts_alike(self):
        self.assertEqual([self.top, self.sub, self.deep], self.ids("folder", self.folder, recursive=True))

    def test_the_total_is_the_whole_folders_and_not_the_pages(self):
        found = library_view.view(self.vl.library, "folder", self.folder, True, limit=2)
        self.assertEqual((3, 2), (found["total"], len(found["ids"])))

    def test_a_folder_typed_in_another_case_or_spelling_is_the_same_folder(self):
        if os.name != "nt":
            self.skipTest("a filesystem with case")
        for typed in (self.folder.lower(), self.folder.upper(), self.folder.replace("\\", "/"), self.folder + "\\",
                      self.folder.replace("\\Trip", "\\.\\Trip")):
            with self.subTest(typed=typed):
                self.assertEqual([self.top], self.ids("folder", typed))
                self.assertEqual(3, len(self.ids("folder", typed, recursive=True)))

    def test_a_folder_with_no_photo_is_an_empty_page_not_an_error(self):
        for recursive in (False, True):
            found = library_view.view(self.vl.library, "folder", os.path.join(self.vl.pictures, "Nothing here"), recursive)
            self.assertEqual(([], 0, None), (found["ids"], found["total"], found["next"]))

    def test_a_folder_whose_name_holds_a_percent_an_underscore_or_a_quote_is_matched_exactly(self):
        odd = self.vl.photo("100% a_b O'Neil", "x.jpg", taken="2024:02:01 00:00:00")
        lookalike = self.vl.photo("100X aXb O'Neil", "y.jpg", taken="2024:02:02 00:00:00")
        folder = os.path.join(self.vl.pictures, "100% a_b O'Neil")
        self.assertEqual([odd], self.ids("folder", folder))
        self.assertEqual([odd], self.ids("folder", folder, True))
        self.assertNotIn(lookalike, self.ids("folder", folder, True))

    def test_a_folder_of_hundreds_pages_through(self):
        made = [self.vl.photo("Big", "p%03d.jpg" % n, taken="2022:05:%02d 00:%02d:00" % (n % 28 + 1, n % 60))
                for n in range(250)]
        ids, sizes, totals = walk(self.vl, "folder", os.path.join(self.vl.pictures, "Big"), False, limit=100)
        self.assertEqual(([100, 100, 50], {250}), (sizes, totals))
        self.assertEqual(sorted(made), sorted(ids))


class AKeyword(Library):
    def setUp(self):
        super().setUp()
        self.vl.tree("Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Trips/100%", "Trips/a_b", "Trips/O'Brien",
                     "Trips/1000", "Trips/aXb", "Trips Extra/Other", "Activity/Sailing")
        photo = self.vl.photo
        self.harbour = photo("A", "h.jpg", taken="2024:01:01 00:00:00", tags=["Trips/Coast/Harbour"])
        self.cliffs = photo("A", "c.jpg", taken="2024:01:02 00:00:00", tags=["Trips/Coast/Cliffs"])
        self.both = photo("A", "b.jpg", taken="2024:01:03 00:00:00", tags=["Trips/Coast/Harbour", "Trips/Coast/Cliffs"])
        self.lakes = photo("A", "l.jpg", taken="2024:01:04 00:00:00", tags=["Trips/Lakes"])
        self.percent = photo("A", "p.jpg", taken="2024:01:05 00:00:00", tags=["Trips/100%"])
        self.thousand = photo("A", "t.jpg", taken="2024:01:06 00:00:00", tags=["Trips/1000"])
        self.under = photo("A", "u.jpg", taken="2024:01:07 00:00:00", tags=["Trips/a_b"])
        self.axb = photo("A", "x.jpg", taken="2024:01:08 00:00:00", tags=["Trips/aXb"])
        self.quote = photo("A", "q.jpg", taken="2024:01:09 00:00:00", tags=["Trips/O'Brien"])
        self.extra = photo("A", "e.jpg", taken="2024:01:10 00:00:00", tags=["Trips Extra/Other"])
        self.none = photo("A", "n.jpg", taken="2024:01:11 00:00:00")

    def test_a_keyword_holds_the_photos_under_it_and_a_photo_with_two_tags_under_it_once(self):
        self.assertEqual([self.harbour, self.cliffs, self.both], self.ids("keyword", "Trips/Coast"))
        found = library_view.view(self.vl.library, "keyword", "Trips/Coast")
        self.assertEqual(3, found["total"], "counted once too")

    def test_the_whole_branch_and_not_a_branch_whose_name_starts_alike(self):
        ids = self.ids("keyword", "Trips")
        self.assertIn(self.lakes, ids)
        self.assertNotIn(self.extra, ids)
        self.assertEqual(9, len(ids))

    def test_a_leaf_holds_its_own_photos(self):
        self.assertEqual([self.harbour, self.both], self.ids("keyword", "Trips/Coast/Harbour"))

    def test_a_percent_an_underscore_and_a_quote_are_plain_characters(self):
        self.assertEqual([self.percent], self.ids("keyword", "Trips/100%"))
        self.assertEqual([self.under], self.ids("keyword", "Trips/a_b"))
        self.assertEqual([self.quote], self.ids("keyword", "Trips/O'Brien"))

    def test_a_keyword_the_tree_has_no_node_for_holds_nothing_and_is_not_an_error(self):
        for tag in ("Trips/Nowhere", "Nothing", "trips/coast", "Trips/%", "Trips/a%b", "'; DROP TABLE photos; --"):
            with self.subTest(tag=tag):
                found = library_view.view(self.vl.library, "keyword", tag)
                self.assertEqual(([], 0), (found["ids"], found["total"]))

    def test_it_is_read_as_a_keyword_is(self):
        self.assertEqual(self.ids("keyword", "Trips/Coast"), self.ids("keyword", " Trips | Coast "))
        self.assertEqual(self.ids("keyword", "Trips/Coast"), self.ids("keyword", "Trips\\Coast"))

    def test_a_node_added_after_a_photo_was_indexed_finds_it(self):
        late = self.vl.photo("A", "late.jpg", taken="2024:02:01 00:00:00", tags=["Trips/Later"])
        self.assertEqual([], self.ids("keyword", "Trips/Later"))
        self.vl.tree("Trips/Later")
        from tagpup.store import taxonomy
        self.vl.conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (late,))
        derived.refresh_photos(self.vl.conn, [late])
        self.vl.conn.commit()
        self.assertEqual([late], self.ids("keyword", "Trips/Later"))
        self.assertTrue(taxonomy.node(self.vl.path, 1) is not None)


class APerson(Library):
    def setUp(self):
        super().setUp()
        self.vl.tree("People/Wren Halloway", "People/Rowan Thackeray", face_root="People")
        self.wren_a = self.vl.photo("A", "a.jpg", taken="2024:01:01 00:00:00", tags=["People/Wren Halloway"])
        self.wren_b = self.vl.photo("A", "b.jpg", taken="2024:01:02 00:00:00", tags=["People/Wren Halloway", "People/Rowan Thackeray"])
        self.rowan = self.vl.photo("A", "c.jpg", taken="2024:01:03 00:00:00", tags=["People/Rowan Thackeray"])
        self.nobody = self.vl.photo("A", "d.jpg", taken="2024:01:04 00:00:00")

    def test_a_person_holds_the_photos_they_are_in(self):
        self.assertEqual([self.wren_a, self.wren_b], self.ids("person", "Wren Halloway"))
        self.assertEqual([self.wren_b, self.rowan], self.ids("person", "Rowan Thackeray"))

    def test_a_person_nobody_is_called_holds_nothing(self):
        found = library_view.view(self.vl.library, "person", "Nobody At All")
        self.assertEqual(([], 0), (found["ids"], found["total"]))

    def test_names_that_differ_only_in_case_are_one_person_and_a_photo_is_counted_once(self):
        face_rows.add_people(self.vl.conn, self.vl.path_of(self.rowan), ["rowan thackeray"], source="face")
        face_rows.add_people(self.vl.conn, self.vl.path_of(self.wren_a), ["WREN HALLOWAY"], source="face")
        self.vl.conn.commit()
        self.assertEqual([self.wren_b, self.rowan], self.ids("person", "Rowan Thackeray"))
        self.assertEqual([self.wren_b, self.rowan], self.ids("person", "ROWAN THACKERAY"))
        found = library_view.view(self.vl.library, "person", "wren halloway")
        self.assertEqual((2, [self.wren_a, self.wren_b]), (found["total"], found["ids"]), "wren_a holds both spellings")

    def test_a_percent_or_a_quote_in_a_name_is_a_plain_character(self):
        for name in ("Wren %", "Wren _alloway", "Wren' OR '1'='1", "%"):
            with self.subTest(name=name):
                self.assertEqual([], self.ids("person", name))


class ADate(Library):
    def setUp(self):
        super().setUp()
        photo = self.vl.photo
        self.a = photo("A", "a.jpg", taken="2023:12:31 23:59:59")
        self.b = photo("A", "b.jpg", taken="2024:06:01 00:00:00")
        self.c = photo("A", "c.jpg", taken="2024:06:30 23:59:59")
        self.d = photo("A", "d.jpg", taken="2024:07:01 00:00:00")
        self.e = photo("A", "e.jpg", taken="2024:06")   # a date with no day, as some cameras write it
        self.named = photo("2019", "no date.jpg")      # no Date Taken: the year is in its folder's name
        self.nothing = photo("Misc", "nothing.jpg")

    def test_a_year_holds_the_photos_of_it_by_the_year_the_library_records(self):
        self.assertEqual([self.e, self.b, self.c, self.d], self.ids("year", "2024"))
        self.assertEqual([self.a], self.ids("year", "2023"))
        self.assertEqual([self.named], self.ids("year", "2019"), "from its folder's name, no Date Taken")

    def test_a_month_holds_its_first_moment_and_its_last_and_not_the_next(self):
        self.assertEqual([self.e, self.b, self.c], self.ids("month", "2024-06"))
        self.assertEqual([self.d], self.ids("month", "2024-07"))
        self.assertEqual([self.a], self.ids("month", "2023-12"))

    def test_a_month_nobody_was_photographed_in_is_empty(self):
        found = library_view.view(self.vl.library, "month", "1999-01")
        self.assertEqual(([], 0), (found["ids"], found["total"]))

    def test_a_photo_with_no_year_is_in_no_year_but_is_in_all(self):
        self.assertIn(self.nothing, self.ids("all"))
        for year in range(1990, 2030):
            self.assertNotIn(self.nothing, self.ids("year", str(year)))


class TheCards(Library):
    def setUp(self):
        super().setUp()
        self.one = self.vl.photo("A", "one.jpg", taken="2024:01:01 00:00:00", real=True)
        self.two = self.vl.photo("A", "two.jpg", taken="2024:01:02 00:00:00")

    def test_a_card_is_small_and_names_the_photo_by_id(self):
        card = library_view.cards(self.vl.library, [self.two, self.one])[0]
        self.assertEqual({"id", "name", "path", "taken", "damaged", "damage", "thumb"}, set(card))
        self.assertEqual((self.two, "two.jpg", self.vl.path_of(self.two), "2024:01:02 00:00:00", False, None),
                         (card["id"], card["name"], card["path"], card["taken"], card["damaged"], card["damage"]))
        self.assertTrue(card["thumb"].startswith("/api/photo-thumb?id=%d&v=" % self.two))

    def test_the_cards_are_in_the_order_asked_and_a_deleted_photo_is_left_out(self):
        self.assertEqual([self.two, self.one], [each["id"] for each in library_view.cards(self.vl.library, [self.two, self.one])])
        self.assertEqual([self.one], [each["id"] for each in library_view.cards(self.vl.library, [99999, self.one])])
        self.assertEqual([], library_view.cards(self.vl.library, []))

    def test_the_thumbnail_url_carries_the_stamp_the_row_holds(self):
        card = library_view.cards(self.vl.library, [self.one])[0]
        mtime = self.vl.rows("SELECT mtime FROM photos WHERE id = ?", self.one)[0][0]
        self.assertEqual("/api/photo-thumb?id=%d&v=%s" % (self.one, repr(float(mtime))), card["thumb"])

    def test_a_damaged_photo_is_flagged_while_its_record_describes_it(self):
        row = self.vl.rows("SELECT mtime, size FROM photos WHERE id = ?", self.one)[0]
        damaged_photos.remember(self.vl.library, [(self.vl.path_of(self.one), (row[0], row[1]), "truncated", "x", 0)])
        card = library_view.cards(self.vl.library, [self.one, self.two])
        self.assertEqual([(True, "truncated"), (False, None)], [(each["damaged"], each["damage"]) for each in card])
        self.vl.conn.execute("UPDATE photos SET size = size + 1 WHERE id = ?", (self.one,))
        self.vl.conn.commit()
        self.assertFalse(library_view.cards(self.vl.library, [self.one])[0]["damaged"], "the file is not the one found damaged")

    def test_500_cards_are_one_batch_and_read_no_blob_or_metadata(self):
        for n in range(60):
            self.vl.photo("B", "p%d.jpg" % n, taken="2024:03:01 00:%02d:00" % n)
        statements = []
        real = db.connect

        def traced(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        counted = []
        for count in (3, 62):
            del statements[:]
            ids = [row[0] for row in self.vl.rows("SELECT id FROM photos ORDER BY id LIMIT %d" % count)]
            with mock.patch.object(db, "connect", traced):
                shown = library_view.cards(self.vl.library, ids)
            self.assertEqual(count, len(shown))
            selects = [each for each in statements if each.lstrip().upper().startswith("SELECT")]
            counted.append(len(selects))
            self.assertLessEqual(len(selects), 8, "a few reads, not one for each photo: %s" % selects)
            for each in statements:
                for column in ("raw_metadata", "vector", "embedding", "crop_image", "tags", "captions"):
                    self.assertIsNone(re.search(r"\b%s\b" % column, each), each)
        self.assertEqual(counted[0], counted[1], "as many reads for 62 photos as for 3")


class NotReady(unittest.TestCase):
    def test_a_library_behind_is_a_sentence_and_nothing_is_migrated(self):
        home = own_home.for_test(self)
        from tagpup.core.library import Library as LibraryName
        for version in (18, 19):
            with self.subTest(version=version):
                path = home.library("older%d.db" % version)
                at_version(path, version)
                library = LibraryName(path)
                for call in (lambda: library_view.view(library, "all"), lambda: library_view.navigator(library, "folders"),
                             lambda: library_view.cards(library, [1])):
                    with self.assertRaises(library_view.NotReady) as caught:
                        call()
                    self.assertIn("has not been brought up to date", str(caught.exception))
                    self.assertIn("older%d" % version, str(caught.exception))
                self.assertEqual(version, db.connect(db.readonly_uri(path), uri=True).execute(
                    "SELECT MAX(version) FROM schema_version").fetchone()[0], "reading it did not migrate it")

    def test_a_library_that_is_not_there_is_not_found(self):
        from tagpup.core.library import Library as LibraryName
        from tagpup.core.result import NotFound
        with self.assertRaises(NotFound):
            library_view.view(LibraryName(os.path.join(own_home.for_test(self).data, "nobody.db")), "all")


class TheNavigator(Library):
    def setUp(self):
        super().setUp()
        self.vl.tree("Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Activity/Sailing", "People/Wren Halloway",
                     "People/Rowan Thackeray", "Unused/Branch", face_root="People")
        photo = self.vl.photo
        photo("2024 A", "1.jpg", taken="2024:06:01 00:00:00", tags=["Trips/Coast/Harbour", "Trips/Coast/Cliffs", "People/Wren Halloway"])
        photo("2024 A", "2.jpg", taken="2024:06:02 00:00:00", tags=["Trips/Coast/Harbour"])
        photo(os.path.join("2024 A", "Sub"), "3.jpg", taken="2024:07:02 00:00:00", tags=["Trips/Lakes", "People/Wren Halloway",
                                                                                         "People/Rowan Thackeray"])
        photo("2023 B", "4.jpg", taken="2023:01:02 00:00:00", tags=["Activity/Sailing", "Not In The Tree"])
        photo("Misc", "5.jpg")
        photo("2019", "6.jpg")

    def section(self, name):
        return library_view.navigator(self.vl.library, name)

    def test_the_folders_are_the_tree_by_native_path_with_both_counts(self):
        found = {each["path"]: each for each in self.section("folders")["folders"]}
        a, sub = os.path.join(self.vl.pictures, "2024 A"), os.path.join(self.vl.pictures, "2024 A", "Sub")
        self.assertEqual((2, 3, self.vl.pictures), (found[a]["direct"], found[a]["recursive"], found[a]["parent"]))
        self.assertEqual((1, 1, a), (found[sub]["direct"], found[sub]["recursive"], found[sub]["parent"]))
        self.assertEqual((0, 6), (found[self.vl.pictures]["direct"], found[self.vl.pictures]["recursive"]))
        self.assertEqual("2024 A", found[a]["name"])
        paths_listed = [each["path"] for each in self.section("folders")["folders"]]
        for each in self.section("folders")["folders"]:
            if each["parent"] is not None:
                self.assertLess(paths_listed.index(each["parent"]), paths_listed.index(each["path"]), "parents first")

    def test_the_folders_agree_with_a_range_of_the_photos_paths(self):
        for each in self.section("folders")["folders"]:
            self.assertEqual(each["recursive"], len(self.ids("folder", each["path"], True)), each["path"])
            self.assertEqual(each["direct"], len(self.ids("folder", each["path"])), each["path"])

    def test_the_keywords_count_each_photo_once_for_a_node_however_many_of_its_tags_lie_under_it(self):
        found = {each["tag"]: each for each in self.section("keywords")["keywords"]}
        self.assertEqual(2, found["Trips/Coast"]["count"], "photo 1 holds two tags under it")
        self.assertEqual(3, found["Trips"]["count"])
        self.assertEqual(2, found["Trips/Coast/Harbour"]["count"])
        self.assertEqual(1, found["Trips/Coast/Cliffs"]["count"])
        self.assertEqual(0, found["Unused/Branch"]["count"], "a node no photo carries is listed with none")
        self.assertEqual("Trips", found["Trips/Coast"]["parent"])
        self.assertIsNone(found["Trips"]["parent"])
        self.assertEqual("Coast", found["Trips/Coast"]["name"])

    def test_every_keyword_count_equals_the_store_s_count_under_a_tag(self):
        for each in self.section("keywords")["keywords"]:
            self.assertEqual(derived.count_under_tag(self.vl.conn, each["tag"]), each["count"], each["tag"])
            self.assertEqual(each["count"], len(self.ids("keyword", each["tag"])), each["tag"])

    def test_a_keyword_no_node_has_is_in_no_count(self):
        self.assertNotIn("Not In The Tree", [each["tag"] for each in self.section("keywords")["keywords"]])

    def test_the_people_are_counted_by_photos_most_first(self):
        found = self.section("people")["people"]
        self.assertEqual([{"name": "Wren Halloway", "count": 2}, {"name": "Rowan Thackeray", "count": 1}], found)

    def test_people_who_differ_only_in_case_are_one_entry_and_a_photo_counts_once(self):
        photo = self.vl.photo("2024 A", "extra.jpg", taken="2024:08:01 00:00:00")
        face_rows.add_people(self.vl.conn, self.vl.path_of(photo), ["wren halloway", "WREN HALLOWAY"], source="face")
        self.vl.conn.commit()
        found = {each["name"]: each["count"] for each in self.section("people")["people"]}
        self.assertEqual(3, found["Wren Halloway"])
        self.assertEqual(2, len(found))

    def test_the_dates_are_years_with_their_months_and_what_has_no_month_of_it(self):
        found = self.section("dates")
        years = {each["year"]: each for each in found["years"]}
        self.assertEqual([2019, 2023, 2024], [each["year"] for each in found["years"]])
        self.assertEqual((3, [{"month": "2024-06", "count": 2}, {"month": "2024-07", "count": 1}], 0),
                         (years[2024]["count"], years[2024]["months"], years[2024]["other"]))
        self.assertEqual((1, [{"month": "2023-01", "count": 1}]), (years[2023]["count"], years[2023]["months"]))
        self.assertEqual((1, [], 1), (years[2019]["count"], years[2019]["months"], years[2019]["other"]), "a year from a name")
        self.assertEqual(1, found["undated"])

    def test_each_month_and_year_agrees_with_the_source_it_opens(self):
        for each in self.section("dates")["years"]:
            self.assertEqual(each["count"], library_view.view(self.vl.library, "year", str(each["year"]))["total"])
            for month in each["months"]:
                self.assertEqual(month["count"], library_view.view(self.vl.library, "month", month["month"])["total"])

    def test_a_date_written_with_dashes_is_in_its_year_and_in_no_month(self):
        photo = self.vl.photo("2025 X", "dashes.jpg", taken="2025-02-03 10:00:00")
        found = {each["year"]: each for each in self.section("dates")["years"]}
        self.assertEqual((1, [], 1), (found[2025]["count"], found[2025]["months"], found[2025]["other"]))
        self.assertEqual([photo], self.ids("year", "2025"))

    def test_an_unknown_section_is_refused(self):
        for section in (None, "", "all", "Folders"):
            with self.subTest(section=section), self.assertRaises(Refused):
                library_view.navigator(self.vl.library, section)

    def test_an_empty_library_has_empty_sections(self):
        empty = ViewLibrary(self, "empty")
        for name, key in (("folders", "folders"), ("people", "people")):
            self.assertEqual([], library_view.navigator(empty.library, name)[key])
        self.assertEqual({"years": [], "undated": 0}, library_view.navigator(empty.library, "dates"))
        self.assertTrue(all(each["count"] == 0 for each in library_view.navigator(empty.library, "keywords")["keywords"]))

    def test_the_counts_follow_a_write(self):
        self.vl.photo("Misc", "later.jpg", taken="2024:06:03 00:00:00", tags=["Trips/Coast/Cliffs"])
        found = {each["tag"]: each["count"] for each in self.section("keywords")["keywords"]}
        self.assertEqual(3, found["Trips/Coast"])
        a = {each["path"]: each for each in self.section("folders")["folders"]}[os.path.join(self.vl.pictures, "Misc")]
        self.assertEqual(2, a["direct"])


@unittest.skipUnless(os.name == "nt", "the spellings are Windows paths")
class ARootedLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.side = rl.Side(self.home, "harbour", real=2, bulk=1, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library

    def test_views_speak_native_paths_and_open_a_folder_by_the_path_the_owner_knows(self):
        folder = os.path.join(self.side.pictures, rl.FOLDERS[0])
        found = library_view.view(self.library, "folder", folder, False)
        self.assertEqual(3, found["total"])
        for card in found["cards"]:
            self.assertTrue(card["path"].startswith(folder), card["path"])
        listed = {each["path"] for each in library_view.navigator(self.library, "folders")["folders"]}
        self.assertIn(folder, listed)
        self.assertIn(self.side.pictures, listed)
        self.assertEqual(3, len(library_view.view(self.library, "folder", self.side.pictures, True, limit=500)["ids"]) // 3)

    def test_a_photo_outside_every_root_is_in_all_and_in_its_folder(self):
        outside = library_view.view(self.library, "folder", self.side.outside_folder, True)
        self.assertEqual(2, outside["total"])
        self.assertTrue(all(card["path"].startswith(self.side.outside_folder) for card in outside["cards"]))

    def test_a_root_this_machine_does_not_place_is_the_roots_sentence_and_never_an_empty_library(self):
        os.remove(config.machine_roots_path())
        for call in (lambda: library_view.view(self.library, "all"), lambda: library_view.navigator(self.library, "folders"),
                     lambda: library_view.cards(self.library, [1])):
            with self.assertRaises(paths.RootsError) as caught:
                call()
            self.assertIn("machine_roots.json", str(caught.exception))

    def test_a_folder_typed_in_the_previous_places_spelling_is_resolved(self):
        import shutil
        copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, copy, copy_function=shutil.copy2)
        old = os.path.join(self.side.pictures, rl.FOLDERS[0])
        config.set_location(rl.NAME, copy)
        found = library_view.view(self.library, "folder", old, False)
        self.assertEqual(3, found["total"])
        self.assertTrue(found["cards"][0]["path"].startswith(copy), "paths are the first place's now")


class TheSchemaOfThePlans(unittest.TestCase):
    def test_ready_needs_the_tables_and_the_indexes(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertTrue(store.ready(conn))
        conn.execute("DROP INDEX idx_photos_year")
        self.assertFalse(store.ready(conn))


if __name__ == "__main__":
    unittest.main()

"""A selection of photos by id, and the one resolver the bulk edits and the tally read it with (tagpup.services.selection;
docs/ARCHITECTURE.md, phase 9d-1).

A selection is a list of ids, or a source minus the ids excluded; the resolver gives the photos that exist, in order, each
once, and for a source exactly the ids and the order of /api/library/ids. Rows are made as the indexer makes them
(tests/view_library.py); fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db  # noqa: E402

SOURCES = (
    {"kind": "all"},
    {"kind": "folder", "value": "{folder}", "recursive": False},
    {"kind": "folder", "value": "{folder}", "recursive": True},
    {"kind": "keyword", "value": "Trips"},
    {"kind": "keyword", "value": "Trips/Coast"},
    {"kind": "person", "value": "Wren Halloway"},
    {"kind": "year", "value": "2024"},
    {"kind": "month", "value": "2024-06"},
)


class Library(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "People/Wren Halloway", face_root="People")
        self.a = self.vl.photo("2024 Coast", "a.jpg", taken="2024:06:01 10:00:00", tags=["Trips/Coast", "People/Wren Halloway"])
        self.b = self.vl.photo("2024 Coast", "b.jpg", taken="2024:06:02 10:00:00", tags=["Trips/Lakes"])
        self.c = self.vl.photo(os.path.join("2024 Coast", "Day 2"), "c.jpg", taken="2023:01:02 10:00:00")
        self.d = self.vl.photo("Misc", "d.jpg")
        self.e = self.vl.photo("Misc", "e.jpg")
        self.folder = os.path.join(self.vl.pictures, "2024 Coast")
        self.library = self.vl.library

    def sources(self):
        return [{key: (value.format(folder=self.folder) if isinstance(value, str) else value) for key, value in each.items()}
                for each in SOURCES]

    def resolve(self, body):
        return selection.resolve(self.library, selection.read(self.library, body))


class Reading(Library):
    def test_the_two_shapes(self):
        self.assertEqual(((1, 2), None, ()), tuple(selection.read(self.library, {"ids": [1, 2]})))
        read = selection.read(self.library, {"source": {"kind": "year", "value": "2024"}, "excluded": [5]})
        self.assertEqual((None, "year", 2024, (5,)), (read.ids, read.source.kind, read.source.value, read.excluded))

    def test_a_duplicate_is_one_photo_in_the_order_first_named(self):
        self.assertEqual((3, 1, 2), selection.read(self.library, {"ids": [3, 1, 3, 2, 1]}).ids)

    def test_what_means_nothing_is_refused_with_a_sentence(self):
        for body in (None, [], "x", {}, {"ids": [1], "source": {"kind": "all"}}, {"ids": "1,2"}, {"ids": [1, "2"]},
                     {"ids": [True]}, {"ids": [1.0]}, {"ids": [0]}, {"ids": [-4]}, {"ids": [2 ** 70]}, {"ids": [None]},
                     {"ids": [1], "excluded": [2]}, {"source": "all"}, {"source": {"kind": "nope"}},
                     {"source": {"kind": "year", "value": "x"}}, {"source": {"kind": "keyword", "value": "  "}},
                     {"source": {"kind": "all"}, "excluded": "1"}, {"source": {"kind": "all"}, "excluded": [{}]}):
            with self.subTest(body=body):
                with self.assertRaises(Refused) as why:
                    selection.read(self.library, body)
                self.assertTrue(str(why.exception))

    def test_twenty_thousand_ids_may_be_named_and_one_more_may_not(self):
        self.assertEqual(20_000, len(selection.read(self.library, {"ids": list(range(1, 20_001))}).ids))
        with self.assertRaises(Refused) as why:
            selection.read(self.library, {"ids": list(range(1, 20_002))})
        self.assertIn("20001", str(why.exception))
        with self.assertRaises(Refused):
            selection.read(self.library, {"source": {"kind": "all"}, "excluded": list(range(1, 20_002))})


class Resolving(Library):
    def test_a_source_is_the_ids_route_for_the_same_source_in_the_same_order(self):
        for named in self.sources():
            with self.subTest(source=named):
                query = {"kind": named["kind"]}
                if named["kind"] == "folder":
                    query.update(folder=named["value"], recursive="1" if named["recursive"] else "")
                elif "value" in named:
                    query["value"] = named["value"]
                route = self.client.get("/library/api/library/ids", query_string=query).get_json()
                resolved = self.resolve({"source": named})
                self.assertEqual(route["ids"], resolved.ids)
                self.assertEqual(route["total"], resolved.requested)
                self.assertEqual(0, resolved.excluded)

    def test_the_excluded_are_taken_out_and_the_order_is_kept(self):
        self.assertEqual([self.c, self.b, self.e],
                         self.resolve({"source": {"kind": "all"}, "excluded": [self.a, self.d]}).ids)
        resolved = self.resolve({"source": {"kind": "all"}, "excluded": [self.a, self.d]})
        self.assertEqual((5, 2), (resolved.requested, resolved.excluded))

    def test_excluded_that_are_not_in_the_source_or_not_anywhere_take_nothing_out(self):
        resolved = self.resolve({"source": {"kind": "year", "value": "2024"}, "excluded": [self.d, 9999, self.e]})
        self.assertEqual(([self.a, self.b], 0), (resolved.ids, resolved.excluded))

    def test_excluding_everything_leaves_nothing_and_is_not_an_error(self):
        resolved = self.resolve({"source": {"kind": "all"}, "excluded": [self.a, self.b, self.c, self.d, self.e, 77]})
        self.assertEqual(([], 5), (resolved.ids, resolved.excluded))

    def test_a_list_of_ids_is_those_that_exist_in_the_order_named_each_once(self):
        resolved = self.resolve({"ids": [self.e, 9999, self.a, self.e, 0 + self.d, 123456]})
        self.assertEqual([self.e, self.a, self.d], resolved.ids)
        self.assertEqual((5, 2), (resolved.requested, resolved.missing))

    def test_ids_of_another_library_are_ids_this_library_has_no_photo_of(self):
        other = ViewLibrary(self, "elsewhere", home=self.home)
        theirs = [other.photo("Away", "x%d.jpg" % number) for number in range(30)]
        resolved = self.resolve({"ids": theirs[10:] + [self.a]})
        self.assertEqual([self.a], resolved.ids)

    def test_a_source_nothing_holds_is_an_empty_selection(self):
        for named in ({"kind": "keyword", "value": "No/Such"}, {"kind": "person", "value": "Nobody Atall"},
                      {"kind": "folder", "value": os.path.join(self.vl.pictures, "Nowhere")}, {"kind": "year", "value": "1999"}):
            with self.subTest(source=named):
                self.assertEqual([], self.resolve({"source": named}).ids)

    def test_a_selection_above_the_cap_is_refused_with_how_many_it_was(self):
        with mock.patch.object(selection, "MAX_SELECTED", 4):
            with self.assertRaises(Refused) as why:
                self.resolve({"source": {"kind": "all"}})
            self.assertIn("5 photos", str(why.exception))
        with mock.patch.object(selection, "MAX_SELECTED", 5):
            self.assertEqual(5, len(self.resolve({"source": {"kind": "all"}}).ids))

    def test_the_cap_counts_the_source_and_not_what_is_left_after_the_exclusions(self):
        # 5 photos, 3 excluded: the source is above the cap of 4, however few remain. A page excluding most of a library
        # is asking for the library's size to be read, and 200,000 is the most a job holds.
        with mock.patch.object(selection, "MAX_SELECTED", 4), self.assertRaises(Refused):
            self.resolve({"source": {"kind": "all"}, "excluded": [self.a, self.b, self.c]})

    def test_a_source_that_changes_while_it_is_read_is_one_moment_not_a_mixture(self):
        # The dated photos and the undated are two statements. A photo dated between them is, without one transaction,
        # in neither: it was undated for the first and dated for the second.
        real = library_view.opened
        moved = []

        def opened(library):
            conn = real(library)

            def watch(statement):
                if "p.taken IS NULL" in statement and not moved:
                    moved.append(statement)
                    other = db.connect(self.vl.path)
                    other.execute("UPDATE photos SET taken = ? WHERE id = ?", ("2025:01:01 00:00:00", self.d))
                    other.commit()
                    other.close()
            conn.set_trace_callback(watch)
            return conn

        with mock.patch.object(library_view, "opened", opened):
            resolved = self.resolve({"source": {"kind": "all"}})
        self.assertTrue(moved, "the photo was re-dated between the two statements")
        self.assertEqual(sorted([self.a, self.b, self.c, self.d, self.e]), sorted(resolved.ids))
        self.assertEqual(5, len(set(resolved.ids)))

    def test_a_library_behind_is_not_ready_a_sentence_and_not_a_traceback(self):
        with mock.patch.object(library_view.store, "ready", return_value=False):
            with self.assertRaises(library_view.NotReady):
                self.resolve({"ids": [self.a]})


if __name__ == "__main__":
    unittest.main()

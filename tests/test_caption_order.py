"""The order by caption (tagpup.store.library_view, migration 23; docs/ARCHITECTURE.md, phase 9, the owner's second review,
#714): a library view in caption order, either way, and the index that makes a page of it a seek.

A photo's caption is the first of its captions -- the one the page shows -- compared without case on its first CAPTION_KEY
characters, ties by id; the photos with none come after the captioned ones in both directions, by id in the order's, as the
undated photos do in an order by Date Taken. Migration 23 is additive and index-only, as 20 and 22 are: no column, no trigger,
no row changes, touches no table (findings #464: nothing checks that by itself, so this does), blocks no undo.

Photos are rows as the indexer records a read (tests/view_library.py); a caption changed is what a save records
(store.photos.record_saved). Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from test_migrations import at_version  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

WREN = "Wren Halloway"


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def walk(vl, kind, value=None, order=None, limit=2):
    """Every id in the order the keyset pages gave them."""
    token, ids = None, []
    while True:
        found = library_view.view(vl.library, kind, value, False, token, limit, order)
        ids += found["ids"]
        token = found["next"]
        if token is None:
            return ids


class Captioned(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("Trips/Coast", "People/" + WREN, face_root="People")
        self.harbour = vl.photo("2021", "a.jpg", taken="2021:07:01 09:00:00", caption="harbour at dawn", tags=["Trips/Coast"])
        self.none_a = vl.photo("2021", "b.jpg", taken="2021:07:02 09:00:00")
        self.anchor = vl.photo("2021", "c.jpg", taken="2020:01:01 09:00:00", caption="Anchor & <b>rope</b>",
                               tags=["Trips/Coast", "People/" + WREN])
        self.zebra = vl.photo("2022", "d.jpg", caption="zebra crossing")
        self.harbour_twin = vl.photo("2022", "e.jpg", taken="2019:01:01 09:00:00", caption="Harbour at dawn")
        self.none_b = vl.photo("2022", "f.jpg", taken="2018:01:01 09:00:00")

    def ids(self, kind="all", value=None, order="caption"):
        return library_view.ids(self.vl.library, kind, value, order=order)["ids"]


class TheOrder(Captioned):
    def test_by_caption_without_case_ties_by_id_and_none_after_in_both_directions(self):
        self.assertEqual([self.anchor, self.harbour, self.harbour_twin, self.zebra, self.none_a, self.none_b], self.ids())
        self.assertEqual([self.zebra, self.harbour_twin, self.harbour, self.anchor, self.none_b, self.none_a],
                         self.ids(order="caption-desc"))

    def test_the_keyset_pages_are_the_list_in_both_directions_and_for_a_source(self):
        for order in ("caption", "caption-desc"):
            for kind, value in (("all", None), ("keyword", "Trips"), ("year", "2021")):
                with self.subTest(order=order, kind=kind):
                    self.assertEqual(self.ids(kind, value, order), walk(self.vl, kind, value, order, limit=1))

    def test_a_caption_is_text_not_markup_and_the_first_one_is_the_one_ordered_by(self):
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            found = dict(conn.execute("SELECT id, %s FROM photos p" % store.caption_sql("p.captions")))
        finally:
            conn.close()
        self.assertEqual("Anchor & <b>rope</b>", found[self.anchor])
        self.assertIsNone(found[self.none_a])
        two = self.vl.photo("2023", "g.jpg", fields={"IPTC:Caption-Abstract": "Bay", "XMP:Title": "Aardvark"})
        self.assertEqual(["Bay", "Aardvark"], self.vl.captions_of(two), "two captions, the first the one the page shows")
        listed = self.ids()
        self.assertLess(listed.index(self.anchor), listed.index(two))
        self.assertLess(listed.index(two), listed.index(self.harbour), "by Bay, not by Aardvark")

    def test_text_that_is_not_json_or_an_empty_list_is_no_caption_and_never_an_error(self):
        self.vl.conn.execute("UPDATE photos SET captions = ? WHERE id = ?", ("not json {", self.zebra))
        self.vl.conn.execute("UPDATE photos SET captions = '[]' WHERE id = ?", (self.harbour,))
        self.vl.conn.commit()
        self.assertEqual([self.anchor, self.harbour_twin, self.harbour, self.none_a, self.zebra, self.none_b], self.ids())

    def test_a_long_caption_is_ordered_by_its_first_characters_and_its_token_is_read(self):
        # Not ASCII: each letter is six characters of the token's JSON.
        long_a = self.vl.photo("2023", "h.jpg", caption=chr(0xC5) + chr(0xE9) * (store.CAPTION_KEY + 50) + "b")
        long_b = self.vl.photo("2023", "i.jpg", caption=chr(0xC5) + chr(0xE9) * (store.CAPTION_KEY + 50) + "a")
        listed = self.ids()
        self.assertLess(listed.index(long_a), listed.index(long_b), "alike in their key: by id")
        self.assertEqual(listed, walk(self.vl, "all", order="caption", limit=1))
        self.assertEqual(self.ids(order="caption-desc"), walk(self.vl, "all", order="caption-desc", limit=1))

    def test_a_caption_changed_since_the_list_was_read_moves_when_it_is_read_again(self):
        photo = self.vl.photo("2024", "j.jpg", caption="middle", real=True)
        before = self.ids()
        self.assertLess(before.index(self.harbour_twin), before.index(photo))
        path = self.vl.path_of(photo)
        store_photos.record_saved(self.vl.path, path, [], ["Aaa first now"], {"XMP:Description": "Aaa first now"})
        self.assertEqual(photo, self.ids()[0], "Refresh view reads the order again")

    def test_a_selection_of_a_view_in_caption_order_is_its_source_whatever_the_order(self):
        chosen = selection.read(self.vl.library, {"source": {"kind": "keyword", "value": "Trips/Coast"}, "excluded": []})
        self.assertEqual({self.harbour, self.anchor}, set(selection.resolve(self.vl.library, chosen).ids))
        tallied = selection.tally(self.vl.library, chosen)
        self.assertEqual((2, [WREN]), (tallied["total"], [each["name"] for each in tallied["people"]]))

    def test_without_the_caption_index_the_order_is_the_same(self):
        with_index = (self.ids(), self.ids(order="caption-desc"), walk(self.vl, "all", order="caption", limit=1))
        conn = db.connect(self.vl.path)
        conn.execute("DROP INDEX %s" % store.CAPTION_INDEX)
        conn.commit()
        conn.close()
        self.assertEqual(with_index, (self.ids(), self.ids(order="caption-desc"), walk(self.vl, "all", order="caption", limit=1)))

    def test_a_token_is_read_only_in_its_own_order(self):
        first = library_view.view(self.vl.library, "all", limit=1, order="caption")
        for other in ("caption-desc", "name", "taken"):
            with self.subTest(other=other):
                with self.assertRaises(Refused):
                    library_view.view(self.vl.library, "all", after=first["next"], limit=1, order=other)


class ThePlans(Captioned):
    def plans(self, kind, value, order):
        """{statement of photos: its plan, one text} of what all_ids runs."""
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            statements, _elapsed = store.id_plans(conn, library_view.source_of(self.vl.library, kind, value), 1000, order)
        finally:
            conn.close()
        return {sql: "\n".join(lines) for sql, lines in statements if "FROM photos" in sql}

    def test_the_captioned_walk_the_caption_index_in_order_and_nothing_is_sorted_but_a_sources_uncaptioned_ids(self):
        for kind, value in (("all", None), ("keyword", "Trips"), ("person", WREN)):
            for order in ("caption", "caption-desc"):
                with self.subTest(kind=kind, order=order):
                    plans = self.plans(kind, value, order)
                    self.assertEqual(2, len(plans), "the captioned, then those with none")
                    for sql, plan in plans.items():
                        self.assertIn(store.CAPTION_INDEX, plan)
                        if kind == "all":
                            # Both phases a range of the index: neither computes a caption again from the photo's row.
                            self.assertIn("SEARCH p USING INDEX %s (<expr>" % store.CAPTION_INDEX, plan)
                        if ">= ''" in sql or kind == "all":
                            # The captioned: the index walked in order. Of a source given by a list of ids (a keyword, a
                            # person) the uncaptioned are a seek for each id and a sort of those ids alone.
                            self.assertNotIn("TEMP B-TREE", plan, sql)

    def test_a_keyset_page_seeks_the_caption_index(self):
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        seen = []
        try:
            scope = store._scope(conn, store.Source(store.ALL))
            conn.set_trace_callback(seen.append)
            store._page(conn, scope, store.Cursor(0, "harbour at dawn", self.harbour), 2, store.CAPTION)
            store._page(conn, scope, store.Cursor(1, None, self.none_a), 2, store.CAPTION)
            conn.set_trace_callback(None)
            plans = [" ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)) for sql in seen
                     if "FROM photos" in sql]
        finally:
            conn.close()
        self.assertTrue(all(store.CAPTION_INDEX in plan and "TEMP B-TREE" not in plan for plan in plans), plans)
        self.assertTrue(any("SEARCH p USING INDEX %s (<expr>>?)" % store.CAPTION_INDEX in plan for plan in plans), plans)


class TheRoute(unittest.TestCase):
    def test_the_ids_route_takes_the_caption_orders(self):
        app, home = web_client.app_for(self, "tagpup")
        vl = ViewLibrary(self, "library", home=home)
        b = vl.photo("A", "x.jpg", caption="beta")
        none = vl.photo("A", "y.jpg")
        a = vl.photo("A", "z.jpg", caption="Alpha")
        client = app.test_client()
        for order, expected in (("caption", [a, b, none]), ("caption-desc", [b, a, none])):
            reply = client.get("/library/api/library/ids", query_string={"kind": "all", "order": order})
            self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
            self.assertEqual((expected, order), (reply.get_json()["ids"], reply.get_json()["order"]))


class MigrationTwentyThree(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 22)
        conn = db.connect(self.path)
        try:
            for name, caption in (("b.jpg", "Beach"), ("a.jpg", None), ("c.jpg", "anchor")):
                fields = {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"}
                if caption:
                    fields["XMP:Description"] = caption
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", name), fields)
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def schema_rows(self):
        return look(self.path, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")

    def test_a_library_at_22_gets_the_index_and_nothing_else(self):
        before_schema = self.schema_rows()
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:23]), mock.patch.object(schema, "LATEST", 23):
            self.assertEqual(["photos by caption"], schema.ensure(self.path))
        added = [row for row in self.schema_rows() if row not in before_schema]
        self.assertEqual([("index", store.CAPTION_INDEX, "photos")], [row[:3] for row in added], "one index, no column or trigger")
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables})
        migration = [m for m in schema.MIGRATIONS if m.version == 23][0]
        self.assertEqual((schema.ADDITIVE, ()), (migration.kind, migration.touches))

    def test_a_change_made_at_22_is_still_undoable_after_it(self):
        applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        schema.ensure(self.path)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
        finally:
            conn.close()

    def test_the_index_holds_each_first_caption_and_a_write_of_captions_keeps_it(self):
        schema.ensure(self.path)
        held = look(self.path, "SELECT %s FROM photos INDEXED BY %s WHERE %s IS NOT NULL ORDER BY %s COLLATE NOCASE, id"
                    % ((store.caption_sql(), store.CAPTION_INDEX) + (store.caption_sql(),) * 2))
        self.assertEqual([("anchor",), ("Beach",)], held)
        conn = db.connect(self.path)
        try:
            conn.execute("UPDATE photos SET captions = 'broken [' WHERE captions = '[\"Beach\"]'")
            conn.commit()
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

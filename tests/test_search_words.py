"""A search's words (tagpup.store.search_index; docs/ARCHITECTURE.md, phase 9e-1): two FTS5 tables made by migration 24
and kept by the store's writes -- the words of keywords, captions and people, prefix-matched; the file name and its
folders below the root, by trigram -- every term typed ANDed as a set with the search's sources, nothing typed ever read
as FTS5's syntax.

Photos are rows as the indexer records them (tests/view_library.py: photo_rows.add_read through record_indexed), a face
named through the faces store, a caption changed by a journaled change and undone, a photo deleted by plain SQL as another
program would. Expectations are written out by hand. Fictional names.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
import web_client  # noqa: E402
import photo_rows  # noqa: E402
import roots_library  # noqa: E402
from face_rows import add_face  # noqa: E402
from test_migrations import at_version  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import checks, db, derived, faces, journal, people, schema, search_index, taxonomy  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

WREN, ROWAN, ELODIE = "Wren Halloway", "Rowan Thackeray", "Élodie Marchetti"


def words(text, **parts):
    return json.dumps(dict(parts, words=text))


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class Library(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("Trips/Coast", "Activity/Sailing", "People/" + WREN, "People/" + ROWAN, "People/" + ELODIE, face_root="People")
        self.cafe = vl.photo("2019 Café trip", "20190412_1430.jpg", taken="2019:04:12 14:30:00",
                             tags=["Trips/Coast", "People/" + ELODIE], caption="Harbour wall at dusk")
        self.water = vl.photo("Misc", "IMG_0001.jpg", taken="2020:07:01 10:00:00", tags=["Activity/Sailing"],
                              caption='Out on the "big" water (NOT calm): a-b test')
        self.odd = vl.photo("Beaches", "sandy.jpg", taken="2021:01:01 10:00:00", tags=["Unfiled/Odd one"])
        self.plain = vl.photo("Misc", "c.jpg")

    def found(self, text, order=None, **parts):
        return library_view.ids(self.vl.library, "search", words(text, **parts), order=order)["ids"]

    def every(self):
        return library_view.ids(self.vl.library, "all")["ids"]


class WhatTheWordsFind(Library):
    def test_each_kind_of_text_is_found_by_its_words(self):
        cases = {"harbour": [self.cafe], "0412": [self.cafe], "cafe": [self.cafe], "elodie": [self.cafe],
                 "marchetti": [self.cafe], "coast": [self.cafe], "trips": [self.cafe], "odd": [self.odd],
                 "unfiled": [self.odd], "beach": [self.odd], "sandy": [self.odd], "img_0001": [self.water],
                 "sailing": [self.water], "water": [self.water], "misc": [self.water, self.plain]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.found(text))

    def test_every_term_must_be_found_and_each_is_a_prefix(self):
        self.assertEqual([self.cafe], self.found("harbour dusk"))
        self.assertEqual([], self.found("harbour sailing"))
        self.assertEqual([self.cafe], self.found("harb du"))
        self.assertEqual([self.cafe], self.found("HARBOUR Wall"))

    def test_diacritics_either_way(self):
        for text in ("élodie", "ELODIE", "Élodie", "café", "CAFE"):
            with self.subTest(text=text):
                self.assertEqual([self.cafe], self.found(text))

    def test_nothing_typed_is_fts_syntax_and_nothing_raises(self):
        nasty = ["AND", "OR", "NOT", "NEAR", "NEAR(a b)", "*", "^", '"', '""', ":", "tags:", "tags : harbour", "(", ")", "-",
                 "-harbour", "harbour*", '"harbour', 'a"b', "o'brien", "^harbour", "{tags}", "+", chr(92), "%", "_", "a_b",
                 "harbour OR sailing", "harbour AND", "NOT calm", "*harbour", "harbour)", "((", "''", "-- --", "IMG_00",
                 "x" * 400, chr(0x416) * 3, "🙂", "?!?"]
        for text in nasty:
            with self.subTest(text=text):
                self.assertIsInstance(self.found(text), list)
        self.assertEqual([self.water], self.found('"big"'), "a quote is text")
        self.assertEqual([self.water], self.found("NOT calm"), "NOT is a word the caption holds, not an operator")
        self.assertEqual([], self.found("harbour OR sailing"), "OR is a word: every term must be found")
        self.assertEqual([self.cafe], self.found("-harbour"), "a minus is not a NOT")
        self.assertEqual([], self.found("tags:harbour"), "a column filter is text: no photo holds the word tags")
        self.assertEqual([self.water], self.found("IMG_00"), "inside a file name")

    def test_words_that_say_nothing_ask_nothing(self):
        for text in ("", "   ", "-", "- ( )", "*"):
            with self.subTest(text=text):
                self.assertEqual(self.every(), self.found(text))

    def test_too_many_words_are_refused_with_a_sentence(self):
        with self.assertRaises(Refused):
            self.found(" ".join("w%d" % n for n in range(search_index.MAX_TERMS + 1)))

    def test_words_with_sources_and_in_every_order_page_as_their_list(self):
        self.vl.photo("Misc", "harbour_b.jpg", taken="2018:01:01 10:00:00", caption="the harbour again")
        self.vl.photo("Misc", "harbour_a.jpg", caption="harbour, undated")
        for order in store.ORDERS:
            with self.subTest(order=order):
                value = words("harbour", none_of=[{"kind": "month", "value": "2018-01"}])
                listed = library_view.ids(self.vl.library, "search", value, order=order)
                token, paged = None, []
                while True:
                    page = library_view.view(self.vl.library, "search", value, False, token, 1, order)
                    paged += page["ids"]
                    token = page["next"]
                    if token is None:
                        break
                self.assertEqual(listed["ids"], paged)
                self.assertEqual(2, listed["total"])
        within = self.found("misc", all_of=[{"kind": "year", "value": "2020"}])
        self.assertEqual([self.water], within)

    def test_a_selection_by_a_search_of_words_tallies_it(self):
        chosen = selection.read(self.vl.library, {"source": {"kind": "search", "value": {"words": "misc"}}, "excluded": []})
        self.assertEqual([self.water, self.plain], list(selection.resolve(self.vl.library, chosen).ids))
        self.assertEqual(2, selection.tally(self.vl.library, chosen)["total"])


class TheWordsFollowTheWrites(Library):
    def test_a_photo_read_again_with_other_keywords(self):
        photo_rows.add_read(self.vl.conn, os.path.join(self.vl.pictures, "Misc", "IMG_0001.jpg"),
                            {"XMP:Subject": ["Trips/Coast"], "EXIF:DateTimeOriginal": "2020:07:01 10:00:00"})
        self.vl.conn.commit()
        self.assertEqual([], self.found("sailing"))
        self.assertEqual([self.cafe, self.water], self.found("coast"))
        self.assertEqual([], self.found("water"), "the caption the file no longer holds")

    def test_a_face_named_makes_its_person_a_word_of_the_photo(self):
        face = add_face(self.vl.conn, self.vl.path_of(self.plain))
        self.vl.conn.commit()
        self.assertEqual([], self.found("thackeray"))
        faces.name(self.vl.conn, [face], ROWAN)
        self.vl.conn.commit()
        self.assertEqual([self.plain], self.found("thackeray"))
        faces.unname(self.vl.conn, [face])
        self.vl.conn.commit()
        self.assertEqual([], self.found("thackeray"))

    def test_a_caption_changed_by_a_journaled_change_and_its_undo(self):
        held = self.vl.rows("SELECT captions FROM photos WHERE id = ?", self.plain)[0][0]
        applied = journal.apply(self.vl.path, "caption", [journal.update(
            "photos", (self.plain,), {"captions": held}, {"captions": json.dumps(["Lighthouse keeper"])})])
        self.assertEqual([self.plain], self.found("lighthouse"))
        journal.undo(self.vl.path, applied.change_id)
        self.assertEqual([], self.found("lighthouse"))

    def test_a_photo_deleted_by_another_program_takes_its_words(self):
        conn = db.connect(self.vl.path)
        try:
            conn.execute("DELETE FROM photos WHERE id = ?", (self.cafe,))
            conn.commit()
            self.assertEqual([], search_index.stale(conn, sample=None))
        finally:
            conn.close()
        self.assertEqual([], self.found("harbour"))

    def test_a_tag_renamed_in_the_tree_is_found_by_the_words_the_file_holds(self):
        # The words are the keyword as the file spells it: until TagTuner rewrites the files the old words find the
        # photo and the new do not; the photo's keyword rows (photo_tags) name no node meanwhile, as the navigator shows.
        with people.tree_edit(self.vl.conn):
            taxonomy.move_branch(self.vl.conn, "Trips/Coast", "Trips/Shore")
        self.vl.conn.commit()
        self.assertEqual([self.cafe], self.found("coast"))
        self.assertEqual([], self.found("shore"))
        photo_rows.add_read(self.vl.conn, self.vl.path_of(self.cafe), {"XMP:Subject": ["Trips/Shore"]})
        self.vl.conn.commit()
        self.assertEqual([self.cafe], self.found("shore"))
        self.assertEqual([], self.found("coast"))

    def test_two_libraries_holding_one_folder_each_find_their_own_words(self):
        other = ViewLibrary(self, "lighthouse", home=self.vl.home)
        theirs = other.photo("Misc", "IMG_0001.jpg", at=self.vl.path_of(self.water), caption="Gulls on the pier")
        self.assertEqual([theirs], library_view.ids(other.library, "search", words("gulls"))["ids"])
        self.assertEqual([], self.found("gulls"))
        self.assertEqual([], library_view.ids(other.library, "search", words("water"))["ids"])


class TheRootsName(unittest.TestCase):
    def test_the_folder_text_is_below_the_root_drive_or_share(self):
        self.assertEqual("2024/Coast", search_index.folder_text("@pictures/2024/Coast/x.jpg"))
        self.assertEqual("", search_index.folder_text("@pictures/x.jpg"))
        if os.name == "nt":
            # A native row: the folder it is in, never the machine's layout above it, a drive or a share.
            self.assertEqual("B", search_index.folder_text("D:" + chr(92) + "A" + chr(92) + "B" + chr(92) + "x.jpg"))
            self.assertEqual("", search_index.folder_text("D:" + chr(92) + "x.jpg"))
            unc = chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "Trips" + chr(92) + "x.jpg"
            self.assertEqual("Trips", search_index.folder_text(unc))
            self.assertEqual("", search_index.folder_text(chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "x.jpg"))

    def test_an_adopted_librarys_root_name_is_never_a_word(self):
        home = own_home.for_test(self)
        side = roots_library.Side(home, "sea", real=1, bulk=2, outside=0)
        self.assertEqual(3, len(library_view.ids(side.library, "search", words("regatta"))["ids"]))
        side.adopt()
        self.assertTrue(side.rows("SELECT path FROM photos")[0][0].startswith("@" + roots_library.NAME + "/"))
        self.assertEqual([], library_view.ids(side.library, "search", words(roots_library.NAME))["ids"],
                         "the root's name is in every row and is never a word")
        self.assertEqual(3, len(library_view.ids(side.library, "search", words("regatta"))["ids"]))
        self.assertEqual(3, len(library_view.ids(side.library, "search", words("trips coast"))["ids"]),
                         "every folder below the root")


class TheDoctor(Library):
    def test_a_row_missing_or_out_of_step_is_found_and_the_repair_mends_it(self):
        # A caption that is only a dash has no word to match (photo_index holds 255 such rows): in step, not stale.
        self.vl.photo("Misc", "dash.jpg", caption="-")
        self.assertEqual(0, checks.search_index_out_of_date(self.vl.conn).count)
        self.vl.conn.execute("DELETE FROM search_words WHERE rowid = ?", (self.cafe,))
        self.vl.conn.execute("INSERT OR REPLACE INTO search_names (rowid, name, folders) VALUES (?, 'other.jpg', '')",
                             (self.water,))
        self.vl.conn.commit()
        found = checks.search_index_out_of_date(self.vl.conn)
        self.assertEqual((2, [self.cafe, self.water]), (found.count, found.examples))
        before, _written, after = derived.repair(self.vl.path)
        self.assertTrue(before)
        self.assertEqual([], after)
        self.assertEqual([self.cafe], self.found("harbour"))


    def test_the_report_says_what_the_rule_cannot_see_and_the_remedy_752(self):
        # #752: a sample of 500, and a word left behind by a version of the app that does not know the index is never seen.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        lines = []
        doctor.report(self.vl.path, out=lines.append)
        said = "\n".join(lines)
        self.assertIn("sample of %d" % search_index.SAMPLE, said)
        self.assertIn("not seen", said)
        self.assertIn("--rebuild-derived --apply", said)
        self.vl.conn.execute("DELETE FROM search_words WHERE rowid = ?", (self.cafe,))
        self.vl.conn.commit()
        self.assertIn("--rebuild-derived --apply", search_index.problems(self.vl.conn)[0])


class WhileTheIndexIsBeingMade(unittest.TestCase):
    """#753: a library below migration 24 is one whose migration is under way or due -- the server brings every library
    it serves up to date as it starts and as a request first names it -- so a search of its words is asked again in a few
    seconds (503, Retry-After), never told to open the library in TagPup."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup", startup=None)
        self.path = self.home.library("library.db")
        at_version(self.path, 23)
        schema._current.clear()
        # The startup thread holds the library, migrating it: the request's own bringing up to date waits, then gives up.
        held = mock.patch("tagpup.services.libraries.bring_up_to_date",
                          side_effect=db.sqlite3.OperationalError("database is locked"))
        held.start()
        self.addCleanup(held.stop)

    def test_a_search_of_words_is_answered_try_again_in_a_moment(self):
        client = self.app.test_client()
        for method in ("get", "post"):
            with self.subTest(method=method):
                if method == "get":
                    reply = client.get("/library/api/library/ids", query_string={"kind": "search", "value": words("harbour")})
                else:
                    reply = client.post("/library/api/library/ids", json={"kind": "search", "value": {"words": "harbour"}})
                self.assertEqual(503, reply.status_code, reply.get_data(as_text=True))
                self.assertEqual("5", reply.headers.get("Retry-After"))
                self.assertIn("being made now", reply.get_json()["error"])
                self.assertNotIn("Open it in TagPup", reply.get_json()["error"])
        structured = client.post("/library/api/library/ids", json={"kind": "search", "value": {"none_of": [
            {"kind": "year", "value": "1900"}]}})
        self.assertEqual(200, structured.status_code, "a search without words does not wait for the index")

    def test_at_24_with_no_tables_it_is_not_a_wait(self):
        conn = db.connect(self.path)
        try:
            conn.execute("INSERT INTO schema_version VALUES (24, 'photos by their words', '2026-10-04 00:00:00')")
            conn.commit()
        finally:
            conn.close()
        reply = self.app.test_client().post("/library/api/library/ids", json={"kind": "search", "value": {"words": "x"}})
        self.assertEqual(400, reply.status_code)
        self.assertNotIn("being made now", reply.get_json()["error"])


class MigrationTwentyFour(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 23)
        conn = db.connect(self.path)
        try:
            for name, caption in (("b.jpg", "Beach day"), ("a.jpg", None), ("Café.jpg", "anchor")):
                fields = {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00", "XMP:Subject": ["Trips/Coast"]}
                if caption:
                    fields["XMP:Description"] = caption
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", "2024 Coast", name), fields)
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def migrate(self):
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:24]), mock.patch.object(schema, "LATEST", 24):
            return schema.ensure(self.path)

    def schema_rows(self):
        return look(self.path, "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name")

    def test_a_library_at_23_gets_the_two_tables_their_shadows_and_the_trigger_and_nothing_else(self):
        before_schema = self.schema_rows()
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        self.assertEqual(migration_names.named(24), self.migrate())
        added = sorted(row for row in self.schema_rows() if row not in before_schema)
        expected = sorted([("table", name, name) for name in search_index.TABLES + search_index.SHADOWS]
                          + [("trigger", "search_goes_with_its_photo", "photos")])
        self.assertEqual(expected, added, "the FTS5 tables and their shadows, and one trigger: no column, no index")
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables},
                         "every row of every table that was there is unchanged")
        migration = [m for m in schema.MIGRATIONS if m.version == 24][0]
        self.assertEqual((schema.ADDITIVE, search_index.TABLES), (migration.kind, migration.touches))
        self.assertLessEqual(set(search_index.TABLES + search_index.SHADOWS), set(schema.UNWATCHED),
                             "a later migration's watch makes no trigger on them")
        self.assertEqual(look(self.path, "SELECT COUNT(*) FROM photos"), look(self.path, "SELECT COUNT(*) FROM search_words"))
        conn = db.connect(self.path)
        try:
            self.assertEqual([], search_index.stale(conn, sample=None))
        finally:
            conn.close()
        library = ViewLibrary.__new__(ViewLibrary)
        from tagpup.core.library import Library as Lib
        library.library = Lib(self.path)
        self.assertEqual(1, len(library_view.ids(library.library, "search", words("cafe"))["ids"]))
        self.assertEqual(1, len(library_view.ids(library.library, "search", words("beach"))["ids"]))

    def test_a_change_made_at_23_is_still_undoable_after_it(self):
        # Made at 23, by the version of the app that knew 23: the journal opens the library through schema.ensure.
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:23]), mock.patch.object(schema, "LATEST", 23):
            applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        self.migrate()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
        finally:
            conn.close()
        journal.undo(self.path, applied.change_id)
        self.assertEqual([], look(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))

    def test_interrupted_it_leaves_the_library_at_23_and_runs_again(self):
        real = search_index.rebuild

        def halfway(conn):
            conn.execute("INSERT INTO search_words (rowid, tags) VALUES (1, 'partial')")
            raise RuntimeError("the power went")

        with mock.patch.object(search_index, "rebuild", side_effect=halfway):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertEqual(23, look(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual([], look(self.path, "SELECT name FROM sqlite_master WHERE name LIKE 'search%'"))
        schema._current.clear()
        with mock.patch.object(search_index, "rebuild", side_effect=real):
            self.assertEqual(migration_names.named(24), self.migrate())
        self.assertEqual(look(self.path, "SELECT COUNT(*) FROM photos"), look(self.path, "SELECT COUNT(*) FROM search_words"))


if __name__ == "__main__":
    unittest.main()

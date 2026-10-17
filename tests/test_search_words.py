"""A search's words (tagpup.store.search_index; docs/ARCHITECTURE.md, phase 9e-1): two FTS5 tables made with the library (the baseline)
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library  # noqa: E402
from face_rows import add_face  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import checks, db, derived, faces, journal, people, search_index, taxonomy  # noqa: E402
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

    def test_words_with_sources_are_found_in_every_order(self):
        self.vl.photo("Misc", "harbour_b.jpg", taken="2018:01:01 10:00:00", caption="the harbour again")
        self.vl.photo("Misc", "harbour_a.jpg", caption="harbour, undated")
        for order in store.ORDERS:
            with self.subTest(order=order):
                value = words("harbour", none_of=[{"kind": "month", "value": "2018-01"}])
                listed = library_view.ids(self.vl.library, "search", value, order=order)
                self.assertEqual((2, 2), (len(listed["ids"]), listed["total"]))
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


if __name__ == "__main__":
    unittest.main()

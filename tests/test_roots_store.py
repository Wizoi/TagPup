"""The store converts a library's paths at its boundary (tagpup.store.roots;
docs/ARCHITECTURE.md, "Roots and machines"): native in memory, root-relative in the database.

A library with no roots is read and written exactly as it always was; one that has a root
holds a photo's path as `@pictures/2024/a.jpg`, and every read of a path column comes back
native and every write converts. Here the rows are what the indexer and Suggest make
(tests/photo_rows.py, tests/roots_library.py), converted by the explicit command, and what
is asked of them is the store's answers: the same as an unconverted library's, once each
library's own folder is taken out of them.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import machine, paths  # noqa: E402
from tagpup.store import db, faces, folders, inspection, journal  # noqa: E402
from tagpup.store import added_folders, damaged_files, embeddings, file_journal  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402
from tagpup.store import settings as store_settings  # noqa: E402
from tagpup.store import suggestions  # noqa: E402

WINDOWS = os.name == "nt"


class TwoLibraries(unittest.TestCase):
    """The same photos in two libraries, in folders of their own: one adopted by a root."""

    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls, prefix="roots_store_")
        cls.plain = rl.Side(cls.home, "plain")
        cls.rooted = rl.Side(cls.home, "rooted")
        #: The journal of files, made before anything was adopted, with native paths.
        cls.changes = {}
        if rl.EXIFTOOL:
            for side in (cls.plain, cls.rooted):
                cls.changes[side.label] = side.tagged_before().details["change"]
        result = cls.rooted.adopt()
        assert result.ok and not result.refused, result.message()

    def both(self, ask):
        """What `ask(conn, side)` answers on each library, each with its own folder taken out."""
        found = []
        for side in (self.plain, self.rooted):
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                found.append(side.norm(ask(conn, side)))
            finally:
                conn.close()
        return found

    def same(self, ask):
        plain, rooted = self.both(ask)
        self.assertEqual(plain, rooted)
        return plain


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhatTheLibraryHolds(TwoLibraries):
    def test_a_photo_under_the_root_is_held_as_its_row_and_one_outside_is_native(self):
        held = self.rooted.raw_paths()
        self.assertEqual(96, sum(1 for path in held if path.startswith("@pictures/")))
        self.assertEqual(sorted(self.rooted.outside), sorted(path for path in held if not path.startswith("@")))
        self.assertEqual([], [path for path in self.plain.raw_paths() if path.startswith("@")])

    def test_the_roots_table_holds_the_root_and_nothing_else_changed_in_it(self):
        self.assertEqual([("pictures", rl.ADDRESS)], [(r["name"], r["address"]) for r in self.rooted.library_roots()])
        self.assertEqual([], self.plain.library_roots())

    def test_every_other_table_with_a_path_is_converted(self):
        self.assertEqual({"@pictures/2024 Harbour/Cut short.jpg", "@pictures/2024 Regatta/Cut short.jpg"},
                         set(self.rooted.raw_paths("damaged_files")))
        self.assertEqual(["@pictures/Added later"], self.rooted.raw_paths("added_folders"))
        if rl.EXIFTOOL:
            self.assertEqual(["@pictures/2024 Regatta/IMG_1001.jpg", "@pictures/2024 Regatta/IMG_1002.jpg"],
                             self.rooted.raw_paths("change_files"))
            self.assertEqual(self.plain.norm(sorted(self.plain.raw_paths("change_files"))),
                             ["<BASE>\\Pictures\\2024 Regatta\\IMG_1001.jpg", "<BASE>\\Pictures\\2024 Regatta\\IMG_1002.jpg"])

    def test_the_folder_settings_are_held_as_rows_and_read_native(self):
        held = dict(self.rooted.rows("SELECT key, value FROM settings WHERE key IN ('library.roots', 'library.ignored')"))
        self.assertEqual({"library.roots": "@pictures", "library.ignored": "@pictures/Not these"}, held)
        read = store_settings.read(self.rooted.db_path)
        self.assertEqual(self.rooted.pictures, read["library.roots"])
        self.assertEqual(os.path.join(self.rooted.pictures, "Not these"), read["library.ignored"])
        self.assertEqual(os.path.join(self.plain.pictures, "Not these"), store_settings.read(self.plain.db_path)["library.ignored"])

    def test_a_suggestions_paths_and_a_photos_source_file_are_held_as_rows(self):
        raw = json.loads(self.rooted.rows("SELECT raw FROM suggestions")[0][0])
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", raw["path"])
        self.assertEqual("@pictures/2024 Regatta/Earlier_1001.jpg", raw["nearest_neighbors"][0]["path"])
        source = json.loads(self.rooted.rows("SELECT raw_metadata FROM photos WHERE path = ?",
                                             ("@pictures/2024 Harbour/Earlier_2001.jpg",))[0][0])["SourceFile"]
        self.assertEqual("@pictures/2024 Harbour/Earlier_2001.jpg", source)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class EveryReadAnswersNative(TwoLibraries):
    """The same question of the two libraries, the same answer."""

    def test_the_photos_under_a_folder(self):
        for folder in ("", "2024 Regatta", os.path.join("Trips", "2025 Coast")):
            rows = self.same(lambda conn, side: sorted(store_photos.rows_under(conn, os.path.join(side.pictures, folder))))
            self.assertTrue(rows)

    def test_the_folders_the_library_holds(self):
        found = self.same(lambda conn, side: sorted(folders.of(conn).listed()))
        self.assertEqual(5, len(found))

    def test_stamps_and_rows_to_check_and_the_index_rows(self):
        self.same(lambda conn, side: sorted(store_photos.stamps(conn)))
        self.same(lambda conn, side: sorted(store_photos.stamps(conn, os.path.join(side.pictures, "2024 Harbour"))))
        self.same(lambda conn, side: sorted(store_photos.rows_to_check(conn)))
        self.same(lambda conn, side: sorted(store_photos.index_rows(conn, "a model"), key=lambda r: r[0]))
        self.same(lambda conn, side: sorted(store_photos.records(conn, "a model")))

    def test_a_rows_raw_metadata_is_what_its_file_would_say(self):
        """The source file in it is the one ExifTool gives, here, whichever library: a row is
        compared with a fresh read of its file by it (tagpup.services.refresh_rows)."""
        for side in (self.plain, self.rooted):
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                path = side.rows_only[3]
                raw = json.loads(store_photos.row_as_recorded(conn, path)[3])
                self.assertEqual(path.replace(os.sep, "/"), raw["SourceFile"], side.label)
            finally:
                conn.close()

    def test_every_lookup_by_a_path_finds_the_same_rows(self):
        def ask(conn, side):
            path = side.rows_only[7]
            return (store_photos.details(conn, path), store_photos.rows_of(conn, [path]),
                    store_photos.stored_spelling(conn, path), store_photos.row_as_recorded(conn, path) is not None,
                    faces.count_for_photo(conn, side.real[0]), faces.counts_on(conn, side.real[0]),
                    inspection.ids_of_stored(conn, [path]), inspection.ids_of_files(conn, [path, side.real[0]]),
                    folders.holds(conn, os.path.dirname(path)),
                    embeddings.stamps_by_path(conn, [path]), store_photos.count_under(conn, side.pictures))
        self.same(ask)

    def test_a_lookup_in_another_case_finds_the_row(self):
        for side in (self.plain, self.rooted):
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                upper = side.real[0].upper()
                self.assertIsNotNone(store_photos.details(conn, upper), side.label)
            finally:
                conn.close()

    def test_faces(self):
        def ask(conn, side):
            return (sorted(faces.for_clustering(conn)), sorted(faces.photos_with_faces(conn)),
                    sorted(faces.names_by_photo(conn).items(), key=lambda kv: kv[0]),
                    sorted(faces.names_by_photo_key(conn).items(), key=lambda kv: kv[0]),
                    sorted(faces.decisions(conn)), faces.unnamed(conn, folder=side.pictures),
                    sorted(faces.unnamed_counts(conn, side.pictures).items()),
                    sorted(faces.identify_candidates(conn)), faces.busiest_photo(conn) is not None,
                    sorted(faces.photos_with_unnamed(conn, every=True)), sorted(faces.named_for_known(conn)),
                    sorted(faces.person_page(conn, "Rowan Thackeray", 50, 0)),
                    faces.rows(conn, [1, 2, 3]), faces.unnamed(conn, photo_path=side.real[0]))
        self.same(ask)

    def test_the_added_and_ignored_folders(self):
        def ask(conn, side):
            return (added_folders.every(conn), added_folders.covers(conn, os.path.join(side.pictures, "Added later", "x")),
                    folders.ignored(conn))
        self.same(ask)

    def test_damaged_files_come_in_the_order_they_always_did(self):
        def ask(conn, side):
            return ([r.path for r in damaged_files.every(conn)],
                    [r.path for r in damaged_files.under(conn, side.pictures)],
                    [r.path for r in damaged_files.under(conn, os.path.join(side.pictures, "2024 Harbour"))])
        listed = self.same(ask)
        self.assertEqual(sorted(listed[0], key=str.lower), listed[0])

    def test_suggestions_in_a_folder_carry_native_paths(self):
        found = self.same(lambda conn, side: suggestions.in_folder(conn, side.pictures))
        (path, entry), = found.items()
        self.assertEqual(path, entry["raw_suggestions"]["path"])
        self.assertTrue(entry["raw_suggestions"]["nearest_neighbors"][0]["path"].startswith("<BASE>"))

    def test_the_whole_library_read_row_by_row(self):
        self.same(lambda conn, side: sorted(store_photos.all_paths(conn)))
        self.same(lambda conn, side: sorted(store_photos.identities(conn).items()))
        self.same(lambda conn, side: sorted(store_photos.tags_by_photo(conn)))
        self.same(lambda conn, side: store_photos.with_tag(conn, "Activity/Sailing"))
        self.same(lambda conn, side: sorted(inspection.ids_and_paths(conn), key=lambda r: r[0]))
        self.same(lambda conn, side: sorted(inspection.whose_file_is_gone(conn)))

    def test_the_order_of_a_listing_is_the_order_the_owner_has_always_seen(self):
        for side in (self.plain, self.rooted):
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                listed = [path for _id, path in inspection.ids_and_paths(conn)]
                self.assertEqual(sorted(listed, key=str.lower), listed, side.label)
            finally:
                conn.close()

    @unittest.skipUnless(rl.EXIFTOOL, "ExifTool not installed")
    def test_the_journal_of_files_reads_native(self):
        found = {}
        for side in (self.plain, self.rooted):
            files = file_journal.files_of(side.db_path, self.changes[side.label])
            found[side.label] = side.norm([(f.path, f.new_path, f.state) for f in files])
        self.assertEqual(found["plain"], found["rooted"])
        self.assertEqual(2, len(found["plain"]))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class EveryWriteConverts(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_writes_")
        self.side = rl.Side(self.home, "rooted", real=1, bulk=3, outside=1)
        self.assertTrue(self.side.adopt().ok)
        self.path = os.path.join(self.side.pictures, "2024 Regatta", "New photo.jpg")
        self.elsewhere = os.path.join(self.side.outside_folder, "New loose.jpg")

    def write(self, work):
        return db.write_with_connection(self.side.db_path, work)

    def held(self):
        return self.side.raw_paths()

    def test_the_indexer_records_a_photo_under_the_root_as_its_row(self):
        self.write(lambda conn: photo_rows.add_read(conn, self.path, rl.read(self.path)))
        self.assertIn("@pictures/2024 Regatta/New photo.jpg", self.held())
        self.assertNotIn(self.path, self.held())

    def test_a_photo_outside_every_root_is_recorded_native(self):
        self.write(lambda conn: photo_rows.add_read(conn, self.elsewhere, rl.read(self.elsewhere)))
        self.assertIn(self.elsewhere, self.held())

    def test_recording_it_again_updates_the_row_it_has(self):
        self.write(lambda conn: photo_rows.add_read(conn, self.path, rl.read(self.path)))
        before = self.side.rows("SELECT id FROM photos WHERE path LIKE '%New photo%'")
        self.write(lambda conn: photo_rows.add_read(conn, self.path.upper(), dict(rl.read(self.path), **{"XMP:Subject": ["Other"]})))
        self.assertEqual(before, self.side.rows("SELECT id FROM photos WHERE path LIKE '%New photo%'"))
        self.assertEqual(1, len(self.side.rows("SELECT id FROM photos WHERE path LIKE '%New photo%' COLLATE NOCASE")))

    def test_a_path_already_in_row_form_is_written_as_it_is(self):
        conn = db.connect(self.side.db_path)
        try:
            row = store_roots.to_row(conn, os.path.join(self.side.pictures, "a", "b.jpg"))
            self.assertEqual("@pictures/a/b.jpg", row)
            roots = store_roots.roots_for(conn)
            self.assertEqual(row, store_roots.row_value(roots, "photos", "path", row))
        finally:
            conn.close()

    def test_renaming_a_row_moves_it_by_its_converted_path(self):
        old = self.side.real[0]
        new = os.path.join(self.side.pictures, "2024 Regatta", "Renamed.jpg")
        moved, skipped = store_photos.move_rows(self.side.db_path, {old: new})
        self.assertEqual((1, []), (moved, skipped))
        self.assertIn("@pictures/2024 Regatta/Renamed.jpg", self.held())
        self.assertNotIn("@pictures/2024 Regatta/IMG_1001.jpg", self.held())

    def test_a_row_moved_out_of_the_root_becomes_native_and_back(self):
        old = self.side.real[0]
        store_photos.move_rows(self.side.db_path, {old: self.elsewhere})
        self.assertIn(self.elsewhere, self.held())
        store_photos.move_rows(self.side.db_path, {self.elsewhere: old})
        self.assertIn("@pictures/2024 Regatta/IMG_1001.jpg", self.held())

    def test_forgetting_a_photo_finds_its_row_and_its_faces(self):
        removed = store_photos.forget_photo(self.side.db_path, self.side.real[0])
        self.assertEqual(1, removed["photos"])
        self.assertEqual(1, removed["faces"])

    def test_removing_a_folder_takes_its_rows_its_added_record_and_its_damaged_ones(self):
        folder = os.path.join(self.side.pictures, "2024 Regatta")
        removed = self.write(lambda conn: store_photos.remove_under(conn, folder))
        self.assertEqual(4, removed["photos_removed"])
        self.assertEqual([], [p for p in self.held() if p.startswith("@pictures/2024 Regatta")])
        self.assertEqual(["@pictures/2024 Harbour/Cut short.jpg"], self.side.raw_paths("damaged_files"))

    def test_a_damaged_file_is_recorded_as_its_row(self):
        path = os.path.join(self.side.pictures, "2025", "Cut.jpg")
        new = self.write(lambda conn: damaged_files.record(conn, path, (1.0, 5), "empty", "no bytes"))
        self.assertTrue(new)
        self.assertIn("@pictures/2025/Cut.jpg", self.side.raw_paths("damaged_files"))
        self.assertFalse(self.write(lambda conn: damaged_files.record(conn, path, (1.0, 5), "empty", "no bytes")))

    def test_an_added_folder_is_recorded_as_its_row(self):
        self.write(lambda conn: added_folders.record(conn, os.path.join(self.side.pictures, "More", "Deeper")))
        self.assertIn("@pictures/More/Deeper", self.side.raw_paths("added_folders"))

    def test_suggest_writes_a_suggestion_with_row_paths(self):
        found = {"tags": [], "people": [], "title": "x", "raw_before_consensus": False,
                 "raw_suggestions": {"path": self.side.real[0], "nearest_neighbors": [
                     {"path": self.side.rows_only[0], "similarity": 0.5}, {"path": self.elsewhere, "similarity": 0.4}]}}
        self.write(lambda conn: suggestions.put(conn, self.side.real[0], found))
        raw = json.loads(self.side.rows("SELECT raw FROM suggestions")[0][0])
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", raw["path"])
        self.assertEqual("@pictures/2024 Regatta/Earlier_1001.jpg", raw["nearest_neighbors"][0]["path"])
        self.assertEqual(self.elsewhere, raw["nearest_neighbors"][1]["path"], "outside every root: native")
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            back = suggestions.in_folder(conn, self.side.pictures)[self.side.real[0]]
        finally:
            conn.close()
        self.assertEqual(found["raw_suggestions"], back["raw_suggestions"])

    def test_a_save_stamps_the_row_and_keeps_its_raw_metadata_source_file(self):
        path = self.side.real[0]
        raw = {"SourceFile": path.replace(os.sep, "/"), "XMP:Subject": ["A"], "Subject": ["A"]}
        store_photos.record_saved(self.side.db_path, path, ["A"], [], raw)
        held = json.loads(self.side.rows("SELECT raw_metadata FROM photos WHERE path = ?",
                                         ("@pictures/2024 Regatta/IMG_1001.jpg",))[0][0])
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", held["SourceFile"])
        _tags, _meta = store_photos.read_tags(self.side.db_path, [path])[0][1:]
        self.assertEqual(path.replace(os.sep, "/"), _meta["SourceFile"])

    def test_a_source_file_that_would_not_come_back_exact_is_left_as_it_is(self):
        roots = machine.roots_of({"pictures": rl.ADDRESS})
        native = self.side.real[0]
        for source in (native, native.lower().replace(os.sep, "/"), "somewhere/else.jpg", "", None, 5):
            self.assertEqual(source, store_roots.source_to_row(source, roots), repr(source))
        forward = native.replace(os.sep, "/")
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", store_roots.source_to_row(forward, roots))
        self.assertEqual(forward, store_roots.source_from_row("@pictures/2024 Regatta/IMG_1001.jpg", roots))

    def test_a_raw_metadata_text_converts_only_its_source_file(self):
        roots = machine.roots_of({"pictures": rl.ADDRESS})
        forward = self.side.real[0].replace(os.sep, "/")
        text = json.dumps({"A": "café SourceFile", "SourceFile": forward, "XMP:Subject": ["x"]})
        row = store_roots.raw_to_row(text, roots)
        self.assertEqual({"A": "café SourceFile", "SourceFile": "@pictures/2024 Regatta/IMG_1001.jpg",
                          "XMP:Subject": ["x"]}, json.loads(row))
        self.assertEqual(json.loads(text), json.loads(store_roots.raw_to_native(row, roots)))
        self.assertEqual("{}", store_roots.raw_to_row("{}", roots))
        self.assertEqual("not json", store_roots.raw_to_row("not json", roots))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class HowAConnectionHoldsItsRoots(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_held_")
        self.side = rl.Side(self.home, "rooted", real=1, bulk=2, outside=0)
        self.asked = []
        real = config.roots_of

        def counting(logical, path=None):
            self.asked.append(dict(logical))
            return real(logical, path)

        patch = mock.patch.object(config, "roots_of", counting)
        patch.start()
        self.addCleanup(patch.stop)
        machine.provide(counting)
        self.addCleanup(machine.provide, real)

    def test_a_library_with_no_roots_never_asks_the_map(self):
        conn = db.connect(self.side.db_path)
        try:
            for _ in range(50):
                store_roots.sql_equals(conn, "path", self.side.real[0])
            self.assertTrue(store_roots.roots_for(conn).identity)
        finally:
            conn.close()
        self.assertEqual([], self.asked)

    def test_a_connection_asks_the_map_once_however_many_paths_it_converts(self):
        self.side.adopt()
        self.asked.clear()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            for _ in range(300):
                store_photos.details(conn, self.side.real[0])
                store_roots.from_row(conn, "@pictures/a.jpg")
        finally:
            conn.close()
        self.assertEqual(1, len(self.asked))

    def test_an_operation_on_a_connection_of_its_own_asks_once(self):
        self.side.adopt()
        self.asked.clear()
        db.write_with_connection(self.side.db_path, lambda conn: [store_photos.rows_of(conn, [p]) for p in self.side.real * 40])
        self.assertEqual(1, len(self.asked))

    def test_roots_changed_by_another_connection_are_read_again(self):
        conn = db.connect(self.side.db_path)
        try:
            self.assertTrue(store_roots.roots_for(conn).identity)
            self.side.adopt()
            self.assertFalse(store_roots.roots_for(conn).identity)
            self.assertEqual("@pictures/x.jpg", store_roots.to_row(conn, os.path.join(self.side.pictures, "x.jpg")))
        finally:
            conn.close()

    def test_inside_a_transaction_the_roots_are_the_ones_it_began_with(self):
        self.side.adopt()
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            first = store_roots.roots_for(conn)
            self.edit_map(os.path.join(self.home.root, "moved"))
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                self.assertIs(first, store_roots.roots_for(conn))
            conn.rollback()
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                self.assertIsNot(first, store_roots.roots_for(conn))
        finally:
            conn.close()

    def edit_map(self, location):
        """The owner moves the root: the new place first, the old one kept."""
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [location, self.side.pictures]}}, handle)

    def test_an_idle_connection_finds_a_map_edited_meanwhile(self):
        self.side.adopt()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(self.side.pictures, store_roots.from_row(conn, "@pictures"))
            moved = os.path.join(self.home.root, "moved")
            self.edit_map(moved)
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                self.assertEqual(moved, store_roots.from_row(conn, "@pictures"))
        finally:
            conn.close()

    def test_a_run_that_pins_the_roots_writes_every_row_under_one_map(self):
        """The owner edits the map -- the old place dropped -- while a run is half through its
        photos: pinned, every row it writes is under the root; without, the second write is
        converted by the map as it stands, and a row for a file at the old place is under no root."""
        self.side.adopt()
        moved = os.path.join(self.home.root, "moved")
        first = os.path.join(self.side.pictures, "2024 Regatta", "Run first.jpg")
        second = os.path.join(self.side.pictures, "2024 Regatta", "Run second.jpg")
        third = os.path.join(self.side.pictures, "2024 Regatta", "Run third.jpg")

        def record(path):
            db.write_with_connection(self.side.db_path, lambda conn: photo_rows.add_read(conn, path, rl.read(path)))

        with store_roots.pinned(self.side.db_path):
            record(first)
            with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "roots": {"pictures": [moved]}}, handle)
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                record(second)
        held = self.side.raw_paths()
        self.assertIn("@pictures/2024 Regatta/Run first.jpg", held)
        self.assertIn("@pictures/2024 Regatta/Run second.jpg", held)
        with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
            record(third)
        self.assertIn(third, self.side.raw_paths(), "unpinned, the map as it stands: a file at the dropped place is under no root")

    def test_one_journaled_change_asks_the_map_once_however_many_rows_it_writes(self):
        from tagpup.store import journal
        self.side.adopt()
        self.asked.clear()
        ids = [row[0] for row in self.side.rows("SELECT id FROM photos WHERE path LIKE '@pictures/2024 Regatta/%'")]
        edits = [journal.update("photos", (photo_id,), {}, {"tags": '["Same"]'}) for photo_id in ids]
        applied = journal.apply(self.side.db_path, "tag them", edits)
        self.assertEqual(len(ids), applied.changed)
        self.assertEqual(1, len(self.asked), "a change holds one Roots for its whole length")

    def test_an_index_open_while_the_library_is_adopted_writes_rows_afterwards(self):
        """The always-on process holds its index's connection open while the CLI adopts the
        library: the next batch it writes is converted by the roots the library has then."""
        from tagpup.services.search import PhotoIndex
        index = PhotoIndex(self.side.db_path, model="m")
        try:
            index.load()

            def batch(name):
                path = os.path.join(self.side.pictures, "2024 Regatta", name)
                index.build_or_update([[0.0] * 4], [{"path": path, "mtime": 1.0, "size": 2, "tags": [], "captions": [],
                                                      "raw_metadata": {"SourceFile": path.replace(os.sep, "/")}}],
                                      dim=4, reload=False)

            batch("Before.jpg")
            self.assertIn(os.path.join(self.side.pictures, "2024 Regatta", "Before.jpg"), self.side.raw_paths())
            self.assertTrue(self.side.adopt().ok)
            batch("After.jpg")
        finally:
            index.close()
        held = self.side.raw_paths()
        self.assertIn("@pictures/2024 Regatta/Before.jpg", held, "the adoption converted what was there")
        self.assertIn("@pictures/2024 Regatta/After.jpg", held, "and the open index wrote its next batch as a row")
        self.assertEqual([], [p for p in held if p.lower().startswith(self.side.pictures.lower() + os.sep)])

    def test_a_map_that_moves_moves_the_generations_every_cache_of_paths_is_keyed_by(self):
        """The Identify Faces grids, the folders the watcher watches and the index hold native
        paths, built while a generation stood: moving the root in the map moves no row, so it
        moves the generation instead."""
        from tagpup.store import generations
        plain = rl.Side(self.home, "plain", real=1, bulk=2, outside=0)
        self.side.adopt()

        def stamps(db_path):
            conn = db.connect(db.readonly_uri(db_path), uri=True)
            try:
                return generations.values(conn), faces.fingerprint(conn)
            finally:
                conn.close()

        before, plain_before = stamps(self.side.db_path), stamps(plain.db_path)
        self.assertEqual(before, stamps(self.side.db_path), "nothing moved")
        self.edit_map(os.path.join(self.home.root, "moved"))
        with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
            after = stamps(self.side.db_path)
        self.assertNotEqual(before[0][0], after[0][0], "photos")
        self.assertNotEqual(before[0][1], after[0][1], "faces")
        self.assertEqual(before[0][2], after[0][2], "the tag tree holds no paths")
        self.assertNotEqual(before[1], after[1])
        self.assertEqual(plain_before, stamps(plain.db_path), "a library with no roots never moves with the map")

    def test_a_pinned_run_holds_one_map_however_often_it_is_edited(self):
        self.side.adopt()
        moved = os.path.join(self.home.root, "moved")
        with store_roots.pinned(self.side.db_path) as roots:
            self.edit_map(moved)
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
                try:
                    self.assertIs(roots, store_roots.roots_for(conn))
                    self.assertEqual(self.side.pictures, store_roots.from_row(conn, "@pictures"))
                finally:
                    conn.close()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                self.assertEqual(moved, store_roots.from_row(conn, "@pictures"))
        finally:
            conn.close()

    def test_a_pinned_run_stops_when_the_librarys_own_roots_change(self):
        with store_roots.pinned(self.side.db_path):
            conn = db.connect(self.side.db_path)
            try:
                self.assertTrue(store_roots.roots_for(conn).identity)
                self.side.adopt()
                with self.assertRaises(store_roots.RootsChanged):
                    store_roots.roots_for(conn)
            finally:
                conn.close()

    def test_a_write_prepared_before_an_adoption_is_run_again_after_it(self):
        path = os.path.join(self.side.pictures, "2024 Regatta", "Caught between.jpg")
        calls = []

        def operation(conn):
            calls.append(1)
            row = store_roots.to_row(conn, path)
            if len(calls) == 1:
                # Another process adopts the library after this write has converted its path
                # and before it writes it.
                self.assertTrue(self.side.adopt().ok)
            conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')",
                         (row,))

        db.write_with_connection(self.side.db_path, operation)
        self.assertEqual(2, len(calls))
        self.assertIn("@pictures/2024 Regatta/Caught between.jpg", self.side.raw_paths())
        self.assertNotIn(path, self.side.raw_paths())

    def test_a_write_on_a_long_lived_connection_begins_at_the_lock(self):
        self.side.adopt()
        conn = db.connect(self.side.db_path, check_same_thread=False)
        try:
            path = os.path.join(self.side.pictures, "2024 Regatta", "Batch.jpg")
            photo_rows.add_read(conn, path, rl.read(path))
            self.assertTrue(conn.in_transaction, "the batch holds the write lock until its caller commits")
            conn.commit()
        finally:
            conn.close()
        self.assertIn("@pictures/2024 Regatta/Batch.jpg", self.side.raw_paths())

    def test_a_connection_that_is_not_dbs_is_read_each_time_and_a_stand_in_has_no_library(self):
        import sqlite3
        plain = sqlite3.connect(self.side.db_path)
        try:
            self.assertTrue(store_roots.roots_for(plain).identity)
        finally:
            plain.close()
        self.side.adopt()
        plain = sqlite3.connect(self.side.db_path)
        try:
            self.assertFalse(store_roots.roots_for(plain).identity)
        finally:
            plain.close()

        class Recorder:
            def execute(self, *args):
                raise AssertionError("a stand-in is not asked")

        self.assertTrue(store_roots.roots_for(Recorder()).identity)

    def test_the_hand_off_to_the_map_names_what_is_missing(self):
        machine.provide(None)
        try:
            with self.assertRaises(paths.RootsError) as raised:
                machine.roots_of({"pictures": rl.ADDRESS})
            self.assertIn("machine_roots.json", str(raised.exception))
            self.assertTrue(machine.roots_of({}).identity)
        finally:
            machine.provide(config.roots_of)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheMapProviderIsNeverForgotten(unittest.TestCase):
    """No entry point has to import tagpup.config before it reads a rooted library: the owner's
    scripts import the store and nothing of config, and a library that holds a root was
    unreadable to them."""

    CODE = (
        "import sys\n"
        "root, scripts, library, script = sys.argv[1:5]\n"
        "sys.path.insert(0, root)\n"
        "sys.path.insert(0, scripts)\n"
        "if script:\n"
        "    __import__(script)\n"
        "before = 'tagpup.config' in sys.modules\n"
        "from tagpup.store import db, photos\n"
        "conn = db.connect(db.readonly_uri(library), uri=True)\n"
        "print(before, len(photos.all_paths(conn)))\n")

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_provider_")
        self.side = rl.Side(self.home, "rooted", real=1, bulk=2, outside=1)
        self.assertTrue(self.side.adopt().ok)
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read_in_a_fresh_interpreter(self, script=""):
        import subprocess
        from tagpup.core import processes
        done = processes.run([sys.executable, "-c", self.CODE, self.root, os.path.join(self.root, "scripts"),
                              self.side.db_path, script], capture_output=True, text=True, timeout=120,
                             stdin=subprocess.DEVNULL)
        self.assertEqual(0, done.returncode, done.stderr[-2000:])
        return done.stdout.split()

    def test_a_process_that_imported_only_the_store_reads_the_library(self):
        before, count = self.read_in_a_fresh_interpreter()
        self.assertEqual("False", before, "config was not imported first: the store asked for it")
        self.assertEqual("10", count)


class TheDynamicImportIsInOnePlace(unittest.TestCase):
    """core.machine imports tagpup.config when asked with no reader registered, and nowhere else in
    core does, since core may import nothing above it."""

    def test_a_failure_to_load_the_reader_is_a_roots_error_naming_what_failed(self):
        from tagpup.core import paths
        machine.provide(None)
        try:
            with mock.patch("importlib.import_module", side_effect=ImportError("no module named something")):
                with self.assertRaises(paths.RootsError) as raised:
                    machine.roots_of({"pictures": rl.ADDRESS})
        finally:
            machine.provide(config.roots_of)
        said = str(raised.exception)
        self.assertIn("tagpup.config", said)
        self.assertIn("ImportError", said)
        self.assertIn("no module named something", said)
        self.assertIn("pictures", said)

    def test_only_machine_py_imports_config_inside_core(self):
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagpup", "core")
        pattern = re.compile(r"""import_module\(\s*["']tagpup\.config["']|import tagpup\.config|"""
                             r"""from tagpup import config|from tagpup\.config""")
        found = []
        for name in sorted(os.listdir(root)):
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        if not line.lstrip().startswith("#") and pattern.search(line):
                            found.append("%s:%d" % (name, number))
        self.assertEqual(1, len(found), found)
        self.assertTrue(found[0].startswith("machine.py:"), found)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheJournalSpeaksOneForm(unittest.TestCase):
    """An edit's values are native; the library holds rows. A change recorded before the
    adoption holds native paths and is replayed as rows."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_journal_")
        self.side = rl.Side(self.home, "rooted", real=2, bulk=2, outside=1)
        self.a, self.b = self.side.real[0], self.side.real[1]
        self.renamed = os.path.join(self.side.pictures, "2024 Regatta", "Another name.jpg")
        self.photo_id = self.side.rows("SELECT id FROM photos WHERE path = ?", (self.a,))[0][0]

    def edit(self, old, new, expect_old=True):
        return journal.apply(self.side.db_path, "a change of a photo's path", [
            journal.update("photos", (self.photo_id,), {"path": old} if expect_old else {}, {"path": new})])

    def path_now(self):
        return self.side.rows("SELECT path FROM photos WHERE id = ?", (self.photo_id,))[0][0]

    def test_a_change_made_before_the_adoption_is_undone_as_rows_after_it(self):
        applied = self.edit(self.a, self.renamed)
        self.assertEqual(self.renamed, self.path_now())
        recorded = self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'path'",
                                  (applied.change_id,))
        self.assertEqual([(self.a, self.renamed)], recorded, "recorded native, as every library's was")
        self.assertTrue(self.side.adopt().ok)
        self.assertEqual("@pictures/2024 Regatta/Another name.jpg", self.path_now())
        undone = journal.undo(self.side.db_path, applied.change_id)
        self.assertEqual(1, undone.rows)
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", self.path_now(), "the rooted form, not a native row")
        self.assertEqual([], [p for p in self.side.raw_paths() if p == self.a])

    def test_the_journal_keeps_what_it_was(self):
        applied = self.edit(self.a, self.renamed)
        before = self.side.rows("SELECT * FROM change_rows ORDER BY id")
        self.assertTrue(self.side.adopt().ok)
        after = self.side.rows("SELECT * FROM change_rows ORDER BY id")
        self.assertEqual(before, after)
        self.assertEqual(1, len([row for row in after if row[1] == applied.change_id]))

    def test_a_change_made_after_the_adoption_records_the_row_form_and_undoes(self):
        self.assertTrue(self.side.adopt().ok)
        applied = self.edit(self.a, self.renamed)
        recorded = self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'path'",
                                  (applied.change_id,))
        self.assertEqual([("@pictures/2024 Regatta/IMG_1001.jpg", "@pictures/2024 Regatta/Another name.jpg")], recorded)
        self.assertEqual("@pictures/2024 Regatta/Another name.jpg", self.path_now())
        journal.undo(self.side.db_path, applied.change_id)
        self.assertEqual("@pictures/2024 Regatta/IMG_1001.jpg", self.path_now())

    def test_a_plan_that_read_a_row_native_is_not_refused_for_the_library_holding_it_as_a_row(self):
        self.assertTrue(self.side.adopt().ok)
        rehearsal = journal.rehearse(self.side.db_path, "x", [
            journal.update("photos", (self.photo_id,), {"path": self.a}, {"path": self.renamed})])
        self.assertIsNone(rehearsal.refused)
        self.assertTrue(rehearsal.exact, rehearsal.differences)

    def test_a_plan_that_read_another_file_is_still_refused(self):
        self.assertTrue(self.side.adopt().ok)
        rehearsal = journal.rehearse(self.side.db_path, "x", [
            journal.update("photos", (self.photo_id,), {"path": self.b}, {"path": self.renamed})])
        self.assertIn("path changed", rehearsal.refused)

    def test_a_setting_changed_before_the_adoption_is_undone_after_it_as_rows(self):
        from tagpup.services import settings
        sub = os.path.join(self.side.pictures, "Trips")
        changed = settings.change(self.side.library, {settings.IGNORED: sub})
        self.assertTrue(changed.changed, changed.message())
        recorded = self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'value'",
                                  (changed.details["change"],))
        self.assertEqual([(os.path.join(self.side.pictures, "Not these"), sub)], recorded, "native, as made")
        self.assertTrue(self.side.adopt().ok)
        undone = journal.undo(self.side.db_path, changed.details["change"])
        self.assertEqual(1, undone.rows)
        self.assertEqual("@pictures/Not these", self.side.rows("SELECT value FROM settings WHERE key = 'library.ignored'")[0][0])

    def test_a_setting_of_folders_is_edited_native_and_recorded_as_rows(self):
        from tagpup.services import settings
        self.assertTrue(self.side.adopt().ok)
        sub = os.path.join(self.side.pictures, "Trips")
        result = settings.change(self.side.library, {settings.IGNORED: sub + "\n" + self.side.outside_folder})
        self.assertTrue(result.changed, result.message())
        self.assertEqual("@pictures/Trips\n" + self.side.outside_folder,
                         self.side.rows("SELECT value FROM settings WHERE key = 'library.ignored'")[0][0])
        recorded = self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'value'",
                                  (result.details["change"],))
        self.assertEqual([("@pictures/Not these", "@pictures/Trips\n" + self.side.outside_folder)], recorded)
        self.assertEqual(sub + "\n" + self.side.outside_folder, settings.of(self.side.library).values["library.ignored"])
        again = settings.change(self.side.library, {settings.IGNORED: sub + "\n" + self.side.outside_folder})
        self.assertEqual([], again.details["changed"], "the same folders, spelled native, are no change")

    def test_a_suggestions_paths_replay_in_row_form(self):
        self.assertTrue(self.side.adopt().ok)
        ids = self.side.rows("SELECT photo_id FROM suggestions")[0]
        raw = self.side.rows("SELECT raw FROM suggestions")[0][0]
        native = store_roots.suggested_to_native(raw, machine.roots_of({"pictures": rl.ADDRESS}))
        self.assertEqual(self.a, json.loads(native)["path"])
        applied = journal.apply(self.side.db_path, "x", [journal.update("suggestions", ids, {"raw": native},
                                                                          {"raw": native})])
        self.assertEqual(0, applied.changed, "native and row spellings of the same text are equal")


class TheHotLookupsUseTheirIndexes(unittest.TestCase):
    """EXPLAIN QUERY PLAN of what a click does, on a library that holds roots: a SEARCH of an
    index, never a SCAN of photos or faces."""

    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls, prefix="roots_plans_")
        cls.side = rl.Side(cls.home, "rooted", real=1, bulk=40, outside=1)
        assert cls.side.adopt().ok

    def plan(self, sql, params):
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            return " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        finally:
            conn.close()

    def conn(self):
        return db.connect(db.readonly_uri(self.side.db_path), uri=True)

    def test_a_photo_by_its_path(self):
        conn = self.conn()
        try:
            where, params = store_roots.sql_equals(conn, "path", self.side.real[0])
        finally:
            conn.close()
        plan = self.plan("SELECT id FROM photos WHERE " + where, params)
        self.assertIn("SEARCH photos", plan)
        self.assertNotIn("SCAN photos", plan)

    def test_the_photos_of_a_folder_and_of_one_folder_alone(self):
        conn = self.conn()
        try:
            under, params = store_roots.sql_under(conn, "path", os.path.join(self.side.pictures, "2024 Regatta"))
            direct, direct_params = store_roots.sql_in(conn, "path", os.path.join(self.side.pictures, "2024 Regatta"))
        finally:
            conn.close()
        for where, given in ((under, params), (direct, direct_params)):
            plan = self.plan("SELECT id FROM photos WHERE " + where, given)
            self.assertIn("SEARCH photos", plan)
            self.assertNotIn("SCAN photos", plan)

    def test_a_folder_above_the_root_reaches_the_roots_rows_by_their_index_too(self):
        conn = self.conn()
        try:
            where, params = store_roots.sql_under(conn, "path", os.path.dirname(self.side.pictures))
        finally:
            conn.close()
        plan = self.plan("SELECT id FROM photos WHERE " + where, params)
        self.assertNotIn("SCAN photos", plan)
        self.assertIn("SEARCH photos", plan)

    def test_the_faces_of_a_photo(self):
        conn = self.conn()
        try:
            where, params = faces._on_photo(conn, self.side.real[0])
        finally:
            conn.close()
        plan = self.plan("SELECT name FROM faces WHERE " + where, params)
        self.assertNotIn("SCAN faces", plan)
        self.assertNotIn("SCAN photos", plan)
        self.assertIn("SEARCH", plan)

    def test_the_damaged_files_of_a_folder(self):
        conn = self.conn()
        try:
            where, params = store_roots.sql_under(conn, "path", os.path.join(self.side.pictures, "2024 Harbour"))
        finally:
            conn.close()
        self.assertNotIn("SCAN damaged_files", self.plan("SELECT path FROM damaged_files WHERE " + where, params))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class OneBoundaryAndNoOther(unittest.TestCase):
    """The six places that compared a path by hand, and anything like them, are gone: every
    comparison of a path column in the store goes through the connection's Roots."""

    def test_no_store_module_asks_paths_for_sql_without_a_connections_roots(self):
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagpup", "store")
        offenders = []
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py") or name == "roots.py":
                continue
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.lstrip().startswith("#") and re.search(r"paths\.sql_(equals|under|in)\(", line):
                        offenders.append("%s:%d %s" % (name, number, line.strip()))
        self.assertEqual([], offenders, "a comparison of a path column that does not convert its argument by the "
                         "connection's roots finds no row in a library that holds a root: use store.roots.sql_*")

    def test_raw_comparisons_convert_their_argument(self):
        for module, function in ((store_photos, "row_as_recorded"), (faces, "counts_on"), (inspection, "ids_of_stored")):
            with open(module.__file__, encoding="utf-8") as handle:
                source = handle.read()
            body = source.split("def %s(" % function, 1)[1].split("\ndef ", 1)[0]
            self.assertIn("to_row(conn", body, function)


if __name__ == "__main__":
    unittest.main()

"""The camera and the lens a photo was taken with, found by the words typed (tagpup.store.search_index `search_gear`, migration
27; docs/ARCHITECTURE.md, "Backlog: which photo fields are searchable").

Photos are rows as the indexer records them (tests/view_library.py: photo_rows.add_read, whose ExifTool answer holds each
field under its group's name and bare), the shapes photo_index has for a camera (a model that says its make and one that does
not; a scan with none) and the lens fields ExifTool names (photo_index holds none: its reads do not ask). Expectations are
written out by hand. Fictional makers and models.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import photo_meta  # noqa: E402
from tagpup.services import library_view  # noqa: E402
from tagpup.store import db, derived, journal, schema, search_index  # noqa: E402

TIDEWATER = {"EXIF:Make": "Tidewater", "EXIF:Model": "Tidewater TX R6m2"}
SKIFF = {"EXIF:Make": "Tidewater", "EXIF:Model": "Skiff 8"}
PIXEL = {"EXIF:Make": "Lanternfly", "EXIF:Model": "Pixel 8 Pro"}
LENS = {"EXIF:LensModel": "TX24-70mm f/2.8L II USM", "EXIF:LensMake": "Tidewater"}
OTHER_LENS = {"EXIF:LensModel": "Objectif Élan 35mm F1.4"}


def words(text, **parts):
    return json.dumps(dict(parts, words=text))


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class Cameras(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        self.big = vl.photo("Trip", "a.jpg", taken="2024:06:01 10:00:00", fields=dict(TIDEWATER, **LENS))
        self.small = vl.photo("Trip", "b.jpg", taken="2024:06:02 10:00:00", fields=SKIFF)
        self.phone = vl.photo("Trip", "c.jpg", taken="2024:06:03 10:00:00", fields=dict(PIXEL, **OTHER_LENS))
        self.scan = vl.photo("Trip", "d.jpg")   # a scan, a screenshot: no camera at all
        self.xmp_only = vl.photo("Trip", "e.jpg", fields={"XMP:Make": "Lanternfly", "XMP:Model": "Kite 2"})

    def found(self, text, **parts):
        return library_view.ids(self.vl.library, "search", words(text, **parts))["ids"]


class WhatTheWordsFind(Cameras):
    def test_the_make_the_model_and_a_partial_token_find_the_camera(self):
        cases = {"tidewater": [self.big, self.small], "tid": [self.big, self.small], "r6": [self.big], "r6m2": [self.big],
                 "TX": [self.big], "skiff": [self.small], "pixel": [self.phone], "lanternfly": [self.phone, self.xmp_only],
                 "kite": [self.xmp_only]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.found(text))

    def test_a_model_that_lacks_its_make_is_found_by_the_make_and_a_model_that_has_it_is_named_once(self):
        self.assertEqual([self.phone], self.found("lanternfly pixel"))
        self.assertEqual([self.small], self.found("tidewater skiff"))
        # "Tidewater TX R6m2" is the model alone: the make is not put before a model that begins with it.
        self.assertEqual([self.big], self.found('"tidewater tx"'))

    def test_the_lens_is_found_by_its_name_its_range_and_its_aperture(self):
        cases = {"24-70": [self.big], "24-70mm": [self.big], "tx24": [self.big], "70mm": [self.big], "usm": [self.big],
                 "f/2.8": [self.big], "2.8l": [self.big], "elan": [self.phone], "35mm": [self.phone], "F1.4": [self.phone],
                 "Élan": [self.phone]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.found(text))

    def test_a_term_is_a_prefix_and_every_term_must_be_found(self):
        self.assertEqual([self.big], self.found("tidewater 24-70"))
        self.assertEqual([self.big], self.found("r6 usm"))
        self.assertEqual([], self.found("skiff 24-70"), "the camera of one photo and the lens of another")
        self.assertEqual([], self.found("kite usm"))

    def test_the_search_still_finds_a_photo_by_its_other_words(self):
        other = self.vl.photo("Tidewater cove", "f.jpg", fields=PIXEL)
        self.assertEqual({self.big, self.small, other}, set(self.found("tidewater")), "the camera and the folder's words")
        self.assertEqual([other], self.found("tidewater cove"))
        self.assertEqual({self.big, self.small, other},
                         set(self.found("tidewater", none_of=[{"kind": "year", "value": "1900"}])))

    def test_a_photo_with_no_camera_data_is_found_by_no_camera(self):
        for text in ("tidewater", "lanternfly", "usm", "pixel"):
            self.assertNotIn(self.scan, self.found(text))
        self.assertEqual([self.scan], self.found("d.jpg"), "by its name, as ever")
        self.assertEqual(5, look(self.vl.path, "SELECT COUNT(*) FROM search_gear_docsize")[0][0], "a row for each photo")

    def test_nothing_typed_is_fts_syntax(self):
        for text in ('"', "camera:tidewater", "camera : tidewater", "lens:usm", "NEAR(a b)", "tidewater OR lanternfly", "-tidewater",
                     "tid*", "(", chr(92), "24-", "-70", "f/", "2.8l*"):
            with self.subTest(text=text):
                self.assertIsInstance(self.found(text), list)
        self.assertEqual([], self.found("camera:tidewater"), "a column filter is text: no photo holds the word camera")
        self.assertEqual([], self.found("tidewater OR lanternfly"))

    def test_the_words_follow_the_photo_through_the_orders(self):
        for order in library_view.store.ORDERS:
            with self.subTest(order=order):
                self.assertEqual({self.big, self.small}, set(library_view.ids(
                    self.vl.library, "search", words("tidewater"), order=order)["ids"]))


class TheWordsFollowTheWrites(Cameras):
    def test_a_photo_read_again_with_another_camera_and_without_a_lens(self):
        photo_rows.add_read(self.vl.conn, self.vl.path_of(self.big), {"EXIF:Make": "Lanternfly", "EXIF:Model": "Kite 2"})
        self.vl.conn.commit()
        self.assertEqual([self.small], self.found("tidewater"))
        self.assertEqual([], self.found("24-70"))
        self.assertEqual({self.big, self.phone, self.xmp_only}, set(self.found("lanternfly")))

    def test_a_photo_read_again_with_a_lens_it_did_not_have_changes_no_photo_meta_row(self):
        # The lens is in no column of photo_meta, so a changed lens alone changes no row there: the words follow anyway.
        before = look(self.vl.path, "SELECT * FROM photo_meta WHERE photo_id = ?", (self.small,))
        photo_rows.add_read(self.vl.conn, self.vl.path_of(self.small), dict(SKIFF, **LENS))
        self.vl.conn.commit()
        self.assertEqual(before, look(self.vl.path, "SELECT * FROM photo_meta WHERE photo_id = ?", (self.small,)))
        self.assertEqual([self.big, self.small], self.found("24-70"))

    def test_a_metadata_change_through_the_journal_and_its_undo(self):
        held = look(self.vl.path, "SELECT raw_metadata FROM photos WHERE id = ?", (self.small,))[0][0]
        changed = json.loads(held)
        changed.update({"EXIF:Model": "Skiff 9", "Model": "Skiff 9", "EXIF:LensModel": "Harbour 50mm", "LensModel": "Harbour 50mm"})
        applied = journal.apply(self.vl.path, "metadata", [journal.update(
            "photos", (self.small,), {"raw_metadata": held}, {"raw_metadata": json.dumps(changed)})])
        self.assertEqual([self.small], self.found("skiff 9"))
        self.assertEqual([self.small], self.found("harbour 50mm"))
        self.assertEqual([], self.found("skiff 8"))
        journal.undo(self.vl.path, applied.change_id)
        self.assertEqual([self.small], self.found("skiff 8"))
        self.assertEqual([], self.found("skiff 9"))
        self.assertEqual([], self.found("harbour 50mm"))

    def test_a_photo_deleted_by_another_program_takes_its_words(self):
        conn = db.connect(self.vl.path)
        try:
            conn.execute("DELETE FROM photos WHERE id = ?", (self.big,))
            conn.commit()
            self.assertEqual([], search_index.stale(conn, sample=None))
            self.assertEqual([], conn.execute("SELECT rowid FROM search_gear WHERE rowid = ?", (self.big,)).fetchall())
        finally:
            conn.close()
        self.assertEqual([self.small], self.found("tidewater"))

    def test_two_libraries_holding_one_folder_each_find_their_own_cameras(self):
        other = ViewLibrary(self, "lighthouse", home=self.vl.home)
        theirs = other.photo("Trip", "a.jpg", at=self.vl.path_of(self.big), fields=PIXEL)
        self.assertEqual([theirs], library_view.ids(other.library, "search", words("pixel"))["ids"])
        self.assertEqual([], library_view.ids(other.library, "search", words("tidewater"))["ids"])
        self.assertEqual([self.big, self.small], self.found("tidewater"))


class TheDoctor(Cameras):
    def test_a_row_missing_or_out_of_step_is_found_and_the_repair_mends_it(self):
        self.assertEqual([], search_index.stale(self.vl.conn, sample=None))
        self.vl.conn.execute("DELETE FROM search_gear WHERE rowid = ?", (self.big,))
        self.vl.conn.execute("INSERT OR REPLACE INTO search_gear (rowid, camera, lens) VALUES (?, 'Other', '')", (self.small,))
        self.vl.conn.commit()
        self.assertEqual([self.big, self.small], search_index.stale(self.vl.conn, sample=None))
        before, _written, after = derived.repair(self.vl.path)
        self.assertTrue(before)
        self.assertEqual([], after)
        self.assertEqual([self.big, self.small], self.found("tidewater"))

    def test_a_lens_the_words_lack_is_found_by_the_sample_and_the_rebuild_restores_it(self):
        self.vl.conn.execute("INSERT OR REPLACE INTO search_gear (rowid, camera, lens) VALUES (?, ?, '')",
                             (self.big, search_index.gear_text(photo_meta.Gear("Tidewater TX R6m2", None))[0]))
        self.vl.conn.commit()
        self.assertEqual([self.big], search_index.stale(self.vl.conn, sample=None))
        self.assertEqual([], search_index.stale(self.vl.conn, sample=None, lens=False), "the camera alone is right")
        derived.repair(self.vl.path)
        self.assertEqual([self.big], self.found("24-70"))

    def test_the_rebuild_reports_the_word_rows_and_the_camera_rows_it_wrote(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        self.vl.conn.execute("DELETE FROM search_gear WHERE rowid = ?", (self.big,))
        self.vl.conn.commit()
        lines = []
        doctor.rebuild_derived(self.vl.path, apply=True, out=lines.append)
        said = "\n".join(lines)
        self.assertIn("5 word row(s), 5 camera and lens row(s)", said)
        self.assertEqual([self.big], self.found("24-70"))

    def test_the_rows_of_an_id_with_no_photo_are_taken_as_the_other_words_are(self):
        # A row for an id no photo has (a photo another program deleted with its trigger absent, a stale id handed to a
        # writer): refresh_photos takes it, as it takes the word index's.
        self.vl.conn.execute("INSERT INTO search_gear (rowid, camera, lens) VALUES (9999, 'Ghost Camera', '')")
        self.vl.conn.commit()
        self.assertEqual([9999], search_index.stale(self.vl.conn, sample=None))
        derived.refresh_photos(self.vl.conn, [9999])
        self.vl.conn.commit()
        self.assertEqual([], search_index.stale(self.vl.conn, sample=None))
        self.assertEqual([], self.found("ghost"))

    def test_the_rebuild_is_one_transaction_and_an_interrupted_one_changes_nothing(self):
        def halfway(conn):
            raise RuntimeError("the power went")

        with mock.patch.object(search_index, "optimize_gear", side_effect=halfway):
            with self.assertRaises(RuntimeError):
                derived.repair(self.vl.path)
        self.assertEqual([], search_index.stale(self.vl.conn, sample=None))
        self.assertEqual([self.big], self.found("24-70"))


class MigrationTwentySeven(unittest.TestCase):
    """A library at 26 is made by taking away what 27 adds from one at the latest, as a version at 26 left it."""

    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        self.big = vl.photo("Trip", "a.jpg", fields=dict(TIDEWATER, **LENS))
        self.scan = vl.photo("Trip", "d.jpg")
        self.vl.conn.close()
        self.path = vl.path
        conn = db.connect(self.path)
        try:
            conn.execute("DROP TRIGGER search_gear_goes_with_its_photo")
            conn.execute("DROP TABLE search_gear")
            conn.execute("DELETE FROM schema_version WHERE version = 27")
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def schema_rows(self):
        return look(self.path, "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name")

    def test_it_adds_the_table_its_shadows_and_the_trigger_and_nothing_else(self):
        before_schema = self.schema_rows()
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")
                  and not table.startswith("search_")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        self.assertEqual(migration_names.named(27), schema.ensure(self.path))
        added = sorted(row for row in self.schema_rows() if row not in before_schema)
        expected = sorted([("table", name, name) for name in (search_index.GEAR,) + search_index.GEAR_SHADOWS]
                          + [("trigger", "search_gear_goes_with_its_photo", "photos")])
        self.assertEqual(expected, added, "the FTS5 table and its shadows, and one trigger: no column, no index")
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables},
                         "every row of every table that was there is unchanged, photo_meta with them")
        migration = [m for m in schema.MIGRATIONS if m.version == 27][0]
        self.assertEqual((schema.ADDITIVE, (search_index.GEAR,)), (migration.kind, migration.touches))
        self.assertLessEqual({search_index.GEAR} | set(search_index.GEAR_SHADOWS), set(schema.UNWATCHED))
        self.assertEqual(2, look(self.path, "SELECT COUNT(*) FROM search_gear")[0][0], "a row for each photo")

    def test_it_makes_the_camera_words_from_photo_meta_and_reads_no_photos_metadata(self):
        # The migration must not read every photo's JSON on open: it makes the words from photo_meta, and its check reads
        # the metadata of the sample alone (SAMPLE photos at most) -- the rebuild that reads each photo is not run.
        with mock.patch.object(derived, "rebuild_all", side_effect=AssertionError("rebuilt every photo")):
            with mock.patch.object(photo_meta, "load", wraps=photo_meta.load) as loaded:
                schema.ensure(self.path)
        self.assertLessEqual(loaded.call_count, search_index.SAMPLE)
        conn = db.connect(self.path)
        try:
            self.assertEqual([self.big], [i for (i,) in conn.execute(
                "SELECT rowid FROM search_gear WHERE search_gear MATCH ?", ('"r6m2"*',))])
            self.assertEqual([], [i for (i,) in conn.execute(
                "SELECT rowid FROM search_gear WHERE search_gear MATCH ?", ('"24-70"*',))],
                "the lens is not in photo_meta: it comes with the rebuild")
        finally:
            conn.close()

    def test_the_lens_comes_with_the_rebuild_the_doctor_asks_for(self):
        schema.ensure(self.path)
        conn = db.connect(self.path)
        try:
            wrong = search_index.stale(conn, sample=None)
            self.assertEqual([self.big], wrong, "the doctor names the photo whose lens the words lack")
            self.assertTrue(derived.problems(conn))
        finally:
            conn.close()
        derived.repair(self.path)
        conn = db.connect(self.path)
        try:
            self.assertEqual([], derived.problems(conn))
            self.assertEqual([self.big], [i for (i,) in conn.execute(
                "SELECT rowid FROM search_gear WHERE search_gear MATCH ?", ('"24-70"*',))])
        finally:
            conn.close()

    def test_a_change_made_at_26_is_still_undoable_after_it(self):
        applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema.ensure(self.path)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
            self.assertIsNone(journal.schema_gap_blocker(26, 27))
        finally:
            conn.close()
        journal.undo(self.path, applied.change_id)
        self.assertEqual([], look(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))

    def test_interrupted_it_leaves_the_library_at_26_and_runs_again(self):
        def halfway(conn):
            conn.execute("INSERT INTO search_gear (rowid, camera) VALUES (1, 'partial')")
            raise RuntimeError("the power went")

        with mock.patch.object(search_index, "rebuild_gear_from_meta", side_effect=halfway):
            with self.assertRaises(RuntimeError):
                schema.ensure(self.path)
        self.assertEqual(26, look(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual([], look(self.path, "SELECT name FROM sqlite_master WHERE name LIKE 'search_gear%'"))
        schema._current.clear()
        self.assertEqual(migration_names.named(27), schema.ensure(self.path))
        self.assertEqual(2, look(self.path, "SELECT COUNT(*) FROM search_gear")[0][0])

    def test_a_library_below_27_is_matched_in_the_two_tables_of_24_alone(self):
        self.assertNotIn(search_index.GEAR, search_index.clause("tidewater")[0])
        self.assertIn(search_index.GEAR, search_index.clause("tidewater", gear=True)[0])
        conn = db.connect(self.path)
        try:
            self.assertFalse(search_index.gear_present(conn))
            self.assertEqual([], search_index.stale(conn, sample=None), "the doctor asks nothing of a table not there")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

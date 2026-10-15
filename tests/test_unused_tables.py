"""Migration 30 drops the four tables nothing reads (tagpup.store.schema `_drop_unused`; owner, 2026-10-10): `search_gear`
(the camera and lens words, an FTS5 table), `photo_meta`, `folder_ids` and `library_identity` (the ids the folder markers
carried). A library at 29 holds all four, with rows -- and each live library one journaled `mark_folders` change of
`folder_ids` -- so this holds the migration to what it promises: one backup, one transaction, nothing else changed, the
journal consistent, every other change still undoable, a search of a camera the same before and after.

A library at 29 is the library of today with the four tables put back as migrations 19, 26 and 27 made them, rows in
them as the old code wrote them (a `photo_meta` row and a camera-words row for every photo, a stamped identity, the folder
ids and the one change that inserted them), and its version put back. Fictional names only.
"""
import json
import os
import shutil
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
from migration_names import after, upto  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import library_view, roots_verify, sync  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

LIBRARY_ID = "11111111-2222-4333-8444-555555555555"
FOLDER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000001"
OTHER_FOLDER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000002"

#: The tables as migrations 19, 26 and 27 made them, and the trigger that took a photo's rows of the first.
LEGACY = (
    "CREATE TABLE photo_meta (photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE, rating INTEGER, make TEXT,"
    " model TEXT, width INTEGER, height INTEGER, latitude REAL, longitude REAL)",
    "CREATE TABLE library_identity (slot INTEGER PRIMARY KEY CHECK (slot = 1), id TEXT NOT NULL, stamped TEXT NOT NULL)",
    "CREATE TABLE folder_ids (id TEXT PRIMARY KEY NOT NULL, path TEXT NOT NULL UNIQUE COLLATE %s, marked TEXT NOT NULL)"
    % paths.COLLATE,
    "CREATE VIRTUAL TABLE search_gear USING fts5(camera, lens, tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3',"
    " content = '', contentless_delete = 1)",
    "CREATE TRIGGER search_gear_goes_with_its_photo AFTER DELETE ON photos BEGIN DELETE FROM search_gear WHERE rowid = OLD.id; END",
    "DROP TRIGGER derived_go_with_their_photo",
    "CREATE TRIGGER derived_go_with_their_photo AFTER DELETE ON photos BEGIN DELETE FROM photo_tags WHERE photo_id = OLD.id;"
    " DELETE FROM photo_folder WHERE photo_id = OLD.id; DELETE FROM photo_meta WHERE photo_id = OLD.id; END",
)
DROPPED = ("search_gear", "photo_meta", "folder_ids", "library_identity")


def query(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def names(path, kind="table"):
    return {name for (name,) in query(path, "SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def shape(path):
    """{table: its columns} of every table that is none of the four or the camera table's shadows."""
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return {table: [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
                for table in sorted(names(path)) if not table.startswith("search_gear") and table not in DROPPED}
    finally:
        conn.close()


def make_legacy(path, folder="D:/Harbour/Trip", rows=True):
    """Put the library at `path` (at the latest) back at 29: the four tables, with `rows`, the one `mark_folders` change
    that inserted the folder ids, and the version."""
    conn = db.connect(path)
    try:
        for statement in LEGACY:
            conn.execute(statement)
        if rows:
            for photo_id, in conn.execute("SELECT id FROM photos").fetchall():
                conn.execute("INSERT INTO photo_meta (photo_id, make, model) VALUES (?, 'Wrong', 'Row')", (photo_id,))
                conn.execute("INSERT INTO search_gear (rowid, camera, lens) VALUES (?, 'zzwrong', '')", (photo_id,))
            conn.execute("INSERT INTO library_identity (slot, id, stamped) VALUES (1, ?, '2026-10-01 10:00:00')", (LIBRARY_ID,))
            change = conn.execute(
                "INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
                " VALUES ('mark_folders', 'applied', 26, '2026-10-01 10:00:00', '2026-10-01 10:00:01', ?)",
                (json.dumps({"counts": {"new": 2}, "rows": {"folder_ids": 2}}),)).lastrowid
            for number, (folder_id, where) in enumerate(((FOLDER_ID, folder), (OTHER_FOLDER_ID, folder + "/Beach"))):
                conn.execute("INSERT INTO folder_ids (id, path, marked) VALUES (?, ?, '2026-10-01 10:00:00')", (folder_id, where))
                for column, value in (("id", folder_id), ("path", where), ("marked", "2026-10-01 10:00:00")):
                    conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                                 " VALUES (?, 'insert', 'folder_ids', ?, ?, NULL, ?)", (change, json.dumps([folder_id]), column, value))
        conn.execute("DELETE FROM schema_version WHERE version >= 30")
        conn.commit()
    finally:
        conn.close()
    schema._current.clear()


def backups(path):
    folder = Library(path).backups
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class AtTwentyNine(unittest.TestCase):
    """A library of photos, a tag, and a change made at 29 that touches other tables."""

    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        self.photos = [vl.photo("Trip", "a.jpg", fields={"EXIF:Make": "Tidewater", "EXIF:Model": "Tidewater TX R6m2"}),
                       vl.photo("Trip", "b.jpg", fields={"EXIF:Make": "Lanternfly", "EXIF:Model": "Pixel 8 Pro"}),
                       vl.photo("Trip", "c.jpg")]
        self.path = vl.path
        vl.conn.close()
        # Changes made at 29: a node added, and a photo's keywords changed (so an undo rebuilds the derived rows).
        added = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        held = query(self.path, "SELECT tags FROM photos WHERE id = ?", (self.photos[0],))[0][0]
        retagged = journal.apply(self.path, "retag", [journal.update(
            "photos", (self.photos[0],), {"tags": held}, {"tags": json.dumps(["Fresh"])})])
        self.made_at_29 = [added.change_id, retagged.change_id]
        conn = db.connect(self.path)
        try:
            conn.executemany("UPDATE changes SET schema_version = 29 WHERE id = ?", [(i,) for i in self.made_at_29])
            conn.commit()
        finally:
            conn.close()

    def migrate(self):
        schema._current.clear()
        return schema.ensure(self.path)

    def found(self, text):
        return library_view.ids(self.vl.library, "search", json.dumps({"words": text}))["ids"]


class WhatItDrops(AtTwentyNine):
    def test_it_drops_the_four_tables_their_shadows_and_the_trigger_and_nothing_else(self):
        make_legacy(self.path)
        before = shape(self.path)
        counts = {table: query(self.path, "SELECT COUNT(*) FROM %s" % table)[0][0] for table in before
                  if table not in ("changes", "change_rows", "schema_version") and not table.startswith("search_")}
        self.assertLessEqual(set(DROPPED), names(self.path))
        self.assertEqual(after(29), self.migrate())
        self.assertEqual(before, shape(self.path), "every other table has the columns it had")
        self.assertEqual(counts, {table: query(self.path, "SELECT COUNT(*) FROM %s" % table)[0][0] for table in counts})
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED or name.startswith("search_gear")])
        self.assertNotIn("search_gear_goes_with_its_photo", names(self.path, "trigger"))
        self.assertEqual(schema.LATEST, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_a_photo_deleted_afterwards_takes_its_derived_rows_and_the_trigger_names_no_dropped_table(self):
        make_legacy(self.path)
        self.migrate()
        sql = query(self.path, "SELECT sql FROM sqlite_master WHERE name = 'derived_go_with_their_photo'")[0][0]
        self.assertFalse([table for table in DROPPED if table in sql], sql)
        conn = db.connect(self.path)
        try:
            conn.execute("DELETE FROM photos WHERE id = ?", (self.photos[1],))   # the old trigger would name photo_meta
            conn.commit()
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM photo_folder WHERE photo_id = ?", (self.photos[1],)).fetchone()[0])
        finally:
            conn.close()

    def test_the_touches_name_only_what_is_real(self):
        migration = [m for m in schema.MIGRATIONS if m.version == 30][0]
        self.assertEqual(schema.DESTRUCTIVE, migration.kind)
        self.assertEqual(DROPPED, tuple(migration.touches))
        self.assertEqual(DROPPED, tuple(schema.DROPPED))
        make_legacy(self.path)
        self.assertLessEqual(set(migration.touches), names(self.path), "each is a table a library at 29 has")
        self.migrate()
        self.assertFalse(set(migration.touches) & names(self.path), "and none is there after")
        self.assertFalse(set(migration.touches) & set(journal.KEYS), "none is a table a change may write")
        self.assertFalse(set(migration.touches) & set(journal.DERIVED))
        self.assertEqual({30: DROPPED, 31: schema.DROPPED_31}, schema.ONLY_DROPS)

    def test_a_library_with_empty_tables_and_one_without_them_migrate_the_same(self):
        make_legacy(self.path, rows=False)
        self.assertEqual(after(29), self.migrate())
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])
        self.assertEqual(1, len(backups(self.path)), "a library with rows in other tables is backed up either way")
        again = ViewLibrary(self, "bare")   # made at the latest: never had them
        schema._current.clear()
        self.assertEqual([], schema.ensure(again.path))
        conn = db.connect(again.path)
        try:
            conn.execute("DELETE FROM schema_version WHERE version >= 30")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(after(29), self.migrate_path(again.path))

    def migrate_path(self, path):
        schema._current.clear()
        return schema.ensure(path)


class TheJournal(AtTwentyNine):
    def test_the_marker_change_and_its_rows_are_taken_and_nothing_names_a_dropped_table(self):
        make_legacy(self.path)
        changes = query(self.path, "SELECT COUNT(*) FROM changes")[0][0]
        rows = query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0]
        self.assertEqual(6, query(self.path, "SELECT COUNT(*) FROM change_rows WHERE table_name = 'folder_ids'")[0][0])
        self.migrate()
        self.assertEqual(0, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0])
        self.assertEqual(rows - 6, query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0], "its rows, and no other")
        self.assertEqual(changes + 2, query(self.path, "SELECT COUNT(*) FROM changes")[0][0], "one taken, the later migrations' own added")
        self.assertEqual([], query(self.path, "SELECT 1 FROM change_rows WHERE table_name IN (?, ?, ?, ?)", DROPPED))
        self.assertEqual([], query(self.path, "SELECT 1 FROM change_rows WHERE change_id NOT IN (SELECT id FROM changes)"))
        recorded = query(self.path, "SELECT operation, schema_version, status, summary FROM changes WHERE operation LIKE 'migration 30%' ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(("migration 30: the tables nothing reads", 29, "applied"), recorded[:3])
        summary = json.loads(recorded[3])
        self.assertEqual("destructive", summary["kind"])
        self.assertEqual({"journal rows taken out": 6, "changes taken out": 1}, summary["took"])
        self.assertTrue(summary["backup"])

    def test_a_change_made_at_29_is_still_undoable_after_it_and_its_undo_works(self):
        make_legacy(self.path)
        self.migrate()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            for change in self.made_at_29:
                self.assertEqual([], journal.refusal(conn, change), change)
            self.assertIsNone(journal.schema_gap_blocker(29, 30))
            self.assertIsNone(journal.schema_gap_blocker(28, 30))
        finally:
            conn.close()
        # The newest first, as an undo is: the keywords, then the node.
        for change in reversed(self.made_at_29):
            undone = journal_service.undo(self.vl.library, change, apply=True)
            self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual([], query(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))
        self.assertEqual([], query(self.path, "SELECT 1 FROM photo_tags"))

    def test_the_migration_itself_is_listed_and_not_undoable(self):
        make_legacy(self.path)
        self.migrate()
        listed = journal_service.history(self.vl.library, limit=5)
        self.assertNotIn("mark_folders", str(listed))
        change = query(self.path, "SELECT MAX(id) FROM changes")[0][0]
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertTrue(journal.refusal(conn, change))
        finally:
            conn.close()

    def test_a_change_that_names_the_dropped_table_among_others_loses_only_those_rows(self):
        make_legacy(self.path)
        conn = db.connect(self.path)
        try:
            mixed = conn.execute("INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
                                 " VALUES ('mixed', 'applied', 26, '2026-10-01 10:00:00', '2026-10-01 10:00:01', '{}')").lastrowid
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'insert', 'folder_ids', '[\"x\"]', 'id', NULL, 'x')", (mixed,))
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'update', 'settings', '[\"a.key\"]', 'value', '1', '2')", (mixed,))
            conn.commit()
        finally:
            conn.close()
        self.migrate()
        self.assertEqual([(mixed, 'settings')], query(self.path, "SELECT change_id, table_name FROM change_rows WHERE change_id = ?", (mixed,)))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM changes WHERE id = ?", (mixed,))[0][0])


class HowItFails(AtTwentyNine):
    def test_interrupted_after_the_journal_was_pruned_nothing_is_half_dropped(self):
        make_legacy(self.path)
        held = {"tables": names(self.path), "triggers": names(self.path, "trigger"),
                "rows": query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0],
                "changes": query(self.path, "SELECT COUNT(*) FROM changes")[0][0],
                "folder_ids": query(self.path, "SELECT id, path FROM folder_ids ORDER BY id"),
                "photo_meta": query(self.path, "SELECT COUNT(*) FROM photo_meta")[0][0]}
        with mock.patch.object(schema, "DERIVED_GO_WITH_THEIR_PHOTO", "CREATE TRIGGER broken"):   # after the prune and the DROP TRIGGERs
            with self.assertRaises(sqlite3.OperationalError):
                self.migrate()
        self.assertEqual(29, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual(held, {"tables": names(self.path), "triggers": names(self.path, "trigger"),
                                "rows": query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0],
                                "changes": query(self.path, "SELECT COUNT(*) FROM changes")[0][0],
                                "folder_ids": query(self.path, "SELECT id, path FROM folder_ids ORDER BY id"),
                                "photo_meta": query(self.path, "SELECT COUNT(*) FROM photo_meta")[0][0]})
        self.assertEqual(1, len(backups(self.path)), "the copy was taken first, and is the way back")
        self.assertEqual(after(29), self.migrate(), "the next open does it")
        self.assertEqual(schema.LATEST, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_a_check_that_fails_rolls_it_all_back(self):
        make_legacy(self.path)
        with mock.patch.object(schema.UnusedTablesGone, "after", return_value=["something is still there"]):
            with self.assertRaises(schema.CheckFailed):
                self.migrate()
        self.assertLessEqual(set(DROPPED), names(self.path))
        self.assertEqual(6, query(self.path, "SELECT COUNT(*) FROM change_rows WHERE table_name = 'folder_ids'")[0][0])
        self.assertEqual(29, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_one_full_backup_is_taken_first_and_holds_the_tables(self):
        make_legacy(self.path)
        self.assertEqual([], backups(self.path))
        self.migrate()
        taken = backups(self.path)
        self.assertEqual(1, len(taken))
        self.assertIn("before-migration-30", taken[0])
        copy = os.path.join(Library(self.path).backups, taken[0])
        self.assertLessEqual(set(DROPPED), names(copy))
        self.assertEqual(2, query(copy, "SELECT COUNT(*) FROM folder_ids")[0][0])
        self.assertEqual(29, query(copy, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_a_second_open_after_the_first_migrates_nothing_and_takes_no_backup(self):
        make_legacy(self.path)
        self.assertEqual(after(29), self.migrate())
        self.assertEqual([], self.migrate(), "another process opening it")
        self.assertEqual(1, len(backups(self.path)))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation LIKE 'migration 30%'")[0][0])

    def test_an_older_app_will_not_open_the_migrated_library(self):
        make_legacy(self.path)
        self.migrate()
        with mock.patch.object(schema, "MIGRATIONS", upto(29)), mock.patch.object(schema, "LATEST", 29):
            schema._current.clear()
            with self.assertRaises(schema.NewerLibrary) as refused:
                schema.ensure(self.path)
        self.assertEqual((schema.LATEST, 29), (refused.exception.found, refused.exception.known))

    def test_a_snapshot_from_before_it_is_migrated_again_when_opened(self):
        make_legacy(self.path)
        conn = db.connect(self.path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        snapshot = os.path.join(os.path.dirname(self.path), "snapshot-29.db")
        shutil.copyfile(self.path, snapshot)
        self.migrate()
        shutil.copyfile(snapshot, self.path)   # restored over the migrated library
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)
        self.assertEqual(after(29), self.migrate())
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])
        self.assertEqual(0, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0])


class TheCameraSearch(AtTwentyNine):
    """A search of a camera or lens finds the same photos on a library behind (the old table, with a wrong word in it,
    is not read) and after."""

    def test_the_same_photos_before_and_after(self):
        make_legacy(self.path)
        cases = {"tidewater": [self.photos[0]], "pixel": [self.photos[1]], "r6m2": [self.photos[0]], "zzwrong": [], "wrong": [],
                 "lanternfly pixel": [self.photos[1]], "pro": [self.photos[1]]}
        for text, expected in cases.items():
            with self.subTest(text=text, behind=True):
                self.assertEqual(expected, self.found(text))
        self.migrate()
        for text, expected in cases.items():
            with self.subTest(text=text, behind=False):
                self.assertEqual(expected, self.found(text))


class AFolderMarkedLibrary(unittest.TestCase):
    """The library of the folder-marker days, with its marker file in a folder: it opens, syncs, verifies and is reported
    by the doctor as one that never had them (the scenario of a live library)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="unused_ids_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=1)
        result = self.side.adopt()
        self.assertIsNone(result.refused, result.refused)
        self.machine = rl.machine()
        self.folder = os.path.join(self.side.pictures, rl.FOLDERS[0])
        make_legacy(self.side.db_path, folder=self.folder)
        with open(os.path.join(self.folder, ".tagpup"), "w", encoding="utf-8") as handle:
            handle.write(LIBRARY_ID + " " + FOLDER_ID + "\n")

    def doctor(self):
        said = []
        broken = rl.doctor().report(self.side.db_path, show=0, out=said.append)
        return broken, "\n".join(str(line) for line in said)

    def test_it_opens_syncs_verifies_and_is_reported_and_the_marker_file_is_left_alone(self):
        schema._current.clear()
        self.assertEqual(after(29), schema.ensure(self.side.db_path))
        result = sync.sync(self.side.library, apply=True, exiftool_path=rl.EXIFTOOL, roots=[self.side.pictures])
        self.assertIsNone(result.refused, result.refused)
        self.assertNotIn("folder_markers", result.details)
        answer = roots_verify.verify(self.side.library, "pictures", self.side.pictures, self.machine)
        self.assertNotIn("markers", answer)
        self.assertEqual(answer["checked"], answer["matches"] + answer["differs"] + answer["missing"]
                         + answer["unreadable"] + answer["unread"])
        broken, report = self.doctor()
        self.assertNotIn("library identity", report)
        self.assertNotIn("folder-ids", report)
        self.assertNotIn("metadata rows", report)
        self.assertTrue(os.path.exists(os.path.join(self.folder, ".tagpup")), "the marker file is left alone")


if __name__ == "__main__":
    unittest.main()

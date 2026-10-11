"""Migration 31 drops `name_review_dismissals` (the names set aside from the names to review) and `damaged_files` (the photo files
found damaged): both features are deleted (owner, 2026-10-11), and all three live libraries held both empty, with no
recorded change naming either (counted 2026-10-11). A library at 30 holds both, so this holds the migration to what it
promises, as test_unused_tables.py holds migration 30: one backup, one transaction, nothing else changed, the journal
consistent (a change naming one of the tables loses those rows, and a change left empty goes), every other change still
undoable, a second open and an older app both safe.

A library at 30 is the library of today with the two tables put back as migrations 16 and 28 made them, rows in them as
the old code wrote them, and its version put back. Fictional names only.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migration_names import after  # noqa: E402
from test_unused_tables import backups, names, query, shape  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DROPPED = ("name_review_dismissals", "damaged_files")

#: The tables as migrations 28 and 16 made them.
LEGACY = (
    "CREATE TABLE name_review_dismissals (name_key TEXT PRIMARY KEY, rows_seen INTEGER NOT NULL, decided TEXT NOT NULL)",
    "CREATE TABLE damaged_files (path TEXT PRIMARY KEY COLLATE %s, mtime REAL NOT NULL, size INTEGER NOT NULL,"
    " kind TEXT NOT NULL, detail TEXT NOT NULL, zero_tail INTEGER NOT NULL DEFAULT 0, found TEXT NOT NULL, seen TEXT NOT NULL,"
    " run TEXT)" % paths.COLLATE,
)


def make_legacy(path, rows=True, journaled=False):
    """Put the library at `path` (at the latest) back at 30: the two tables, with `rows`, and the version. `journaled` adds
    two changes naming them (none does in a live library): one only, one among a settings row."""
    conn = db.connect(path)
    try:
        for statement in LEGACY:
            conn.execute(statement)
        if rows:
            conn.execute("INSERT INTO name_review_dismissals VALUES ('wren halloway', 3, '2026-10-01 10:00:00')")
            conn.execute("INSERT INTO name_review_dismissals VALUES ('sable quill', 1, '2026-10-01 10:00:00')")
            conn.execute("INSERT INTO damaged_files (path, mtime, size, kind, detail, found, seen)"
                         " VALUES ('D:/Harbour/Trip/a.jpg', 1.5, 10, 'truncated', 'x', '2026-10-01', '2026-10-01')")
        if journaled:
            only = conn.execute("INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
                                " VALUES ('dismiss', 'applied', 28, '2026-10-01 10:00:00', '2026-10-01 10:00:01', '{}')").lastrowid
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'insert', 'name_review_dismissals', '[\"a\"]', 'rows_seen', NULL, '3')", (only,))
            mixed = conn.execute("INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
                                 " VALUES ('mixed', 'applied', 28, '2026-10-01 10:00:00', '2026-10-01 10:00:01', '{}')").lastrowid
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'insert', 'damaged_files', '[\"x\"]', 'kind', NULL, 'truncated')", (mixed,))
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'update', 'settings', '[\"a.key\"]', 'value', '1', '2')", (mixed,))
        conn.execute("DELETE FROM schema_version WHERE version = 31")
        conn.commit()
    finally:
        conn.close()
    schema._current.clear()


class AtThirty(unittest.TestCase):
    """A library of photos, and two changes made at 30 that touch other tables."""

    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        self.photos = [vl.photo("Trip", "a.jpg"), vl.photo("Trip", "b.jpg")]
        self.path = vl.path
        vl.conn.close()
        added = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        held = query(self.path, "SELECT tags FROM photos WHERE id = ?", (self.photos[0],))[0][0]
        retagged = journal.apply(self.path, "retag", [journal.update(
            "photos", (self.photos[0],), {"tags": held}, {"tags": json.dumps(["Fresh"])})])
        self.made_at_30 = [added.change_id, retagged.change_id]
        conn = db.connect(self.path)
        try:
            conn.executemany("UPDATE changes SET schema_version = 30 WHERE id = ?", [(i,) for i in self.made_at_30])
            conn.commit()
        finally:
            conn.close()

    def migrate(self, path=None):
        schema._current.clear()
        return schema.ensure(path or self.path)


class WhatItDrops(AtThirty):
    def test_it_drops_the_two_tables_and_nothing_else_and_their_rows_go_with_them(self):
        make_legacy(self.path)
        before = shape(self.path)
        counts = {table: query(self.path, "SELECT COUNT(*) FROM %s" % table)[0][0] for table in before
                  if table not in ("changes", "change_rows", "schema_version") and not table.startswith("search_")
                  and table not in DROPPED}
        self.assertEqual((2, 1), tuple(query(self.path, "SELECT COUNT(*) FROM %s" % t)[0][0] for t in DROPPED))
        self.assertEqual(after(30), self.migrate())
        self.assertEqual({t: c for t, c in before.items() if t not in DROPPED}, shape(self.path), "the other tables' columns")
        self.assertEqual(counts, {table: query(self.path, "SELECT COUNT(*) FROM %s" % table)[0][0] for table in counts})
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])
        self.assertEqual([], [n for n in names(self.path, "index") if "name_review" in n or "damaged" in n])
        self.assertEqual(31, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_the_declaration_is_true(self):
        migration = [m for m in schema.MIGRATIONS if m.version == 31][0]
        self.assertEqual(schema.DESTRUCTIVE, migration.kind)
        self.assertEqual(DROPPED, tuple(migration.touches))
        self.assertEqual(DROPPED, tuple(schema.DROPPED_31))
        self.assertEqual(schema.DROPPED_31, schema.ONLY_DROPS[31])
        self.assertFalse(set(DROPPED) & (set(journal.KEYS) | set(journal.DERIVED)), "no change may write them")
        self.assertEqual([(), ()], [m.touches for m in schema.MIGRATIONS if m.version in (16, 28)], "they make nothing now")

    def test_a_library_with_empty_tables_and_one_that_never_had_them_migrate_the_same(self):
        make_legacy(self.path, rows=False)
        self.assertEqual(after(30), self.migrate())
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])
        self.assertEqual(1, len(backups(self.path)), "a library with rows in other tables is backed up either way")
        again = ViewLibrary(self, "bare")   # made at the latest, as a new library is: never had them
        self.assertEqual([], self.migrate(again.path))
        conn = db.connect(again.path)
        try:
            conn.execute("DELETE FROM schema_version WHERE version = 31")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(after(30), self.migrate(again.path))

    def test_a_new_library_has_neither_table(self):
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])


class TheJournal(AtThirty):
    def test_a_change_naming_a_dropped_table_loses_only_those_rows_and_one_left_empty_goes(self):
        make_legacy(self.path, journaled=True)
        changes = query(self.path, "SELECT COUNT(*) FROM changes")[0][0]
        rows = query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0]
        mixed = query(self.path, "SELECT id FROM changes WHERE operation = 'mixed'")[0][0]
        self.migrate()
        self.assertEqual(0, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation = 'dismiss'")[0][0])
        self.assertEqual([(mixed, "settings")], query(self.path, "SELECT change_id, table_name FROM change_rows WHERE change_id = ?",
                                                      (mixed,)))
        self.assertEqual(rows - 2, query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0])
        self.assertEqual(changes, query(self.path, "SELECT COUNT(*) FROM changes")[0][0], "one taken, the migration's own added")
        self.assertEqual([], query(self.path, "SELECT 1 FROM change_rows WHERE table_name IN (?, ?)", DROPPED))
        self.assertEqual([], query(self.path, "SELECT 1 FROM change_rows WHERE change_id NOT IN (SELECT id FROM changes)"))
        recorded = query(self.path, "SELECT operation, schema_version, status, summary FROM changes ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(("migration 31: the names set aside and the damaged files", 30, "applied"), recorded[:3])
        summary = json.loads(recorded[3])
        self.assertEqual("destructive", summary["kind"])
        self.assertEqual({"journal rows taken out": 2, "changes taken out": 1}, summary["took"])
        self.assertTrue(summary["backup"])

    def test_with_nothing_journaled_it_takes_nothing(self):
        make_legacy(self.path)
        self.migrate()
        recorded = query(self.path, "SELECT summary FROM changes ORDER BY id DESC LIMIT 1")[0][0]
        self.assertEqual({"journal rows taken out": 0, "changes taken out": 0}, json.loads(recorded)["took"])

    def test_a_change_made_at_30_is_still_undoable_after_it_and_its_undo_works(self):
        make_legacy(self.path)
        self.migrate()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            for change in self.made_at_30:
                self.assertEqual([], journal.refusal(conn, change), change)
            self.assertIsNone(journal.schema_gap_blocker(30, 31))
            self.assertIsNone(journal.schema_gap_blocker(28, 31))
        finally:
            conn.close()
        for change in reversed(self.made_at_30):
            undone = journal_service.undo(self.vl.library, change, apply=True)
            self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual([], query(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))
        self.assertEqual([], query(self.path, "SELECT 1 FROM photo_tags"))

    def test_the_migration_itself_is_not_undoable(self):
        make_legacy(self.path)
        self.migrate()
        change = query(self.path, "SELECT MAX(id) FROM changes")[0][0]
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertTrue(journal.refusal(conn, change))
        finally:
            conn.close()


class HowItFails(AtThirty):
    def held(self):
        return {"tables": names(self.path), "triggers": names(self.path, "trigger"),
                "rows": query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0],
                "changes": query(self.path, "SELECT COUNT(*) FROM changes")[0][0],
                "dismissed": query(self.path, "SELECT name_key FROM name_review_dismissals ORDER BY 1"),
                "damaged": query(self.path, "SELECT path FROM damaged_files")}

    def test_interrupted_after_the_drops_nothing_is_half_dropped_and_the_next_open_finishes(self):
        make_legacy(self.path, journaled=True)
        held = self.held()
        real = [m for m in schema.MIGRATIONS if m.version == 31][0]

        def crashes(conn):
            real.apply(conn)   # the journal pruned and both tables dropped, then the power goes
            raise RuntimeError("the power went")

        broken = tuple(m for m in schema.MIGRATIONS if m.version != 31) + (real._replace(apply=crashes),)
        with mock.patch.object(schema, "MIGRATIONS", broken):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertEqual(30, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual(held, self.held())
        self.assertEqual(1, len(backups(self.path)), "the copy was taken first, and is the way back")
        self.assertEqual(after(30), self.migrate(), "the next open does it")
        self.assertEqual(31, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])

    def test_a_check_that_fails_rolls_it_all_back(self):
        make_legacy(self.path, journaled=True)
        held = self.held()
        with mock.patch.object(schema.UnusedTablesGone, "after", return_value=["something is still there"]):
            with self.assertRaises(schema.CheckFailed):
                self.migrate()
        self.assertEqual(held, self.held())
        self.assertEqual(30, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_one_full_backup_is_taken_first_and_holds_the_tables_and_their_rows(self):
        make_legacy(self.path)
        self.assertEqual([], backups(self.path))
        self.migrate()
        taken = backups(self.path)
        self.assertEqual(1, len(taken))
        self.assertIn("before-migration-31", taken[0])
        copy = os.path.join(Library(self.path).backups, taken[0])
        self.assertLessEqual(set(DROPPED), names(copy))
        self.assertEqual(2, query(copy, "SELECT COUNT(*) FROM name_review_dismissals")[0][0])
        self.assertEqual(1, query(copy, "SELECT COUNT(*) FROM damaged_files")[0][0])
        self.assertEqual(30, query(copy, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_a_second_open_after_the_first_migrates_nothing_and_takes_no_backup(self):
        make_legacy(self.path)
        self.assertEqual(after(30), self.migrate())
        self.assertEqual([], self.migrate(), "another process opening it")
        self.assertEqual(1, len(backups(self.path)))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation LIKE 'migration 31%'")[0][0])

    def test_two_processes_opening_it_at_once_migrate_it_once(self):
        make_legacy(self.path)
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema; "
                "print(len(schema.ensure(%r)))" % (ROOT, self.path))
        env = dict(os.environ, TAGPUP_HOME=self.vl.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(["0", "1"], sorted(answers))
        self.assertEqual(1, len(backups(self.path)))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation LIKE 'migration 31%'")[0][0])

    def test_an_older_app_will_not_open_the_migrated_library(self):
        make_legacy(self.path)
        self.migrate()
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:30]), mock.patch.object(schema, "LATEST", 30):
            schema._current.clear()
            with self.assertRaises(schema.NewerLibrary) as refused:
                schema.ensure(self.path)
            self.assertTrue(schema.newer_problem(self.path))
        self.assertEqual((31, 30), (refused.exception.found, refused.exception.known))
        self.assertEqual(31, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0], "and writes nothing")

    def test_a_snapshot_from_before_it_is_migrated_again_when_opened(self):
        make_legacy(self.path)
        conn = db.connect(self.path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        snapshot = os.path.join(os.path.dirname(self.path), "snapshot-30.db")
        shutil.copyfile(self.path, snapshot)
        self.migrate()
        shutil.copyfile(snapshot, self.path)   # restored over the migrated library
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)
        self.assertEqual(after(30), self.migrate())
        self.assertEqual([], [name for name in names(self.path) if name in DROPPED])


if __name__ == "__main__":
    unittest.main()

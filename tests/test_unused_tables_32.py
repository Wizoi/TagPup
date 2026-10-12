"""Migration 32 drops `tag_taxonomy.is_people` and folds `sync_runs` into `job_runs` (tagpup.store.schema
`_drop_is_people_and_fold_syncs`; owner, 2026-10-11). The column is read and written by nothing and one live library still
has it (539 rows 0, 360 rows 1); a column drop rebuilds `tag_taxonomy`, a journaled table, parent of foreign keys and triggers,
so this holds the rebuild to keeping every id, row, index and trigger, and the fold to keeping what "last in step" showed.
Then, as test_unused_tables_31.py holds migration 31: one backup, one transaction, the journal consistent (every older change
still undoable, also one that recorded the column), a second open, two processes and an older app all safe.

A library at 31 is the library of today with `tag_taxonomy` rebuilt as the first schema made it (`is_people` in the middle),
`sync_runs` put back as migration 14 made it with rows as the old code wrote them, and its version put back. Fictional names.
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
from test_unused_tables import backups, names, query  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, job_runs, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: `tag_taxonomy` as the first schema made it: `is_people` in the middle, `has_face` last.
LEGACY_NODES = ("id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT UNIQUE, parent_id INTEGER, name TEXT, is_people INTEGER DEFAULT 0,"
                " hidden_from_autocomplete INTEGER DEFAULT 0, has_face INTEGER DEFAULT 0,"
                " FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE")

#: `sync_runs` as migration 14 made it.
LEGACY_SYNCS = ("CREATE TABLE sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT NOT NULL, finished TEXT NOT NULL,"
                " whole INTEGER NOT NULL, in_step INTEGER NOT NULL, found TEXT NOT NULL, changed TEXT NOT NULL, change_id INTEGER)")

FOUND = {"rows": 60, "files": 61, "new": 0, "missing": 1, "review_folders": 2}
CHANGED = {"rows": 0, "from_files": 0, "relinked": 0, "queued_folders": 0}


def with_is_people(path):
    """Rebuild `tag_taxonomy` of the library at `path` (at the latest) as a library from before 32 holds it, the People nodes
    1 and the others 0. The version is left at the latest: the caller puts it back once it has made its old changes."""
    conn = db.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        schema.rebuild_table(conn, "tag_taxonomy", LEGACY_NODES)
        conn.execute("UPDATE tag_taxonomy SET is_people = 1 WHERE tag IN ('People', 'People/Rowan Thackeray')")
        conn.commit()
    finally:
        conn.close()
    schema._current.clear()


def add_syncs(path, syncs):
    """`sync_runs` as migration 14 made it, holding `syncs`: (started, finished, whole, in_step, found, changed, change_id)."""
    conn = db.connect(path)
    try:
        conn.execute(LEGACY_SYNCS)
        for started, finished, whole, in_step, found, changed, change_id in syncs:
            conn.execute("INSERT INTO sync_runs (started, finished, whole, in_step, found, changed, change_id)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (started, finished, whole, in_step, json.dumps(found, sort_keys=True), json.dumps(changed, sort_keys=True),
                          change_id))
        conn.commit()
    finally:
        conn.close()


def at_thirty_one(path, made_at_31=()):
    """Put the version of the library at `path` back at 31, and `made_at_31` (change ids) with it."""
    conn = db.connect(path)
    try:
        conn.executemany("UPDATE changes SET schema_version = 31 WHERE id = ?", [(i,) for i in made_at_31])
        conn.execute("DELETE FROM schema_version WHERE version = 32")
        conn.commit()
    finally:
        conn.close()
    schema._current.clear()


def nodes(path):
    """Every node by id, with every column but `is_people`, and the type of each value."""
    columns = sorted(c for c in columns_of(path, "tag_taxonomy") if c != "is_people")
    return query(path, "SELECT %s FROM tag_taxonomy ORDER BY id" % ", ".join(columns)), \
        query(path, "SELECT %s FROM tag_taxonomy ORDER BY id" % ", ".join("typeof(%s)" % c for c in columns))


def objects(path):
    return query(path, "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger', 'view') AND sql IS NOT NULL"
                       " ORDER BY name")


def columns_of(path, table):
    return [row[1] for row in query(path, "PRAGMA table_info(%s)" % table)]


class AtThirtyOne(unittest.TestCase):
    """A library of photos and a tree, `tag_taxonomy` as a library from before 32 holds it, and two changes made at 31."""

    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("People/Rowan Thackeray", "Trips/Coast", "Trips/Coast/Cliffs", "Events/Fair", face_root="People")
        self.photos = [vl.photo("Trip", "a.jpg", tags=["Trips/Coast"]), vl.photo("Trip", "b.jpg", tags=["People/Rowan Thackeray"])]
        self.path = vl.path
        vl.conn.close()
        with_is_people(self.path)
        # Changes made while the column was there: the rows they record hold it.
        node = query(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Events/Fair'")[0][0]
        held = query(self.path, "SELECT tags FROM photos WHERE id = ?", (self.photos[0],))[0][0]
        self.deleted = journal.apply(self.path, "delete a node", [journal.delete(
            "tag_taxonomy", (node,), {"tag": "Events/Fair"})]).change_id
        self.added = journal.apply(self.path, "add a node", [journal.insert(
            "tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})]).change_id
        self.retagged = journal.apply(self.path, "retag", [journal.update(
            "photos", (self.photos[0],), {"tags": held}, {"tags": json.dumps(["Fresh"])})]).change_id
        self.made_at_31 = [self.deleted, self.added, self.retagged]
        at_thirty_one(self.path, self.made_at_31)

    def migrate(self, path=None):
        schema._current.clear()
        return schema.ensure(path or self.path)


class TheColumn(AtThirtyOne):
    def test_the_recorded_rows_hold_the_column(self):
        """The fixture is the live shape: a node a change deleted or inserted is recorded with `is_people`."""
        self.assertEqual({"delete", "insert"}, {a for (a,) in query(
            self.path, "SELECT action FROM change_rows WHERE column_name = 'is_people'")})
        self.assertEqual(1, query(self.path, "SELECT is_people FROM tag_taxonomy WHERE tag = 'People'")[0][0])
        self.assertEqual(0, query(self.path, "SELECT is_people FROM tag_taxonomy WHERE tag = 'Trips'")[0][0], "both shapes")
        self.assertEqual(["id", "tag", "parent_id", "name", "is_people", "hidden_from_autocomplete", "has_face"],
                         columns_of(self.path, "tag_taxonomy"))

    def test_it_drops_the_column_and_keeps_every_id_row_index_and_trigger(self):
        rows, types = nodes(self.path)
        marks, triggers = objects(self.path), names(self.path, "trigger")
        counter = query(self.path, "SELECT seq FROM sqlite_sequence WHERE name = 'tag_taxonomy'")
        counts = {t: query(self.path, 'SELECT COUNT(*) FROM "%s"' % t)[0][0] for t in names(self.path)
                  if not t.startswith("search_") and t not in ("changes", "change_rows", "schema_version", "job_runs")}
        self.assertEqual(after(31), self.migrate())
        self.assertEqual(["id", "tag", "parent_id", "name", "has_face", "hidden_from_autocomplete"],
                         columns_of(self.path, "tag_taxonomy"), "as a new library makes it")
        self.assertEqual((rows, types), nodes(self.path), "every row, by id, with the type of each value")
        self.assertEqual(marks, objects(self.path), "every index, trigger and view, as it was")
        self.assertEqual(triggers, names(self.path, "trigger"))
        self.assertEqual(counter, query(self.path, "SELECT seq FROM sqlite_sequence WHERE name = 'tag_taxonomy'"),
                         "a node deleted by the last change is not given its id again")
        self.assertEqual(counts, {t: query(self.path, 'SELECT COUNT(*) FROM "%s"' % t)[0][0] for t in counts})
        self.assertEqual([], query(self.path, "PRAGMA foreign_key_check"))
        self.assertEqual([("ok",)], query(self.path, "PRAGMA quick_check"))
        self.assertEqual(32, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_the_triggers_and_the_cascade_still_work(self):
        self.migrate()
        conn = db.connect(self.path)
        try:
            node = conn.execute("SELECT tag_id FROM photo_tags LIMIT 1").fetchone()[0]   # a node a photo is filed under
            before = conn.execute("SELECT value FROM generations WHERE name = 'taxonomy'").fetchone()
            conn.execute("DELETE FROM tag_taxonomy WHERE id = ?", (node,))
            self.assertIsNone(conn.execute("SELECT 1 FROM photo_tags WHERE tag_id = ?", (node,)).fetchone(),
                              "photo_tags_go_with_their_node")
            self.assertNotEqual(before, conn.execute("SELECT value FROM generations WHERE name = 'taxonomy'").fetchone(),
                                "the generation trigger moves")
            conn.rollback()
            # A node that is the next id is given, and the parent's cascade is still declared.
            conn.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Later', 'Later')")
            self.assertEqual(query(self.path, "SELECT seq FROM sqlite_sequence WHERE name = 'tag_taxonomy'")[0][0] + 1,
                             conn.execute("SELECT id FROM tag_taxonomy WHERE tag = 'Later'").fetchone()[0])
            conn.rollback()
        finally:
            conn.close()
        self.assertIn(("tag_taxonomy", "parent_id", "CASCADE"), [
            (row[2], row[3], row[6]) for row in query(self.path, "PRAGMA foreign_key_list(tag_taxonomy)")])

    def test_a_library_without_the_column_is_changed_in_nothing_but_its_version(self):
        again = ViewLibrary(self, "bare")   # made at the latest, as a new library, and two of the three live libraries, are
        again.conn.close()
        at_thirty_one(again.path)
        before = {"nodes": nodes(again.path), "objects": objects(again.path), "sql": query(
            again.path, "SELECT sql FROM sqlite_master WHERE name = 'tag_taxonomy'")}
        self.assertEqual(after(31), self.migrate(again.path))
        self.assertEqual(before, {"nodes": nodes(again.path), "objects": objects(again.path), "sql": query(
            again.path, "SELECT sql FROM sqlite_master WHERE name = 'tag_taxonomy'")}, "the table was not rebuilt")
        recorded = query(again.path, "SELECT summary FROM changes ORDER BY id DESC LIMIT 1")[0][0]
        self.assertEqual({"tag_taxonomy rows rebuilt": 0, "journal rows taken out": 0, "changes taken out": 0,
                          "sync records kept": 0, "sync records dropped": 0}, json.loads(recorded)["took"])

    def test_the_declaration_is_true(self):
        migration = [m for m in schema.MIGRATIONS if m.version == 32][0]
        self.assertEqual(schema.DESTRUCTIVE, migration.kind)
        self.assertEqual({"tag_taxonomy": ("is_people",)}, schema.COLUMNS_DROPPED[32])
        self.assertNotIn(32, schema.ONLY_DROPS, "it rebuilds a journaled table: the column's own declaration")
        before = columns_of(self.path, "tag_taxonomy")
        self.migrate()
        self.assertEqual({"is_people"}, set(before) - set(columns_of(self.path, "tag_taxonomy")))
        self.assertEqual([m.touches for m in schema.MIGRATIONS if m.version == 14], [()], "14 makes nothing now")

    def test_a_library_whose_column_came_from_an_older_schema_not_from_the_helper_migrates_too(self):
        """The fixtures above rebuild the table with schema.rebuild_table, the code under test. This one is made by the
        migrations up to 31 and gains the column by ALTER TABLE, a path of its own (the column lands last, as it did in no live
        library, but nothing here is the rebuild)."""
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:31]), mock.patch.object(schema, "LATEST", 31):
            old = ViewLibrary(self, "older")
            old.tree("People/Rowan Thackeray", "Trips/Coast", face_root="People")
            old.photo("Trip", "a.jpg", tags=["Trips/Coast"])
            old.conn.close()
            self.assertEqual(31, query(old.path, "SELECT MAX(version) FROM schema_version")[0][0])
        conn = db.connect(old.path)
        try:
            conn.execute("ALTER TABLE tag_taxonomy ADD COLUMN is_people INTEGER DEFAULT 0")
            conn.execute("UPDATE tag_taxonomy SET is_people = 1 WHERE tag = 'People/Rowan Thackeray'")
            conn.commit()
        finally:
            conn.close()
        rows, types = nodes(old.path)
        marks = objects(old.path)
        schema._current.clear()
        self.assertEqual(after(31), schema.ensure(old.path))
        self.assertNotIn("is_people", columns_of(old.path, "tag_taxonomy"))
        self.assertEqual((rows, types), nodes(old.path))
        self.assertEqual(marks, objects(old.path))
        self.assertEqual([], query(old.path, "PRAGMA foreign_key_check"))

    def test_the_journal_rows_that_name_the_column_are_taken_out_and_the_rest_of_their_change_stays(self):
        named = query(self.path, "SELECT COUNT(*) FROM change_rows WHERE column_name = 'is_people'")[0][0]
        others = query(self.path, "SELECT COUNT(*) FROM change_rows WHERE column_name != 'is_people'")[0][0]
        self.assertGreater(named, 0)
        self.migrate()
        self.assertEqual(0, query(self.path, "SELECT COUNT(*) FROM change_rows WHERE column_name = 'is_people'")[0][0])
        self.assertEqual(others, query(self.path, "SELECT COUNT(*) FROM change_rows")[0][0], "no other row went")
        taken = json.loads(query(self.path, "SELECT summary FROM changes ORDER BY id DESC LIMIT 1")[0][0])["took"]
        self.assertEqual((named, 0), (taken["journal rows taken out"], taken["changes taken out"]))
        self.assertEqual(1, len(query(self.path, "SELECT 1 FROM changes WHERE id = ?", (self.deleted,))), "the change stays")


def syncs():
    """A history as the old code left it: 3 whole syncs (the newest in step is the second), folder syncs between."""
    found = dict(FOUND)
    return [("2026-10-08 08:00:00", "2026-10-08 08:00:30", 1, 1, found, CHANGED, None),
            ("2026-10-09 08:00:00", "2026-10-09 08:00:40", 1, 1, dict(found, new=0), dict(CHANGED, rows=3, from_files=3), 17),
            ("2026-10-09 09:00:00", "2026-10-09 09:00:02", 0, 1, found, CHANGED, None),
            ("2026-10-10 08:00:00", "2026-10-10 08:00:50", 1, 0, dict(found, new=4), dict(CHANGED, queued_folders=1), None),
            ("2026-10-10 09:00:00", "2026-10-10 09:00:02", 0, 0, dict(found, new=1), CHANGED, None)]


class TheSyncs(AtThirtyOne):
    def test_the_newest_whole_sync_in_step_becomes_a_run_and_the_rest_are_dropped(self):
        add_syncs(self.path, syncs())
        runs = query(self.path, "SELECT COUNT(*) FROM job_runs")[0][0]
        rebuilt = query(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0]
        self.migrate()
        self.assertNotIn("sync_runs", names(self.path))
        self.assertEqual(runs + 1, query(self.path, "SELECT COUNT(*) FROM job_runs")[0][0])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            kept = job_runs.newest_sync(conn)
            self.assertEqual(kept, job_runs.newest_sync(conn, whole=True))
            self.assertIsNone(job_runs.newest_sync(conn, whole=False), "the folder syncs are not kept")
            self.assertEqual("2026-10-09 08:00:40", job_runs.last_sync(conn)["last_in_step"], "what 'last in step' showed")
        finally:
            conn.close()
        self.assertEqual(("2026-10-09 08:00:00", "2026-10-09 08:00:40", True, True, 17, "harbour"),
                         (kept["started"], kept["finished"], kept["whole"], kept["in_step"], kept["change_id"],
                          query(self.path, "SELECT library FROM job_runs WHERE id = ?", (kept["id"],))[0][0]))
        self.assertEqual(dict(FOUND, new=0), kept["found"])
        self.assertEqual(dict(CHANGED, rows=3, from_files=3), kept["changed"])
        summary = json.loads(query(self.path, "SELECT summary FROM changes ORDER BY id DESC LIMIT 1")[0][0])["took"]
        self.assertEqual((rebuilt, 1, 4), (summary["tag_taxonomy rows rebuilt"], summary["sync records kept"],
                                           summary["sync records dropped"]))

    def test_it_writes_what_record_sync_writes(self):
        add_syncs(self.path, syncs())
        self.migrate()
        folded = query(self.path, "SELECT job, outcome, changed FROM job_runs WHERE job = ?", (schema.SYNC_WHOLE_JOB,))
        self.assertEqual(job_runs.SYNC_WHOLE, schema.SYNC_WHOLE_JOB)
        other = ViewLibrary(self, "other")
        job_runs.record_sync(other.path, "2026-10-09 08:00:00", True, True, dict(FOUND, new=0), dict(CHANGED, rows=3, from_files=3), 17)
        self.assertEqual(folded, query(other.path, "SELECT job, outcome, changed FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,)))

    def test_with_no_record_there_is_none_to_keep(self):
        add_syncs(self.path, [])
        self.migrate()
        self.assertNotIn("sync_runs", names(self.path))
        self.assertEqual([], query(self.path, "SELECT 1 FROM job_runs WHERE job IN (?, ?)", job_runs.SYNC_JOBS))

    def test_with_none_in_step_there_is_none_to_keep(self):
        add_syncs(self.path, [syncs()[3], syncs()[2]])
        self.migrate()
        self.assertEqual([], query(self.path, "SELECT 1 FROM job_runs WHERE job IN (?, ?)", job_runs.SYNC_JOBS))
        self.assertNotIn("sync_runs", names(self.path))

    def test_with_many_records_the_one_is_kept(self):
        def at(n):
            return "2026-10-%02d %02d:%02d:00" % (1 + n // 100, (n % 100) // 4, (n % 4) * 15)

        # Every 50th is of the whole library, and those of every 3rd are not in step: the newest in step is 400.
        many = [(at(n), at(n), int(n % 50 == 0), int(n % 3 != 0), FOUND, CHANGED, None) for n in range(500)]
        add_syncs(self.path, many)
        self.migrate()
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0])
        self.assertEqual(0, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_FOLDER,))[0][0])
        self.assertEqual(at(400), query(self.path, "SELECT started FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0])

    def test_the_other_runs_of_the_library_are_left_as_they_are(self):
        conn = db.connect(self.path)
        try:
            conn.execute("INSERT INTO job_runs (job, library, started, finished, outcome, changed) VALUES"
                         " ('snapshots', 'harbour', '2026-10-10 03:00:00', '2026-10-10 03:00:05', 'done', '{\"taken\": 1}')")
            conn.commit()
        finally:
            conn.close()
        add_syncs(self.path, syncs())
        before = query(self.path, "SELECT * FROM job_runs ORDER BY id")
        self.migrate()
        self.assertEqual(before, query(self.path, "SELECT * FROM job_runs WHERE id <= ? ORDER BY id", (before[-1][0],)))

    def test_a_sync_that_ends_while_the_library_is_upgrading_records_after_it(self):
        """The sync's record opens the library (schema.ensure), which migrates it under the write lock first: the record is a
        run of job_runs beside the folded one, and nothing is written to a table that is gone."""
        add_syncs(self.path, syncs())
        schema._current.clear()
        run_id = job_runs.record_sync(self.path, "2026-10-11 08:00:00", True, False, dict(FOUND, new=2), CHANGED, None)
        self.assertNotIn("sync_runs", names(self.path))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(run_id, job_runs.newest_sync(conn)["id"])
            self.assertEqual("2026-10-09 08:00:40", job_runs.last_sync(conn)["last_in_step"])
            self.assertFalse(job_runs.last_sync(conn)["last_run"]["in_step"])
        finally:
            conn.close()
        self.assertEqual(2, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0])


class TheJournal(AtThirtyOne):
    def test_every_change_made_at_31_is_undoable_after_it_also_the_delete_whose_column_rows_were_taken_out(self):
        self.migrate()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            for change in self.made_at_31:
                self.assertEqual([], journal.refusal(conn, change), change)
            self.assertIsNone(journal.schema_gap_blocker(31, 32))
            self.assertIsNone(journal.schema_gap_blocker(30, 32))
            self.assertIsNone(journal.schema_gap_blocker(28, 32))
        finally:
            conn.close()
        for change in (self.retagged, self.added):
            undone = journal_service.undo(self.vl.library, change, apply=True)
            self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual([], query(self.path, "SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"), "the added node is gone")
        gone_node = self.deleted
        undone = journal_service.undo(self.vl.library, gone_node, apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        back = query(self.path, "SELECT id, tag, name FROM tag_taxonomy WHERE tag = 'Events/Fair'")
        self.assertEqual(1, len(back), "the deleted node is back, under its own id, without the column")
        self.assertNotIn("is_people", columns_of(self.path, "tag_taxonomy"))

    def test_without_the_declaration_every_older_change_would_be_refused_and_says_so(self):
        """What the owner would see were COLUMNS_DROPPED missing: each change of the journal, refused with the migration named."""
        self.migrate()
        with mock.patch.object(schema, "COLUMNS_DROPPED", {}):
            conn = db.connect(db.readonly_uri(self.path), uri=True)
            try:
                refused = journal.refusal(conn, self.added)
            finally:
                conn.close()
        self.assertEqual(1, len(refused))
        self.assertIn("migration 32", refused[0])
        self.assertIn("destructive", refused[0])

    def test_a_change_made_at_30_is_undoable_too(self):
        self.assertIsNone(journal.schema_gap_blocker(30, 32))

    def test_the_migration_itself_is_not_undoable(self):
        self.migrate()
        change = query(self.path, "SELECT MAX(id) FROM changes")[0][0]
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertTrue(journal.refusal(conn, change))
        finally:
            conn.close()
        recorded = query(self.path, "SELECT operation, schema_version, status, summary FROM changes WHERE id = ?", (change,))[0]
        self.assertEqual(("migration 32: no is_people, and syncs among the job runs", 31, "applied"), recorded[:3])
        self.assertEqual("destructive", json.loads(recorded[3])["kind"])
        self.assertTrue(json.loads(recorded[3])["backup"])


class HowItFails(AtThirtyOne):
    def held(self):
        return {"tables": names(self.path), "triggers": names(self.path, "trigger"),
                "columns": columns_of(self.path, "tag_taxonomy"), "nodes": nodes(self.path),
                "syncs": query(self.path, "SELECT * FROM sync_runs ORDER BY id"),
                "job_runs": query(self.path, "SELECT COUNT(*) FROM job_runs")[0][0],
                "changes": query(self.path, "SELECT COUNT(*) FROM changes")[0][0]}

    def setUp(self):
        super().setUp()
        add_syncs(self.path, syncs())

    def test_interrupted_after_both_halves_nothing_is_half_done_and_the_next_open_finishes(self):
        held = self.held()
        real = [m for m in schema.MIGRATIONS if m.version == 32][0]

        def crashes(conn):
            real.apply(conn)   # the table rebuilt, the syncs folded and dropped, then the power goes
            raise RuntimeError("the power went")

        broken = tuple(m for m in schema.MIGRATIONS if m.version != 32) + (real._replace(apply=crashes),)
        with mock.patch.object(schema, "MIGRATIONS", broken):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertEqual(31, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertEqual(held, self.held(), "rolled back whole: the column, the nodes, the syncs, the runs")
        self.assertEqual(1, len(backups(self.path)), "the copy was taken first, and is the way back")
        self.assertEqual(after(31), self.migrate(), "the next open does it")
        self.assertEqual(32, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertNotIn("is_people", columns_of(self.path, "tag_taxonomy"))
        self.assertNotIn("sync_runs", names(self.path))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0])

    def test_a_check_that_fails_rolls_it_all_back(self):
        held = self.held()
        with mock.patch.object(schema.SyncRunsFolded, "after", return_value=["something is wrong"]):
            with self.assertRaises(schema.CheckFailed):
                self.migrate()
        self.assertEqual(held, self.held())
        self.assertEqual(31, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_one_full_backup_is_taken_first_and_holds_the_column_and_the_syncs(self):
        self.assertEqual([], backups(self.path))
        self.migrate()
        taken = backups(self.path)
        self.assertEqual(1, len(taken))
        self.assertIn("before-migration-32", taken[0])
        copy = os.path.join(Library(self.path).backups, taken[0])
        self.assertIn("is_people", columns_of(copy, "tag_taxonomy"))
        self.assertEqual(5, query(copy, "SELECT COUNT(*) FROM sync_runs")[0][0])
        self.assertEqual(31, query(copy, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_the_library_is_being_migrated_while_it_runs(self):
        """A request for it is answered 503 at once (tagpup.web.libraries: schema.migrating), not left on its write lock."""
        seen = []
        real = [m for m in schema.MIGRATIONS if m.version == 32][0]

        def watching(conn):
            seen.append(schema.migrating(self.path))
            return real.apply(conn)

        patched = tuple(m for m in schema.MIGRATIONS if m.version != 32) + (real._replace(apply=watching),)
        with mock.patch.object(schema, "MIGRATIONS", patched):
            self.migrate()
        self.assertEqual([True], seen)
        self.assertFalse(schema.migrating(self.path))

    def test_a_second_open_after_the_first_migrates_nothing_and_takes_no_backup(self):
        self.assertEqual(after(31), self.migrate())
        self.assertEqual([], self.migrate(), "another process opening it")
        self.assertEqual(1, len(backups(self.path)))
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM changes WHERE operation LIKE 'migration 32%'")[0][0])
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0])

    def test_two_processes_opening_it_at_once_migrate_it_once(self):
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
        self.assertEqual(1, query(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_WHOLE,))[0][0],
                         "folded once, not twice")

    def test_an_older_app_will_not_open_the_migrated_library(self):
        self.migrate()
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:31]), mock.patch.object(schema, "LATEST", 31):
            schema._current.clear()
            with self.assertRaises(schema.NewerLibrary) as refused:
                schema.ensure(self.path)
            self.assertTrue(schema.newer_problem(self.path))
        self.assertEqual((32, 31), (refused.exception.found, refused.exception.known))
        self.assertEqual(32, query(self.path, "SELECT MAX(version) FROM schema_version")[0][0], "and writes nothing")

    def test_a_snapshot_from_before_it_is_migrated_again_when_opened(self):
        conn = db.connect(self.path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        snapshot = os.path.join(os.path.dirname(self.path), "snapshot-31.db")
        shutil.copyfile(self.path, snapshot)
        self.migrate()
        shutil.copyfile(snapshot, self.path)   # restored over the migrated library
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)
        self.assertEqual(after(31), self.migrate())
        self.assertNotIn("is_people", columns_of(self.path, "tag_taxonomy"))
        self.assertNotIn("sync_runs", names(self.path))


if __name__ == "__main__":
    unittest.main()

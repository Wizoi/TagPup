"""Each migration says what it is, and the runner holds it to that (docs/ARCHITECTURE.md,
phase 7.5; tagpup.store.schema).

A migration marked as changing data took a full copy of the library first -- 1.4 GB of
photo_index, five kept -- and one that did not was trusted. Now each declares its kind:
one that adds takes nothing and is watched to change nothing that was there; one that
changes data has every row it changed recorded in the journal, undoable while it is the
newest; one that destroys takes one backup, under the write lock. Each runs in one
transaction with the checks it names, and a failed check rolls it back. Every run is a
row of `changes`. A table is rebuilt only by SQLite's twelve steps (`rebuild_table`).

Libraries are seeded in plain SQL, as the indexer and the older versions stored them,
in homes of their own. The names are fictional.
"""
import ast
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
from journal_library import JournalLibrary  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: What each migration is, from its code. A new one is added here, or this fails.
KINDS = {
    14: schema.ADDITIVE,     # the baseline: a new library's tables; makes nothing in an existing one
    15: schema.ADDITIVE,     # the added_folders table
    16: schema.ADDITIVE,     # makes nothing now (it made damaged_files, which 31 drops)
    17: schema.ADDITIVE,     # the faces_pending table
    18: schema.ADDITIVE,     # the roots table, empty
    19: schema.ADDITIVE,     # photo_tags, folders and photo_folder, derived from the photos
    20: schema.ADDITIVE,     # idx_photos_taken and idx_photos_year: the indexes the library views page by
    21: schema.ADDITIVE,     # faces.tag_id and photo_people.tag_id, filled from the name and the tree
    22: schema.ADDITIVE,     # idx_photos_name: the index a library view orders by file name with
    23: schema.ADDITIVE,     # idx_photos_caption: the index a library view orders by caption with
    24: schema.ADDITIVE,     # search_words, search_names: the word index a search matches words in (derived)
    25: schema.ADDITIVE,     # the faces_detected table, empty
    26: schema.ADDITIVE,     # makes nothing now (it made library_identity and folder_ids, empty)
    27: schema.ADDITIVE,     # makes nothing now (it made search_gear, the camera and lens words)
    28: schema.ADDITIVE,     # idx_faces_tag and idx_photo_people_tag (it made name_review_dismissals, empty, which 31 drops)
    29: schema.ADDITIVE,     # the trigger that keeps a person faces name from being deleted; the faces' generation moves with tag_id
    30: schema.DESTRUCTIVE,  # drops search_gear, photo_meta, folder_ids and library_identity (one backup)
    31: schema.DESTRUCTIVE,  # drops name_review_dismissals and damaged_files (one backup)
    32: schema.DESTRUCTIVE,  # drops tag_taxonomy.is_people (a rebuild) and folds sync_runs into job_runs (one backup)
}


#: The migrations that only make indexes (and, for 29, a trigger), or make nothing in a library that exists (14, 16, 26 and 27): they change no row of any table.
INDEX_ONLY = (14, 16, 20, 22, 23, 26, 27, 28, 29)


class TheNamesTestsExpect(unittest.TestCase):
    """tests/migration_names.py: the one place a test gets the names of the migrations a library at an older schema
    runs, so a new migration is declared in KINDS and nowhere else (#689)."""

    def test_the_names_follow_the_migrations_and_a_new_one_joins_them(self):
        self.assertEqual([], migration_names.after(schema.LATEST))
        last = schema.MIGRATIONS[-1]
        self.assertEqual([last.name], migration_names.after(last.version - 1))
        self.assertEqual(["migration %d: %s" % (last.version, last.name)], migration_names.operations_after(last.version - 1))
        self.assertEqual([last.name], migration_names.named(last.version))
        added = step(schema.ADDITIVE, lambda conn: None)
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS + (added,)):
            self.assertEqual([last.name, "a step"], migration_names.after(last.version - 1))


def backups(db_path):
    folder = Library(db_path).backups
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def step(kind, apply, touches=(), checks=None, version=None, name="a step"):
    """A migration after the last, for a test."""
    return schema.Migration(version or schema.LATEST + 1, name, apply, kind, "a test's", tuple(touches),
                            (schema.RowsKept(but=touches),) + schema.STANDARD if checks is None else checks)


class Migrated(JournalLibrary):
    """A library at the latest schema with rows like the owner's, and migrations after."""

    def migrate(self, *added):
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS + added), \
                mock.patch.object(schema, "LATEST", added[-1].version):
            schema._current.clear()   # the next process to open it
            return schema.ensure(self.db_path)

    def version(self):
        return self.query("SELECT MAX(version) FROM schema_version")[0][0]

    def recorded(self):
        """(operation, schema_version, status, summary) of each change, oldest first."""
        return [(op, version, status, json.loads(summary)) for op, version, status, summary in self.query(
            "SELECT operation, schema_version, status, summary FROM changes ORDER BY id")]

    def newest_change(self):
        return self.query("SELECT MAX(id) FROM changes")[0][0]

    def tables(self):
        return {name for (name,) in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}


# ---- Additive ------------------------------------------------------------------------------

class AnAdditiveMigration(Migrated):
    @staticmethod
    def add(conn):
        conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)")
        conn.execute("CREATE INDEX idx_faces_prob ON faces(prob)")
        conn.execute("ALTER TABLE photos ADD COLUMN rating INTEGER")
        conn.execute("UPDATE photos SET rating = 0")   # a column it adds, filled

    def test_takes_no_backup_and_is_recorded(self):
        self.migrate(step(schema.ADDITIVE, self.add, ("albums", "faces", "photos")))
        self.assertEqual([], backups(self.db_path))
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration %d: a step" % (schema.LATEST + 1), schema.LATEST, "applied"),
                         (operation, version, status))
        self.assertEqual(schema.ADDITIVE, summary["kind"])
        self.assertEqual({}, summary["rows"])
        self.assertEqual(["nothing dropped", "nothing that was there changed", "rows kept", "foreign keys",
                          "integrity"], summary["checks"])

    def test_cannot_be_undone(self):
        # A schema is not undone: the change is at the version before, and refused.
        self.migrate(step(schema.ADDITIVE, self.add, ("albums",)))
        with self.assertRaises(journal.Refusal) as refused:
            journal.undo(self.db_path, self.newest_change())
        self.assertIn("a migration is not undone", str(refused.exception))

    def test_that_changes_a_row_is_rolled_back(self):
        face = self.ids["kept"]

        def rename(conn):
            conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY)")
            conn.execute("UPDATE faces SET name = 'Tamsin Rook' WHERE id = ?", (face,))

        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.ADDITIVE, rename, ("albums",)))
        self.assertEqual("nothing that was there changed", failed.exception.check)
        self.assertIn("faces: 1 updated", str(failed.exception))
        self.assertNotIn("Tamsin", str(failed.exception))
        self.assertEqual(schema.LATEST, self.version())
        self.assertNotIn("albums", self.tables())
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces WHERE id = ?", (face,)))

    def test_that_drops_a_column_is_refused(self):
        def drop(conn):
            conn.execute("ALTER TABLE suggestions DROP COLUMN error")

        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.ADDITIVE, drop, ("suggestions",)))
        self.assertEqual("nothing dropped", failed.exception.check)
        self.assertEqual(schema.LATEST, self.version())


# ---- Changing data -------------------------------------------------------------------------

class ADataChangingMigration(Migrated):
    def setUp(self):
        super().setUp()
        self.before = self.dump()

    def change(self, conn):
        conn.execute("UPDATE faces SET name = 'Maren Oakhollow', name_source = 'manual' WHERE id = ?",
                     (self.ids["copy"],))
        conn.execute("DELETE FROM tag_taxonomy WHERE tag = 'Activity/Sailing'")
        conn.execute("INSERT INTO suggestions (photo_id, tags, people, title, raw, before_consensus, model, created)"
                     " VALUES (?, '[]', '[]', 'Prize giving', '{}', 1, 'm', '2026-09-25 09:00:00')", (self.ids["finish"],))
        conn.execute("UPDATE photos SET raw_metadata = json_set(raw_metadata, ?, '2023:07:01 12:00:00') WHERE id = ?",
                     ('$."EXIF:DateTimeOriginal"', self.ids["prize"]))

    def run_it(self):
        self.migrate(step(schema.DATA, self.change, ("faces", "tag_taxonomy", "suggestions", "photos")))
        return self.newest_change()

    def test_takes_no_backup(self):
        self.run_it()
        self.assertEqual([], backups(self.db_path))

    def test_records_every_row_it_changed(self):
        change_id = self.run_it()
        entry = journal.history(self.db_path, change_id=change_id)[0]
        self.assertEqual("migration %d: a step" % (schema.LATEST + 1), entry["operation"])
        self.assertEqual(schema.LATEST + 1, entry["schema_version"])
        self.assertEqual({"faces": {"update": 1}, "tag_taxonomy": {"delete": 1}, "suggestions": {"insert": 1},
                          "photos": {"update": 1}}, entry["rows"])
        self.assertEqual({"faces": [[self.ids["copy"]]], "tag_taxonomy": [[self.ids["Activity/Sailing"]]],
                          "suggestions": [[self.ids["finish"]]], "photos": [[self.ids["prize"]]]}, entry["keys"])
        self.assertIn("every change recorded", entry["summary"]["checks"])

    def test_is_undone_while_it_is_the_newest(self):
        change_id = self.run_it()
        self.assertNotEqual(self.before, self.dump())
        self.assertTrue(journal.rehearse_undo(self.db_path, change_id).exact)
        journal.undo(self.db_path, change_id)
        self.assertEqual(self.before, self.dump())

    def test_is_not_undone_once_another_migration_has_run(self):
        change_id = self.run_it()
        after = step(schema.ADDITIVE, lambda conn: conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY)"),
                     ("albums",), version=schema.LATEST + 2)
        self.migrate(step(schema.DATA, self.change, ("faces",)), after)
        with self.assertRaises(journal.Refusal):
            journal.undo(self.db_path, change_id)

    def test_that_writes_a_table_the_journal_does_not_record_is_refused(self):
        def embed(conn):
            conn.execute("INSERT INTO tag_embeddings (tag, prompt, model_name, pretrained, embedding)"
                         " VALUES ('Activity/Sailing', 'a photo of sailing', 'ViT-B-32', 'laion', x'00')")

        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.DATA, embed, ("tag_embeddings",)))
        self.assertEqual("every change recorded", failed.exception.check)
        self.assertEqual([], self.query("SELECT tag FROM tag_embeddings"))
        self.assertEqual(schema.LATEST, self.version())


# ---- Destroying ----------------------------------------------------------------------------

SUGGESTIONS_WITHOUT_ERROR = ("photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,"
                             " tags TEXT, people TEXT, title TEXT, raw TEXT,"
                             " before_consensus INTEGER NOT NULL DEFAULT 0, model TEXT, created TEXT")


class ADestructiveMigration(Migrated):
    @staticmethod
    def drop(conn):
        schema.rebuild_table(conn, "suggestions", SUGGESTIONS_WITHOUT_ERROR)

    def test_takes_one_backup_and_names_it(self):
        self.migrate(step(schema.DESTRUCTIVE, self.drop, ("suggestions",)))
        taken = backups(self.db_path)
        self.assertEqual(1, len(taken))
        operation, version, _status, summary = self.recorded()[-1]
        self.assertEqual((schema.DESTRUCTIVE, taken[0]), (summary["kind"], summary["backup"]))
        self.assertEqual(schema.LATEST, version)
        # The copy is the library as the migration found it.
        conn = db.connect(db.readonly_uri(os.path.join(Library(self.db_path).backups, taken[0])), uri=True)
        self.addCleanup(conn.close)
        self.assertIn("error", [row[1] for row in conn.execute("PRAGMA table_info(suggestions)")])

    def test_two_in_one_run_take_one_backup(self):
        self.migrate(step(schema.DESTRUCTIVE, self.drop, ("suggestions",)),
                     step(schema.DESTRUCTIVE, lambda conn: conn.execute("DROP TABLE tag_embeddings"), (),
                          version=schema.LATEST + 2))
        self.assertEqual(1, len(backups(self.db_path)))

    def test_backs_up_a_library_with_a_tree_and_no_photos(self):
        # Only photos counted: a library holding a tree, faces' names or a journal and no
        # photo rows lost them to a destructive migration with no copy taken.
        for table in ("suggestions", "embeddings", "face_crops", "faces", "photos"):
            self.execute("DELETE FROM %s" % table)
        self.migrate(step(schema.DESTRUCTIVE, lambda conn: conn.execute("DROP TABLE tag_embeddings"), ()))
        self.assertEqual(1, len(backups(self.db_path)))

    def test_cannot_be_undone_but_by_its_backup(self):
        self.migrate(step(schema.DESTRUCTIVE, self.drop, ("suggestions",)))
        with self.assertRaises(journal.Refusal):
            journal.undo(self.db_path, self.newest_change())


# ---- A failed check ------------------------------------------------------------------------

class AFailedCheck(Migrated):
    def test_rolls_the_migration_back_and_names_the_check(self):
        def lose(conn):
            conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY)")
            conn.execute("DELETE FROM faces WHERE id = ?", (self.ids["copy"],))

        faces = self.query("SELECT COUNT(*) FROM faces")[0][0]
        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.DESTRUCTIVE, lose, ("albums",), checks=(schema.RowsKept(),) + schema.STANDARD))
        self.assertEqual("rows kept", failed.exception.check)
        self.assertIn("Migration %d (a step) was not applied" % (schema.LATEST + 1), str(failed.exception))
        self.assertIn("faces had %d row(s) and has %d" % (faces, faces - 1), str(failed.exception))
        self.assertEqual(faces, self.query("SELECT COUNT(*) FROM faces")[0][0])
        self.assertNotIn("albums", self.tables())
        self.assertEqual(schema.LATEST, self.version())
        self.assertEqual([], [r for r in self.recorded() if r[0].startswith("migration")])

    def test_a_row_naming_nothing_is_refused(self):
        def orphan(conn):
            conn.execute("INSERT INTO faces (photo_id, box) VALUES (987654, '[1,2,3,4]')")

        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.DATA, orphan, ("faces",), checks=schema.STANDARD))
        self.assertEqual("foreign keys", failed.exception.check)
        self.assertIn("1 row(s) of faces name a row of photos that is not there", str(failed.exception))
        self.assertEqual([], self.query("SELECT id FROM faces WHERE photo_id = 987654"))

    def test_a_row_that_already_named_nothing_does_not_stop_it(self):
        # A library is not held hostage by a row it already had: only what the migration
        # made worse refuses it.
        self.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (987654, x'FFD8')")
        self.migrate(step(schema.ADDITIVE, lambda conn: conn.execute("CREATE INDEX idx_x ON face_crops(jpeg)"),
                          ("face_crops",)))
        self.assertEqual(schema.LATEST + 1, self.version())


# ---- The twelve steps ----------------------------------------------------------------------

FACES_WITHOUT_REASON = ("id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        " photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,"
                        " box TEXT, embedding BLOB, name TEXT, prob REAL, name_source TEXT, excluded INTEGER DEFAULT 0,"
                        " tag_id INTEGER")


class TheTwelveSteps(Migrated):
    def objects(self):
        return set(self.query("SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('index', 'trigger')"))

    def rebuild(self, table, definition, **kwargs):
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                copied = schema.rebuild_table(conn, table, definition, **kwargs)
                conn.commit()
                return copied
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def test_keep_the_rows_indexes_triggers_and_counter(self):
        gone = self.execute("INSERT INTO faces (photo_id, box) VALUES (?, '[9,9,9,9]')", (self.ids["prize"],))
        self.execute("DELETE FROM faces WHERE id = ?", (gone,))
        rows = self.query("SELECT id, photo_id, name, embedding FROM faces ORDER BY id")
        objects = self.objects()
        self.assertEqual(len(rows), self.rebuild("faces", FACES_WITHOUT_REASON))
        self.assertEqual(rows, self.query("SELECT id, photo_id, name, embedding FROM faces ORDER BY id"))
        self.assertEqual(objects, self.objects())
        self.assertNotIn("excluded_reason", [r[1] for r in self.query("PRAGMA table_info(faces)")])
        self.assertGreater(self.execute("INSERT INTO faces (photo_id, box) VALUES (?, '[1,1,1,1]')",
                                        (self.ids["prize"],)), gone)
        # The crop trigger came back with the table.
        self.execute("DELETE FROM faces WHERE id = ?", (self.ids["kept"],))
        self.assertEqual([], self.query("SELECT 1 FROM face_crops WHERE face_id = ?", (self.ids["kept"],)))

    def test_rebuild_a_table_a_trigger_elsewhere_names(self):
        # faces' trigger deletes from face_crops: the rename stopped on it while the
        # table was gone, until the trigger is dropped and made again with it.
        objects = self.objects()
        self.rebuild("face_crops", "face_id INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,"
                                   " jpeg BLOB NOT NULL")
        self.assertEqual(objects, self.objects())
        self.execute("DELETE FROM faces WHERE id = ?", (self.ids["kept"],))
        self.assertEqual([], self.query("SELECT 1 FROM face_crops WHERE face_id = ?", (self.ids["kept"],)))

    def test_refuse_a_connection_with_foreign_keys_on(self):
        conn = db.connect(self.db_path, foreign_keys=True)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(RuntimeError):
            schema.rebuild_table(conn, "faces", FACES_WITHOUT_REASON)
        conn.rollback()
        self.assertIn("excluded_reason", [r[1] for r in self.query("PRAGMA table_info(faces)")])

    def test_refuse_to_run_outside_a_transaction(self):
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(RuntimeError):
            schema.rebuild_table(conn, "faces", FACES_WITHOUT_REASON)

    def test_refuse_a_rebuild_leaving_a_row_naming_nothing(self):
        columns = ["id", "photo_id", "box", "embedding", "name", "prob", "name_source", "excluded", "tag_id"]
        copy = {c: c for c in columns}
        copy["photo_id"] = "photo_id + 100000"
        with self.assertRaises(schema.CheckFailed) as failed:
            self.rebuild("faces", FACES_WITHOUT_REASON, copy=copy)
        self.assertEqual("foreign keys", failed.exception.check)
        self.assertIn("excluded_reason", [r[1] for r in self.query("PRAGMA table_info(faces)")])

    def test_are_the_only_way_a_table_is_rebuilt(self):
        allowed = {("tagpup/store/schema.py", "rebuild_table")}

        def renames(node):
            """A string in `node` that is SQL renaming a table: ALTER TABLE ... RENAME TO."""
            return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and "ALTER TABLE" in n.value.upper() and "RENAME TO" in n.value.upper()
                       for n in ast.walk(node))

        found = set()
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "tagpup")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
                for node in ast.walk(tree):
                    # The innermost function holding the statement.
                    if isinstance(node, ast.FunctionDef) and renames(node) and not any(
                            isinstance(inner, ast.FunctionDef) and inner is not node and renames(inner)
                            for inner in ast.walk(node)):
                        found.add((relative, node.name))
        self.assertLessEqual(found, allowed)
        self.assertIn(("tagpup/store/schema.py", "rebuild_table"), found)


# ---- What each migration is ----------------------------------------------------------------

class TheClassification(unittest.TestCase):
    def test_of_every_migration_is_held_here(self):
        self.assertEqual(KINDS, {m.version: m.kind for m in schema.MIGRATIONS})

    def test_every_migration_says_why_and_names_its_checks(self):
        for m in schema.MIGRATIONS:
            with self.subTest(m.version):
                self.assertIn(m.kind, schema.KINDS)
                self.assertTrue(m.why and "\n" not in m.why)
                # An index-only migration changes no row of any table, so it touches none (and blocks no undo).
                self.assertTrue(m.touches or m.version in INDEX_ONLY)
                names = [check.name for check in m.checks]
                self.assertIn("foreign keys", names)
                self.assertIn("integrity", names)
                self.assertTrue(any(name.startswith("rows kept") for name in names))

    def test_a_migration_that_does_not_say_what_it_is_cannot_be_declared(self):
        with self.assertRaises(TypeError):
            schema.Migration(schema.LATEST + 1, "a step", lambda conn: None)

    def test_the_versions_run_one_to_the_latest(self):
        self.assertEqual(list(range(schema.BASELINE, schema.LATEST + 1)), [m.version for m in schema.MIGRATIONS])


# ---- A library at every older version ------------------------------------------------------

def seed_at_baseline(conn):
    """The rows a library made by the baseline holds, written as the indexer and Suggest store them at that schema: two
    photos taken in 2019 and 2020, a person named by two faces and an unnamed one, two crops, two vectors, one saved
    suggestion. (The people lines are the baseline's own: `photo_people` before it had `tag_id`.)"""
    from tagpup.store import photos
    vector = bytes(range(64))
    for name, taken in (("a.jpg", "2019:05:04 10:00:00"), ("b.jpg", "2020:08:01 16:30:00")):
        photo_id = conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                                " VALUES (?, 1.5, 1200, ?, '[]', ?)",
                                ("D:/Harbour/" + name, json.dumps(["People/Wren Halloway"]),
                                 json.dumps({"EXIF:DateTimeOriginal": taken}))).lastrowid
        conn.execute("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, 0, 'Wren Halloway', 'keyword')",
                     (photo_id,))
        face_id = conn.execute("INSERT INTO faces (photo_id, box, embedding, name, prob, name_source)"
                               " VALUES (?, '[0,0,10,10]', ?, 'Wren Halloway', 0.99, 'manual')", (photo_id, vector)).lastrowid
        conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, x'FFD8FFD9')", (face_id,))
        conn.execute("INSERT INTO embeddings (photo_id, model, mtime, size, vector) VALUES (?, 'ViT-T|tiny', 1.5, 1200, ?)",
                     (photo_id, vector))
        if name == "a.jpg":
            conn.execute("INSERT INTO suggestions (photo_id, tags, people, title, raw, before_consensus, created)"
                         " VALUES (?, ?, '[]', 'Rowing', '{}', 0, '2026-09-25 09:00:00')",
                         (photo_id, json.dumps([{"tag": "Activity/Rowing", "score": 0.8}])))
    conn.execute("INSERT INTO faces (photo_id, box, prob) VALUES (?, '[20,20,30,30]', 0.9)", (photo_id,))
    people = conn.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('People', 'People', 1)").lastrowid
    conn.execute("INSERT INTO tag_taxonomy (tag, parent_id, name, has_face)"
                 " VALUES ('People/Wren Halloway', ?, 'Wren Halloway', 1)", (people,))
    photos.date_photos(conn)


def at_version(db_path, version):
    """A library as a version of TagPup at `version` (14 or later) left it: made by the baseline, as a new library is,
    given the rows of seed_at_baseline, and brought forward by the migration list itself."""
    assert version >= schema.BASELINE, "a library below the baseline is refused, not built"
    conn = db.connect(db_path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
                     " applied_at TEXT NOT NULL)")
        conn.commit()
        for m in migration_names.upto(version):
            conn.execute("BEGIN IMMEDIATE")
            m.apply(conn)
            if m.version == schema.BASELINE:
                seed_at_baseline(conn)
            conn.execute("INSERT INTO schema_version VALUES (?, ?, '2026-09-01 00:00:00')", (m.version, m.name))
            conn.commit()
    finally:
        conn.close()


class ALibraryAtEveryOlderVersion(unittest.TestCase):
    def test_is_brought_to_the_latest(self):
        for version in range(schema.BASELINE, schema.LATEST):
            with self.subTest(version=version):
                home = own_home.for_test(self)
                path = home.library("harbour.db")
                at_version(path, version)
                pending = [m for m in schema.MIGRATIONS if m.version > version]
                self.assertEqual([m.name for m in pending], schema.ensure(path))

                conn = db.connect(db.readonly_uri(path), uri=True)
                self.addCleanup(conn.close)
                self.assertEqual(schema.LATEST, schema.version(conn))
                self.assertEqual([("D:/Harbour/a.jpg", "Wren Halloway", 2019), ("D:/Harbour/b.jpg", "Wren Halloway", 2020),
                                  ("D:/Harbour/b.jpg", None, 2020)], conn.execute(
                    "SELECT p.path, f.name, p.year FROM faces f JOIN photos p ON p.id = f.photo_id ORDER BY f.id"
                ).fetchall())
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM face_crops").fetchone()[0])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
                self.assertEqual([("Rowing",)], conn.execute("SELECT title FROM suggestions").fetchall())

                # Each run is recorded in the journal the library already has, and none has rows to undo: each is at the
                # version before, and refused.
                recorded = conn.execute("SELECT operation, schema_version, summary FROM changes ORDER BY id").fetchall()
                self.assertEqual(["migration %d: %s" % (m.version, m.name) for m in pending],
                                 [operation for operation, _v, _s in recorded])
                kinds = [json.loads(summary)["kind"] for _o, _v, summary in recorded]
                self.assertEqual([m.kind for m in pending], kinds)
                self.assertEqual([m.version - 1 for m in pending], [v for _o, v, _s in recorded])

                # One backup, when anything pending destroys.
                wanted = 1 if any(m.kind != schema.ADDITIVE for m in pending) else 0
                self.assertEqual(wanted, len(backups(path)), "backups at version %d" % version)


class ALibraryBelowTheBaseline(unittest.TestCase):
    """A library older than TagPup can bring forward is refused, not converted, whatever opens it (owner, 2026-10-15)."""

    def old_library(self, version):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, schema.BASELINE)
        conn = db.connect(path)
        try:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (?, 'an old step', '2026-09-01 00:00:00')", (version,))
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()
        return path

    def digest(self, path):
        import hashlib
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_is_refused_with_a_sentence_and_nothing_is_written_or_backed_up(self):
        path = self.old_library(13)
        before = self.digest(path)
        with self.assertRaises(schema.TooOld) as refused:
            schema.ensure(path)
        self.assertIsInstance(refused.exception, schema.NewerLibrary, "every place that answers a newer library answers it")
        self.assertIn("older than TagPup can bring forward", str(refused.exception))
        self.assertIn("restore a newer snapshot", str(refused.exception))
        self.assertEqual((13, schema.BASELINE), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, self.digest(path), "not even the table of migrations is written")
        self.assertEqual([], backups(path))
        self.assertFalse(schema.migrating(path))
        self.assertEqual(refused.exception.args[0], schema.newer_problem(path))   # the CLI, the MCP and the doctor say the same
        with self.assertRaises(schema.TooOld):
            schema.ensure(path)   # and again: nothing was remembered as current

    def test_the_recovery_tools_still_read_it_as_it_is(self):
        path = self.old_library(9)
        before = self.digest(path)
        with schema.reading_newer():
            self.assertEqual([], schema.ensure(path))
        self.assertIn("older than TagPup can bring forward", schema.newer_note(path))
        self.assertEqual(before, self.digest(path))

    def test_a_library_at_exactly_the_baseline_is_brought_forward_as_it_always_was(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, schema.BASELINE)
        self.assertIsNone(schema.newer_problem(path))
        self.assertEqual(migration_names.after(schema.BASELINE), schema.ensure(path))
        self.assertEqual(schema.LATEST, schema.version_of(path))

    def test_an_empty_file_is_a_new_library_not_an_old_one(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        conn = db.connect(path)
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.commit()
        conn.close()   # a crash between the table of migrations and the baseline leaves exactly this
        self.assertIsNone(schema.newer_problem(path))
        self.assertEqual(migration_names.after(0), schema.ensure(path))
        self.assertEqual([], backups(path))


class AnAdditiveMigrationCountsOnlyWhatItTouches(unittest.TestCase):
    def test_a_step_counts_no_table_it_does_not_touch(self):
        # A rows-kept check once counted every table twice, face_crops (1.4 GB) among them, under the write lock: 26 s on the
        # owner's library. The runner's watch already refuses an additive migration that changed a row anywhere; the count is
        # of the tables it touches (docs/findings.md, #272).
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, schema.BASELINE)
        counted = []
        real = schema._count

        def count(conn, table):
            counted.append(table)
            return real(conn, table)

        # The additive ones: 30 destroys, and names the tables it keeps the rows of (test_unused_tables.py).
        with mock.patch.object(schema, "_count", side_effect=count), \
                mock.patch.object(schema, "MIGRATIONS", migration_names.upto(29)), mock.patch.object(schema, "LATEST", 29):
            self.assertEqual([m.name for m in migration_names.upto(29) if m.version > schema.BASELINE], schema.ensure(path))
        # What they touch: 15 added_folders, 17 faces_pending, 25 faces_detected, which they make; 21 faces and photo_people,
        # whose columns it adds (a count of faces reads its smallest index, not its rows).
        self.assertLessEqual(set(counted), {"added_folders", "faces_pending", "faces", "photo_people", "faces_detected"})


class ANewLibrary(unittest.TestCase):
    def test_is_made_without_a_backup_or_a_record_of_each_step(self):
        # Making a library is not migrating one: there is nothing to keep or undo.
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        self.assertEqual([], backups(path))
        conn = db.connect(db.readonly_uri(path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0])


if __name__ == "__main__":
    unittest.main()

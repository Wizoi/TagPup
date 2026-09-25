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
from journal_library import JournalLibrary  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: What each migration is, from its code. A new one is added here, or this fails.
KINDS = {
    1: schema.ADDITIVE,      # CREATE ... IF NOT EXISTS; an older library is refused
    2: schema.DESTRUCTIVE,   # drops the two counter tables, their counts carried over
    3: schema.DESTRUCTIVE,   # drops faces.crop_image once the crops are copied
    4: schema.DESTRUCTIVE,   # rebuilds photos and faces
    5: schema.DESTRUCTIVE,   # drops embedding_cache and photos.embedding, and some vectors
    6: schema.DESTRUCTIVE,   # drops photos.people; a name only the list held is lost
    7: schema.DATA,          # takes saved suggestions in as rows, and photo rows for them
    8: schema.ADDITIVE,      # adds taken and year, filled from each row's own values
    9: schema.ADDITIVE,      # the journal's tables
    10: schema.ADDITIVE,     # the settings table
    11: schema.ADDITIVE,     # change_files, and changes.owner
    12: schema.ADDITIVE,     # change_files.stamp
}


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
        self.assertIn("schema", str(refused.exception))

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
                        " box TEXT, embedding BLOB, name TEXT, prob REAL, name_source TEXT, excluded INTEGER DEFAULT 0")


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
        columns = ["id", "photo_id", "box", "embedding", "name", "prob", "name_source", "excluded"]
        copy = {c: c for c in columns}
        copy["photo_id"] = "photo_id + 100000"
        with self.assertRaises(schema.CheckFailed) as failed:
            self.rebuild("faces", FACES_WITHOUT_REASON, copy=copy)
        self.assertEqual("foreign keys", failed.exception.check)
        self.assertIn("excluded_reason", [r[1] for r in self.query("PRAGMA table_info(faces)")])

    def test_are_the_only_way_a_table_is_rebuilt(self):
        # Migration 4 rebuilt photos and faces by hand before the helper; it has run on
        # every library, and is left as it ran.
        allowed = {("tagpup/store/schema.py", "rebuild_table"), ("tagpup/store/schema.py", "_photo_ids")}

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
                self.assertTrue(m.touches)
                names = [check.name for check in m.checks]
                self.assertIn("foreign keys", names)
                self.assertIn("integrity", names)
                self.assertTrue(any(name.startswith("rows kept") for name in names))

    def test_a_migration_that_does_not_say_what_it_is_cannot_be_declared(self):
        with self.assertRaises(TypeError):
            schema.Migration(schema.LATEST + 1, "a step", lambda conn: None)

    def test_the_versions_run_one_to_the_latest(self):
        self.assertEqual(list(range(1, schema.LATEST + 1)), [m.version for m in schema.MIGRATIONS])

    def test_nothing_the_journal_rebuilds_is_watched(self):
        self.assertLessEqual(set(journal.DERIVED), set(schema.UNWATCHED))


# ---- A library at every older version ------------------------------------------------------

def seed_2026_09(db_path):
    """A library as the tables of 2026-09 held it, before migration 1: its two counters,
    a photo's vector twice, a face with its crop on its row, a list of people, and a file
    of saved suggestions beside it."""
    conn = db.connect(db_path)
    try:
        conn.execute("CREATE TABLE photos (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT,"
                     " people TEXT, captions TEXT, raw_metadata TEXT, embedding BLOB, document_id TEXT)")
        conn.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY AUTOINCREMENT, photo_path TEXT, box TEXT,"
                     " embedding BLOB, name TEXT, crop_image BLOB, prob REAL, name_source TEXT,"
                     " excluded INTEGER DEFAULT 0, excluded_reason TEXT,"
                     " FOREIGN KEY(photo_path) REFERENCES photos(path) ON DELETE CASCADE)")
        conn.execute("CREATE TABLE tag_taxonomy (id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT UNIQUE,"
                     " parent_id INTEGER, name TEXT, has_face INTEGER DEFAULT 0,"
                     " hidden_from_autocomplete INTEGER DEFAULT 0,"
                     " FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE)")
        conn.execute("CREATE TABLE embedding_cache (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,"
                     " model_name TEXT, pretrained TEXT, preserve_full_frame INTEGER,"
                     " max_aspect_ratio REAL, force_image_size INTEGER, embedding BLOB)")
        vector = bytes(range(64))
        for name, taken in (("a.jpg", "2019:05:04 10:00:00"), ("b.jpg", "2020:08:01 16:30:00")):
            path = "D:/Harbour/" + name
            conn.execute("INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)"
                         " VALUES (?, 1.5, 1200, ?, ?, '[]', ?, ?)",
                         (path, json.dumps(["People/Wren Halloway"]), json.dumps(["Wren Halloway"]),
                          json.dumps({"EXIF:DateTimeOriginal": taken}), vector))
            conn.execute("INSERT INTO embedding_cache VALUES (?, 1.5, 1200, 'ViT-T', 'tiny', 1, 1.4, 512, ?)",
                         (path, vector))
            conn.execute("INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob, name_source)"
                         " VALUES (?, '[0,0,10,10]', ?, 'Wren Halloway', x'FFD8FFD9', 0.99, 'manual')", (path, vector))
        conn.execute("INSERT INTO faces (photo_path, box, prob) VALUES ('D:/Harbour/b.jpg', '[20,20,30,30]', 0.9)")
        people = conn.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('People', 'People', 1)").lastrowid
        conn.execute("INSERT INTO tag_taxonomy (tag, parent_id, name, has_face)"
                     " VALUES ('People/Wren Halloway', ?, 'Wren Halloway', 1)", (people,))
        for name, value in (("faces", 7), ("taxonomy", 3)):
            conn.execute("CREATE TABLE %s_generation (id INTEGER PRIMARY KEY CHECK (id = 1),"
                         " generation INTEGER NOT NULL)" % name)
            conn.execute("INSERT INTO %s_generation VALUES (1, ?)" % name, (value,))
        conn.commit()
    finally:
        conn.close()
    saved = {"D:/Harbour": {"suggestions": {"D:/Harbour/a.jpg": {
        "tags": [{"tag": "Activity/Rowing", "score": 0.8}], "people": [], "title": "Rowing",
        "raw_suggestions": {"suggested_tags": []}}}}}
    # Beside the library, named as tagpup.jobs.suggestions named it (schema._suggestions_file).
    beside = os.path.join(os.path.dirname(db_path), "gui_suggestions_cache_%s.json"
                          % os.path.splitext(os.path.basename(db_path))[0])
    with open(beside, "w", encoding="utf-8") as handle:
        json.dump(saved, handle)


def at_version(db_path, version):
    """The library of seed_2026_09 brought to `version` by the migration list itself, as
    a version of TagPup at that migration left it."""
    seed_2026_09(db_path)
    conn = db.connect(db_path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
                     " applied_at TEXT NOT NULL)")
        conn.commit()
        for m in schema.MIGRATIONS[:version]:
            conn.execute("BEGIN IMMEDIATE")
            m.apply(conn)
            conn.execute("INSERT INTO schema_version VALUES (?, ?, '2026-09-01 00:00:00')", (m.version, m.name))
            conn.commit()
    finally:
        conn.close()


class ALibraryAtEveryOlderVersion(unittest.TestCase):
    def test_is_brought_to_the_latest(self):
        for version in range(schema.LATEST):
            with self.subTest(version=version):
                home = own_home.for_test(self)
                path = home.library("harbour.db")
                at_version(path, version)
                pending = schema.MIGRATIONS[version:]
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

                # Each run is recorded, those before the journal existed once it did.
                recorded = conn.execute("SELECT operation, schema_version, summary FROM changes ORDER BY id").fetchall()
                self.assertEqual(["migration %d: %s" % (m.version, m.name) for m in pending],
                                 [operation for operation, _v, _s in recorded])
                kinds = [json.loads(summary)["kind"] for _o, _v, summary in recorded]
                self.assertEqual([m.kind for m in pending], kinds)
                # None has rows to undo: those before migration 9 had no journal to hold
                # them, so each is at the version before and refused.
                self.assertEqual([m.version - 1 for m in pending], [v for _o, v, _s in recorded])

                # One backup, when anything pending destroys -- or changes data before
                # there is a journal to record it (migration 7).
                wanted = 1 if any(m.kind != schema.ADDITIVE for m in pending) else 0
                self.assertEqual(wanted, len(backups(path)), "backups at version %d" % version)


class AnAdditiveMigrationCountsOnlyWhatItTouches(unittest.TestCase):
    def test_migration_11_counts_no_table_it_does_not_touch(self):
        # Migration 11 took 26 s on the owner's library: its rows-kept check counted
        # every table twice, face_crops (1.4 GB) among them, under the write lock. The
        # runner's watch already refuses an additive migration that changed a row
        # anywhere; the count is of the tables it touches (docs/findings.md, #272).
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, 10)
        counted = []
        real = schema._count

        def count(conn, table):
            counted.append(table)
            return real(conn, table)

        with mock.patch.object(schema, "_count", side_effect=count):
            self.assertEqual(["photo files in the journal", "the stamp of each file before its write"],
                             schema.ensure(path))
        # What it touches: change_files, which it makes, and changes, the runner's own;
        # migration 12 touches change_files alone.
        self.assertLessEqual(set(counted), {"change_files", "changes"})


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

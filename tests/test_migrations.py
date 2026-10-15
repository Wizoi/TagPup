"""The migration runner, and a library meeting a step it has not had (docs/DATABASE.md, "The baseline"; tagpup.store.schema).

There is one real migration, the baseline (schema 32): it makes a NEW library whole, and a library below it is refused. The
runner is for the next ones (33 on), so every test here gives the code SYNTHETIC steps (tests/migration_names.py) after the
baseline and holds the runner to what it promises:

* one that adds takes nothing and is watched to change nothing that was there; one that changes data has every row it
  changed recorded in the journal, undoable while it is the newest; one that destroys takes one backup, under the write
  lock. Each runs in one transaction with the checks it names, and a failed check rolls it back. Every run is a row of
  `changes`. A table is rebuilt only by SQLite's twelve steps (`rebuild_table`).
* D8: a library kept at 32 meets an additive step and a destructive one: interrupted part-way it resumes; two openers apply
  each once; a failing check leaves the library as it was; one backup, before the destructive step only, and none for a
  library being made; an older app refuses it; a change made at 32 stays undoable across the additive step and not across
  the destructive one.
* A library below the baseline is refused with a sentence before anything is written to it, whoever opens it.

A step is declared once, in tests/migration_names.py, not in a list of names in every test that says which ran (#689). However
many destructive steps are pending, one copy from before the first holds the library for all of them: a second was another
3 GB of photo_index, and another of the five kept (#76).

Libraries are seeded in plain SQL, as the indexer stores them, in homes of their own. The names are fictional.
"""
import ast
import contextlib
import hashlib
import json
import os
import sys
import textwrap
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
from journal_library import JournalLibrary  # noqa: E402
from migration_names import step  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def backups(db_path):
    """The copies taken before a migration of this library (the folder is the home's, shared by its libraries)."""
    folder = Library(db_path).backups
    stem = os.path.splitext(os.path.basename(db_path))[0] + ".before-"
    return sorted(name for name in os.listdir(folder) if name.startswith(stem)) if os.path.isdir(folder) else []


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Migrated(JournalLibrary):
    """A library at the latest schema with rows like the owner's, and synthetic migrations after."""

    def setUp(self):
        super().setUp()
        self.steps = contextlib.ExitStack()
        self.addCleanup(self.steps.close)

    def migrate(self, *added):
        """Open the library with the code knowing `added` after the baseline, and go on knowing them: the library is now
        at their version, and what the test does next is done by that code (`older_app` is the code without them)."""
        self.older_app()
        self.steps.enter_context(migration_names.ahead(*added))
        try:
            return schema.ensure(self.db_path)
        except BaseException:
            self.older_app()   # a step that failed: the library is as the code without it finds it
            raise

    def older_app(self):
        """From now on the code knows only the real migrations."""
        self.steps.close()

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
        self.assertEqual(("migration %d: a step" % (migration_names.BASE + 1), migration_names.BASE, "applied"),
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
        self.assertEqual(migration_names.BASE, self.version())
        self.assertNotIn("albums", self.tables())
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces WHERE id = ?", (face,)))

    def test_that_drops_a_column_is_refused(self):
        def drop(conn):
            conn.execute("ALTER TABLE suggestions DROP COLUMN error")

        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(step(schema.ADDITIVE, drop, ("suggestions",)))
        self.assertEqual("nothing dropped", failed.exception.check)
        self.assertEqual(migration_names.BASE, self.version())

    def test_counts_only_the_tables_it_touches(self):
        # A rows-kept check once counted every table twice, face_crops (1.4 GB) among them, under the write lock: 26 s on the
        # owner's library. The runner's watch already refuses an additive migration that changed a row anywhere; the count is
        # of the tables it touches (docs/findings.md, #272).
        counted = []
        real = schema._count

        def count(conn, table):
            counted.append(table)
            return real(conn, table)

        from unittest import mock
        with mock.patch.object(schema, "_count", side_effect=count):
            self.migrate(step(schema.ADDITIVE, lambda conn: conn.execute("CREATE INDEX idx_x ON added_folders(added)"),
                              ("added_folders",)))
        self.assertLessEqual(set(counted), {"added_folders"})


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
        self.assertEqual("migration %d: a step" % (migration_names.BASE + 1), entry["operation"])
        self.assertEqual(migration_names.BASE + 1, entry["schema_version"])
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
        # One that touches a table the journal keys (a column of faces): the rows the change recorded may mean something else.
        after = step(schema.ADDITIVE, lambda conn: conn.execute("ALTER TABLE faces ADD COLUMN quality REAL"),
                     ("faces",), version=migration_names.BASE + 2)
        self.migrate(step(schema.DATA, self.change, ("faces",), version=migration_names.BASE + 1), after)
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
        self.assertEqual(migration_names.BASE, self.version())


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
        self.assertEqual(migration_names.BASE, version)
        # The copy is the library as the migration found it.
        conn = db.connect(db.readonly_uri(os.path.join(Library(self.db_path).backups, taken[0])), uri=True)
        self.addCleanup(conn.close)
        self.assertIn("error", [row[1] for row in conn.execute("PRAGMA table_info(suggestions)")])

    def test_two_in_one_run_take_one_backup(self):
        self.migrate(step(schema.DESTRUCTIVE, self.drop, ("suggestions",)),
                     step(schema.DESTRUCTIVE, lambda conn: conn.execute("DROP TABLE tag_embeddings"), (),
                          version=migration_names.BASE + 2))
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
        self.assertIn("Migration %d (a step) was not applied" % (migration_names.BASE + 1), str(failed.exception))
        self.assertIn("faces had %d row(s) and has %d" % (faces, faces - 1), str(failed.exception))
        self.assertEqual(faces, self.query("SELECT COUNT(*) FROM faces")[0][0])
        self.assertNotIn("albums", self.tables())
        self.assertEqual(migration_names.BASE, self.version())
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
        self.assertEqual(migration_names.BASE + 1, self.version())


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


# ---- What a migration is -------------------------------------------------------------------

class TheBaselineIsTheOnlyMigration(unittest.TestCase):
    def test_it_says_what_it_is_and_names_its_checks(self):
        [baseline] = schema.MIGRATIONS
        self.assertEqual((schema.BASELINE, migration_names.BASE, schema.ADDITIVE), (baseline.version, 32, baseline.kind))
        self.assertIn(baseline.kind, schema.KINDS)
        self.assertTrue(baseline.why and "\n" not in baseline.why)
        names = [check.name for check in baseline.checks]
        self.assertIn("foreign keys", names)
        self.assertIn("integrity", names)
        self.assertTrue(any(name.startswith("rows kept") for name in names))

    def test_a_migration_that_does_not_say_what_it_is_cannot_be_declared(self):
        with self.assertRaises(TypeError):
            schema.Migration(schema.LATEST + 1, "a step", lambda conn: None)


# ---- D8: a library kept at the baseline meets two new steps --------------------------------

class AKeptLibraryMeetsTwoNewSteps(Migrated):
    """The library has its rows and a change made at 32. The code then knows an additive step (33: a table) and a destructive
    one (34: drops `tag_embeddings`)."""

    def setUp(self):
        super().setUp()
        self.additive, self.destroying = migration_names.two_steps()
        self.change = journal.apply(self.db_path, "add a node",
                                    [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})]).change_id

    def test_interrupted_between_the_steps_it_resumes_with_the_one_that_was_not_done(self):
        def crashes(conn):
            conn.execute("DROP TABLE tag_embeddings")
            raise RuntimeError("the power went")

        with self.assertRaises(RuntimeError):
            self.migrate(self.additive, self.destroying._replace(apply=crashes))
        self.assertEqual(33, self.version(), "the additive step is done and recorded; the other left no trace")
        self.assertIn("tag_embeddings", self.tables())
        self.assertEqual(1, len(backups(self.db_path)), "taken before the destructive step, and not before the additive")
        self.assertEqual([self.destroying.name], self.migrate(self.additive, self.destroying))
        self.assertEqual(34, self.version())
        self.assertNotIn("tag_embeddings", self.tables())

    def test_two_processes_opening_it_at_once_apply_each_step_once(self):
        code = textwrap.dedent("""
            import sys
            sys.path.insert(0, %(root)r); sys.path.insert(0, %(tests)r)
            import migration_names
            from tagpup.store import schema
            with migration_names.ahead(*migration_names.two_steps()):
                print(len(schema.ensure(%(path)r)))
            """) % {"root": ROOT, "tests": os.path.dirname(os.path.abspath(__file__)), "path": self.db_path}
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(["0", "2"], sorted(answers))
        self.assertEqual([(32,), (33,), (34,)], self.query("SELECT version FROM schema_version ORDER BY version"))
        self.assertEqual(migration_names.operations(self.additive, self.destroying),
                         [row[0] for row in self.query("SELECT operation FROM changes WHERE operation LIKE 'migration %' ORDER BY id")])
        self.assertEqual(1, len(backups(self.db_path)))

    def test_two_threads_opening_it_at_once_apply_each_step_once(self):
        applied, barrier = [], threading.Barrier(2)

        def open_it():
            barrier.wait()
            applied.append(len(schema._ensure(self.db_path)))   # past the in-process memory, as another process would be

        with migration_names.ahead(self.additive, self.destroying):
            threads = [threading.Thread(target=open_it) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual([0, 2], sorted(applied))

    def test_a_failing_check_leaves_the_library_as_the_step_found_it(self):
        def lose(conn):
            conn.execute("DELETE FROM faces WHERE id = ?", (self.ids["copy"],))
            conn.execute("DROP TABLE tag_embeddings")

        faces = self.query("SELECT COUNT(*) FROM faces")[0][0]
        failing = self.destroying._replace(apply=lose, checks=(schema.RowsKept(),) + schema.STANDARD)
        with self.assertRaises(schema.CheckFailed) as failed:
            self.migrate(self.additive, failing)
        self.assertEqual("rows kept", failed.exception.check)
        self.assertEqual((33, faces), (self.version(), self.query("SELECT COUNT(*) FROM faces")[0][0]))
        self.assertIn("tag_embeddings", self.tables())
        self.assertEqual([self.additive.name], [r[0][len("migration 33: "):] for r in self.recorded() if r[0].startswith("migration")])

    def test_one_backup_before_the_destructive_step_only_and_none_for_a_library_being_made(self):
        self.migrate(self.additive)
        self.assertEqual([], backups(self.db_path), "an additive step takes none")
        self.migrate(self.additive, self.destroying)
        [taken] = backups(self.db_path)
        self.assertEqual(taken, self.recorded()[-1][3]["backup"])
        made = self.home.library("made.db")
        with migration_names.ahead(self.additive, self.destroying):
            self.assertEqual([schema.MIGRATIONS[0].name, self.additive.name, self.destroying.name], schema.ensure(made))
        self.assertEqual([], backups(made), "a library being made has nothing to lose")
        self.assertEqual([(0,)], [(count,) for (count,) in
                                  db_rows(made, "SELECT COUNT(*) FROM changes")], "and no record of each step")

    def test_an_older_app_refuses_what_the_newer_one_migrated(self):
        self.migrate(self.additive, self.destroying)
        self.older_app()
        before = digest(self.db_path)
        with self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((34, 32), (refused.exception.found, refused.exception.known))
        self.assertEqual(refused.exception.args[0], schema.newer_problem(self.db_path))
        self.assertEqual(before, digest(self.db_path), "nothing written")

    def test_a_change_made_at_32_stays_undoable_across_the_additive_step_and_not_the_destructive_one(self):
        self.migrate(self.additive)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.change))
        self.migrate(self.additive, self.destroying)
        [why] = journal.refusal(conn, self.change)
        self.assertIn("migration 34", why)
        self.assertIn("is destructive", why)

    def test_the_additive_step_leaves_the_change_undoable_and_undo_puts_the_rows_back(self):
        self.assertEqual(1, len(self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'")))
        self.migrate(self.additive)
        journal.undo(self.db_path, self.change)
        self.assertEqual([], self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))


def db_rows(path, sql):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---- A library below the baseline ----------------------------------------------------------

class ALibraryBelowTheBaseline(unittest.TestCase):
    """A library older than TagPup can bring forward is refused, not converted, whatever opens it (owner, 2026-10-15; the
    baseline moved from 14 to 32 the same day: every live library is at 32, the snapshots and backups below it cannot be
    restored)."""

    def library_at(self, version):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        conn = db.connect(path)
        try:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (?, 'an old step', '2026-09-01 00:00:00')", (version,))
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()
        return path

    def test_is_refused_with_a_sentence_and_nothing_is_written_or_backed_up(self):
        for version in (31, 13):
            with self.subTest(version=version):
                path = self.library_at(version)
                before = digest(path)
                with self.assertRaises(schema.TooOld) as refused:
                    schema.ensure(path)
                self.assertIsInstance(refused.exception, schema.NewerLibrary, "every place that answers a newer library answers it")
                self.assertIn("older than TagPup can bring forward", str(refused.exception))
                self.assertIn("restore a newer snapshot", str(refused.exception))
                self.assertEqual((version, schema.BASELINE), (refused.exception.found, refused.exception.known))
                self.assertEqual(before, digest(path), "not even the table of migrations is written")
                self.assertEqual([], backups(path))
                self.assertFalse(schema.migrating(path))
                self.assertEqual(refused.exception.args[0], schema.newer_problem(path))   # the CLI, the MCP and the doctor
                with self.assertRaises(schema.TooOld):
                    schema.ensure(path)   # and again: nothing was remembered as current

    def test_the_recovery_tools_still_read_it_as_it_is(self):
        path = self.library_at(31)
        before = digest(path)
        with schema.reading_newer():
            self.assertEqual([], schema.ensure(path))
        self.assertIn("older than TagPup can bring forward", schema.newer_note(path))
        self.assertEqual(before, digest(path))

    def test_a_library_at_the_baseline_is_opened_without_a_write_a_migration_or_a_backup(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        schema._current.clear()
        before = digest(path)
        self.assertIsNone(schema.newer_problem(path))
        self.assertEqual([], schema.ensure(path))
        self.assertEqual(before, digest(path))
        self.assertEqual([], backups(path))
        self.assertEqual(32, schema.version_of(path))

    def test_an_empty_file_is_a_new_library_not_an_old_one(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        conn = db.connect(path)
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.commit()
        conn.close()   # a crash between the table of migrations and the baseline leaves exactly this
        self.assertIsNone(schema.newer_problem(path))
        self.assertEqual([m.name for m in schema.MIGRATIONS], schema.ensure(path))
        self.assertEqual([], backups(path))


class ANewLibrary(unittest.TestCase):
    def test_is_made_without_a_backup_or_a_record_of_each_step(self):
        # Making a library is not migrating one: there is nothing to keep or undo.
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        self.assertEqual([], backups(path))
        self.assertEqual([(0,)], db_rows(path, "SELECT COUNT(*) FROM changes"))
        self.assertEqual([(32,)], db_rows(path, "SELECT version FROM schema_version"))

    def test_made_by_two_processes_at_once_is_made_once(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema; print(len(schema.ensure(%r)))"
                % (ROOT, path))
        env = dict(os.environ, TAGPUP_HOME=home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(["0", "1"], sorted(answers))
        self.assertEqual([(32,)], db_rows(path, "SELECT version FROM schema_version"))


if __name__ == "__main__":
    unittest.main()

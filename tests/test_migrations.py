"""The migration runner, and a library meeting a step it has not had (docs/DATABASE.md, "The baseline"; tagpup.store.schema).

There are seven real migrations: the baseline (schema 32), which makes a NEW library whole (a library below it is refused);
33, which moves each face's vector to `face_embeddings` under FaceNet's key and drops `faces.embedding`, the empty
`faces_pending` and the redundant `idx_faces_name`, on a library kept at 32 and on a new one alike; 34, which adds the
empty `photo_fingerprints`; 35, which adds `idx_embeddings_stamp`; 36, which adds `job_runs.parent`, `heartbeat` and
`progress`; 37, which adds `photos.kind` and `change_files.patch` and `patch_before`; and 38, which adds the empty
`embedding_frames` and the two triggers that take a clip's frames with its photo and its vector (34 to 38 additive: no backup,
and a change made before each stays undoable).
The runner is held to what it promises by SYNTHETIC steps after the latest real one (tests/migration_names.py):

* one that adds takes nothing and is watched to change nothing that was there; one that destroys takes one backup, under
  the write lock. Each runs in one transaction with the checks it names, and a failed check rolls it back. Every run is a
  row of `changes`, and is not undone.
* D8: a library kept at the latest schema meets an additive step and a destructive one: interrupted part-way it resumes; two
  openers apply each once; a failing check leaves the library as it was; one backup, before the destructive step only, and
  none for a library being made; an older app refuses it; a change made before stays undoable across the additive step and
  not across the destructive one. And the owner's library at 32 meets the real 33: one backup, every vector byte for byte
  under FaceNet's key, the table and the index gone, every other row as it was; a face without FaceNet's vector refuses it
  with a sentence and nothing changed, until that face is deleted; a library with no face; a reader mid-read keeps its
  snapshot; a second library in the home is not touched; after it a face's vectors go with the face and move the faces
  generation; killed after the backup it is still at 32 and migrates at the next open; the app before 33 refuses it after.
  The owner's library at 33 meets the real 34: no backup, the table and nothing else, every row as it was, a change made
  at 33 still undoable; killed part way it is still at 33 and migrates at the next open; the app before 34 refuses it
  after; a photo's fingerprint is kept, and reads as none once its photo is deleted. The owner's library at 34 meets the
  real 35: no backup, the index and nothing else, every row as it was, a change made at 34 still undoable, and a sync's
  stamp join answering the same stamps; killed part way it is still at 34 and migrates at the next open; the app before
  35 refuses it after. The owner's library at 35, with its runs (one running), meets the real 36: no backup, the three
  columns NULL in every run and the index, nothing else, a change made at 35 still undoable, a second open a no-op; the
  readers of the runs answer alike at 35 and at 36; two processes opening it at once apply it once; killed part way it is
  still at 35 and migrates at the next open; the app before 36 refuses it after. The owner's library at 36, with a photo
  deleted by a journaled change and a file written, meets the real 37: no backup, every photo a 'photo' and every file
  without a patch, nothing else, the deletion still undoable and the photo back as a 'photo', a second open a no-op; a
  photo the indexer or Suggest adds after it is a 'photo', and no kind but 'photo' and 'video' is stored; the files read
  alike at 36 and at 37; two processes opening it at once apply it once; killed part way it is still at 36 and migrates at
  the next open; the app before 37 refuses it after. The owner's library at 37, with a photo deleted by a journaled change,
  meets the real 38: no backup, the table and its two triggers and nothing else, every vector byte for byte, the deletion
  still undoable, a second open a no-op; two processes opening it at once apply it once; killed part way it is still at 37
  and migrates at the next open; the app before 38 refuses it after.
* A library below the baseline is refused with a sentence before anything is written to it, whoever opens it.

A step is declared once, in tests/migration_names.py, not in a list of names in every test that says which ran (#689). However
many destructive steps are pending, one copy from before the first holds the library for all of them: a second was another
3 GB of photo_index, and another of the five kept (#76).

Libraries are seeded in plain SQL, as the indexer stores them, in homes of their own. The names are fictional.
"""
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import textwrap
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import FACENET, add_vector_of_face, photo_id  # noqa: E402
from journal_library import MODEL, JournalLibrary, vector  # noqa: E402
from migration_names import step  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, file_journal, generations, job_runs, journal, photo_fingerprints, photos as store_photos, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def backups(db_path):
    """The copies taken before a migration of this library (the folder is the home's, shared by its libraries)."""
    folder = Library(db_path).backups
    stem = os.path.splitext(os.path.basename(db_path))[0] + ".before-"
    return sorted(name for name in os.listdir(folder) if name.startswith(stem)) if os.path.isdir(folder) else []


def digest(path):
    """The library file's bytes, and its write-ahead log's if it has one: a write not yet checkpointed is in the log."""
    found = hashlib.sha256()
    for each in (path, path + "-wal"):
        if os.path.exists(each):
            with open(each, "rb") as handle:
                found.update(handle.read())
    return found.hexdigest()


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
        self.assertNotIn("rows", summary, "History counts a change's rows from change_rows")
        self.assertEqual(["nothing dropped", "nothing that was there changed", "rows kept", "foreign keys",
                          "integrity"], summary["checks"])

    def test_cannot_be_undone(self):
        # A schema is not undone: the change is at the version before, and refused.
        self.migrate(step(schema.ADDITIVE, self.add, ("albums",)))
        refused = journal_service.undo(Library(self.db_path), self.newest_change(), apply=True).refused
        self.assertIn("a migration is not undone", refused)

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


# ---- Destroying ----------------------------------------------------------------------------

class ADestructiveMigration(Migrated):
    @staticmethod
    def drop(conn):
        conn.execute("ALTER TABLE suggestions DROP COLUMN error")

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
        self.assertTrue(journal_service.undo(Library(self.db_path), self.newest_change(), apply=True).refused)


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
            self.migrate(step(schema.DESTRUCTIVE, orphan, ("faces",), checks=schema.STANDARD))
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


# ---- What a migration is -------------------------------------------------------------------

class TheRealMigrations(unittest.TestCase):
    def test_each_says_what_it_is_and_names_its_checks(self):
        baseline, dropping, fingerprints, stamps, nesting, kinds, frames = schema.MIGRATIONS
        self.assertEqual((32, schema.ADDITIVE), (baseline.version, baseline.kind))
        self.assertEqual((33, schema.DESTRUCTIVE, ("faces_pending", "faces", "face_embeddings"), "face vectors in face_embeddings"),
                         (dropping.version, dropping.kind, dropping.touches, dropping.name))
        self.assertEqual((34, schema.ADDITIVE, ("photo_fingerprints",), "photo fingerprints"),
                         (fingerprints.version, fingerprints.kind, fingerprints.touches, fingerprints.name))
        # No table touched: an index writes no row, so a change made at 34 stays undoable (journal.schema_gap_blocker).
        self.assertEqual((35, schema.ADDITIVE, (), "the vector stamps in an index"),
                         (stamps.version, stamps.kind, stamps.touches, stamps.name))
        self.assertIsNone(journal.schema_gap_blocker(34, 35))
        # `job_runs` is no table the journal keys: a change made at 35 stays undoable.
        self.assertEqual((36, schema.ADDITIVE, ("job_runs",), "runs nest and say how far they got"),
                         (nesting.version, nesting.kind, nesting.touches, nesting.name))
        self.assertNotIn("job_runs", journal.KEYS)
        self.assertIsNone(journal.schema_gap_blocker(35, 36))
        # `change_files` is no table the journal keys, and `photos` is not named: a change made at 36 stays undoable.
        self.assertEqual((37, schema.ADDITIVE, ("change_files",), "photos know their kind; a patch is recorded before it is written"),
                         (kinds.version, kinds.kind, kinds.touches, kinds.name))
        self.assertNotIn("change_files", journal.KEYS)
        self.assertIsNone(journal.schema_gap_blocker(36, 37))
        # No table touched: `embedding_frames`, new and empty, is keyed by the journal, and a change made at 37 stays undoable.
        self.assertEqual((38, schema.ADDITIVE, (), "a clip's frames beside its vector"),
                         (frames.version, frames.kind, frames.touches, frames.name))
        self.assertIn("embedding_frames", journal.KEYS)
        self.assertIsNone(journal.schema_gap_blocker(37, 38))
        self.assertEqual((schema.BASELINE, schema.LATEST, migration_names.BASE), (32, 38, 38))
        for migration in schema.MIGRATIONS:
            self.assertIn(migration.kind, schema.KINDS)
            self.assertTrue(migration.why and "\n" not in migration.why)
            names = [check.name for check in migration.checks]
            self.assertIn("foreign keys", names)
            self.assertIn("integrity", names)
            self.assertTrue(any(name.startswith("rows kept") for name in names))

    def test_a_migration_that_does_not_say_what_it_is_cannot_be_declared(self):
        with self.assertRaises(TypeError):
            schema.Migration(schema.LATEST + 1, "a step", lambda conn: None)


# ---- D8: a library kept at the baseline meets two new steps --------------------------------

class AKeptLibraryMeetsTwoNewSteps(Migrated):
    """The library has its rows and a change made at the latest schema. The code then knows an additive step (a table) and a
    destructive one (drops `tag_embeddings`) after it."""

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
        self.assertEqual(self.additive.version, self.version(), "the additive step is done and recorded; the other left no trace")
        self.assertIn("tag_embeddings", self.tables())
        self.assertEqual(1, len(backups(self.db_path)), "taken before the destructive step, and not before the additive")
        self.assertEqual([self.destroying.name], self.migrate(self.additive, self.destroying))
        self.assertEqual(self.destroying.version, self.version())
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
        # Each step is a transaction of its own under the write lock: one process may take both, or each may take one
        # (a split depends on timing). What holds either way: the two steps were applied once between the two.
        self.assertEqual(2, sum(int(answer) for answer in answers), answers)
        self.assertEqual([(m.version,) for m in migration_names.REAL + (self.additive, self.destroying)],
                         self.query("SELECT version FROM schema_version ORDER BY version"))
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
        self.assertEqual((self.additive.version, faces), (self.version(), self.query("SELECT COUNT(*) FROM faces")[0][0]))
        self.assertIn("tag_embeddings", self.tables())
        self.assertEqual(migration_names.operations(self.additive),
                         [r[0] for r in self.recorded() if r[0].startswith("migration")])

    def test_one_backup_before_the_destructive_step_only_and_none_for_a_library_being_made(self):
        self.migrate(self.additive)
        self.assertEqual([], backups(self.db_path), "an additive step takes none")
        self.migrate(self.additive, self.destroying)
        [taken] = backups(self.db_path)
        self.assertEqual(taken, self.recorded()[-1][3]["backup"])
        made = self.home.library("made.db")
        with migration_names.ahead(self.additive, self.destroying):
            self.assertEqual([m.name for m in migration_names.REAL] + [self.additive.name, self.destroying.name],
                             schema.ensure(made))
        self.assertEqual([], backups(made), "a library being made has nothing to lose")
        self.assertEqual([(0,)], [(count,) for (count,) in
                                  db_rows(made, "SELECT COUNT(*) FROM changes")], "and no record of each step")

    def test_an_older_app_refuses_what_the_newer_one_migrated(self):
        self.migrate(self.additive, self.destroying)
        self.older_app()
        before = digest(self.db_path)
        with self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((self.destroying.version, migration_names.BASE), (refused.exception.found, refused.exception.known))
        self.assertEqual(refused.exception.args[0], schema.newer_problem(self.db_path))
        self.assertEqual(before, digest(self.db_path), "nothing written")

    def test_a_change_made_before_stays_undoable_across_the_additive_step_and_not_the_destructive_one(self):
        self.migrate(self.additive)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.change))
        self.migrate(self.additive, self.destroying)
        [why] = journal.refusal(conn, self.change)
        self.assertIn("migration %d" % self.destroying.version, why)
        self.assertIn("is destructive", why)

    def test_the_additive_step_leaves_the_change_undoable_and_undo_puts_the_rows_back(self):
        self.assertEqual(1, len(self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'")))
        self.migrate(self.additive)
        journal.undo(self.db_path, self.change)
        self.assertEqual([], self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))


# ---- The owner's library at 32 meets migration 33 ------------------------------------------------------------------------

class TheOwnersLibraryAt32MeetsMigration33(Migrated):
    """Made and seeded by the app before 33, as the three live libraries are (at 32, each face's vector in `faces.embedding`,
    2,048 bytes; `faces_pending` empty, `idx_faces_name` there), then opened by this one."""

    NAME = "face vectors in face_embeddings"
    DROPPED = {("table", "faces_pending"), ("index", "idx_faces_name")}
    #: Made by 33, and made again without the column (the faces table, and its generation trigger, which named it).
    MADE = {("table", "face_embeddings"), ("trigger", "face_embeddings_go_with_their_face")} | {
        ("trigger", "generation_face_embeddings_" + event) for event in ("insert", "update", "delete")}
    REMADE = {("table", "faces"), ("trigger", "generation_faces_update")}

    def setUp(self):
        with migration_names.up_to(schema.BASELINE):
            super().setUp()
        self.assertEqual(schema.BASELINE, self.version())
        self.assertLessEqual(self.DROPPED, set(self.objects()))
        self.before = (self.objects(), self.counts(), self.dump())
        self.enterContext(migration_names.up_to(33))   # the app with 33 as its latest: 34 is its own class's

    def detected(self, conn, path, box, embedding, crop, prob):
        """Record a face as the indexer at 32 did: its vector in `faces.embedding`, its crop in `face_crops`."""
        face_id = conn.execute("INSERT INTO faces (photo_id, box, embedding, prob) VALUES (?, ?, ?, ?)",
                               (photo_id(conn, path), json.dumps(box), embedding, prob)).lastrowid
        if crop:
            conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, crop))
        return face_id

    def objects(self):
        """{(type, name): sql} of every table, index and trigger."""
        return {(kind, name): sql for kind, name, sql in self.query(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite%'")}

    def counts(self):
        return {name: self.query("SELECT COUNT(*) FROM %s" % name)[0][0] for (kind, name) in self.objects() if kind == "table"}

    @staticmethod
    def as_33_leaves(dump, columns):
        """`dump` (the library at 32, `columns` its faces' columns) as 33 leaves it: each face without its vector, and the
        vector, byte for byte and of the type it was stored as, in `face_embeddings` under FaceNet's key."""
        at = 2 * columns.index("embedding")   # a dump's row is each value, then its type
        found = dict(dump)
        found["faces"] = [row[:at] + row[at + 2:] for row in dump["faces"]]
        found["face_embeddings"] = [(row[0], "integer", FACENET, "text") + row[at:at + 2] for row in dump["faces"]]
        return found

    def test_it_takes_one_backup_moves_every_vector_and_leaves_every_other_row(self):
        objects, counts, dump = self.before
        columns = [row[1] for row in self.query("PRAGMA table_info(faces)")]
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(33, self.version())
        after = self.objects()
        self.assertLessEqual(self.MADE | self.REMADE, set(after))
        self.assertEqual({k: v for k, v in objects.items() if k not in self.DROPPED | self.REMADE},
                         {k: v for k, v in after.items() if k not in self.MADE | self.REMADE})
        self.assertNotIn("embedding", [row[1] for row in self.query("PRAGMA table_info(faces)")])
        counts.pop("faces_pending")
        counts["schema_version"] += 1
        counts["changes"] += 1   # the migration's own record
        counts["face_embeddings"] = counts["faces"]
        self.assertEqual(counts, self.counts())
        self.assertEqual(self.as_33_leaves(dump, columns), self.dump())
        [taken] = backups(self.db_path)
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 33: " + self.NAME, 32, "applied", schema.DESTRUCTIVE, taken, {"face vectors": 4}),
                         (operation, version, status, summary["kind"], summary["backup"], summary["took"]))
        self.assertIn("every face's vector copied", summary["checks"])
        backup = db_rows(os.path.join(Library(self.db_path).backups, taken),
                         "SELECT type, name FROM sqlite_master WHERE name IN ('faces_pending', 'idx_faces_name')")
        self.assertEqual(self.DROPPED, set(backup), "the copy is the way back")
        plan = " ".join(r[3] for r in self.query("EXPLAIN QUERY PLAN SELECT id FROM faces WHERE name = ?", ("x",)))
        self.assertIn("idx_faces_person", plan, "a lookup by name still has an index")
        self.assertEqual([], schema.ensure(self.db_path))

    def test_a_face_without_facenets_vector_refuses_it_with_a_sentence_until_that_face_is_deleted(self):
        # 0 such faces in the three live libraries (2026-10-18): the check stands between a vector of another length, or
        # none, and filing it as FaceNet's.
        conn = db.connect(self.db_path)
        try:
            path = conn.execute("SELECT path FROM photos WHERE id = ?", (self.ids["prize"],)).fetchone()[0]
            short = self.detected(conn, path, [5, 5, 40, 40], vector(5, 256), None, 0.97)
            none = self.detected(conn, path, [50, 5, 90, 40], None, None, 0.96)
            conn.commit()
        finally:
            conn.close()
        held = (self.objects(), self.counts(), self.dump())
        with self.assertRaises(schema.CheckFailed) as refused:
            schema.ensure(self.db_path)
        self.assertEqual("every face's vector copied", refused.exception.check)
        self.assertIn("Migration 33 (%s) was not applied" % self.NAME, str(refused.exception))
        self.assertIn("2 face(s) hold no vector of FaceNet's length (2048 bytes)", str(refused.exception))
        self.assertIn("remove those faces, and open it again", str(refused.exception))
        self.assertEqual(32, self.version())
        self.assertEqual(held, (self.objects(), self.counts(), self.dump()), "nothing was changed")
        self.execute("DELETE FROM faces WHERE id IN (?, ?)", (short, none))
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(33, self.version())
        self.assertEqual(self.query("SELECT id FROM faces ORDER BY id"),
                         self.query("SELECT face_id FROM face_embeddings WHERE model = ? ORDER BY face_id", (FACENET,)))

    def test_a_kept_library_with_no_face_is_migrated(self):
        self.execute("DELETE FROM faces")   # its crops go with it (face_crops_go_with_their_face)
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(33, self.version())
        self.assertEqual([(0,)], self.query("SELECT COUNT(*) FROM face_embeddings"))
        self.assertEqual({"face vectors": 0}, self.recorded()[-1][3]["took"])

    def test_a_reader_mid_read_keeps_what_it_was_reading_and_its_next_read_is_of_33(self):
        reader = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        first = reader.execute("SELECT id, embedding FROM faces ORDER BY id").fetchall()
        self.assertEqual([self.NAME], schema.ensure(self.db_path), "a reader does not hold the migration back")
        self.assertEqual(first, reader.execute("SELECT id, embedding FROM faces ORDER BY id").fetchall(),
                         "the read it was in the middle of is of the library as it was")
        reader.rollback()
        self.assertEqual(first, reader.execute("SELECT face_id, vector FROM face_embeddings WHERE model = ? ORDER BY face_id",
                                               (FACENET,)).fetchall())

    def test_of_two_libraries_in_one_home_only_the_one_opened_is_migrated(self):
        other = self.home.library("cove.db")
        with migration_names.up_to(schema.BASELINE):
            schema.ensure(other)
            conn = db.connect(other)
            try:
                self.detected(conn, os.path.join(self.home.root, "Cove", "cove_001.jpg"), [1, 2, 30, 40], vector(9), None, 0.99)
                conn.commit()
            finally:
                conn.close()
        before = digest(other)
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(before, digest(other), "the other library's file is as it was")
        self.assertEqual([(32,)], db_rows(other, "SELECT MAX(version) FROM schema_version"))
        self.assertEqual([], backups(other))
        self.assertEqual(1, len(backups(self.db_path)))

    def test_after_it_a_deleted_face_takes_its_vectors_and_a_written_vector_moves_the_faces_generation(self):
        schema.ensure(self.db_path)
        face = self.ids["copy"]
        conn = db.connect(self.db_path)
        try:
            moved = generations.value(conn, "faces")
            add_vector_of_face(conn, face, vector(7), "scrfd-adaface")   # a second model's vector beside FaceNet's
            conn.commit()
            self.assertEqual(moved + 1, generations.value(conn, "faces"), "a grid of the faces is stale")
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM face_embeddings WHERE face_id = ?", (face,)).fetchone()[0])
            conn.execute("DELETE FROM faces WHERE id = ?", (face,))
            conn.commit()
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM face_embeddings WHERE face_id = ?", (face,)).fetchone()[0])
        finally:
            conn.close()

    def test_killed_after_the_backup_it_is_still_at_32_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 33]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the vectors copied and the column dropped, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 33) + (real._replace(apply=killed),)
            schema.LATEST = 33
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(1, len(backups(self.db_path)), "the backup was taken")
        self.assertEqual(32, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump()), "as it was")
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(33, self.version())
        self.assertFalse(self.DROPPED & set(self.objects()))

    def test_the_app_before_33_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(schema.BASELINE), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((33, 32), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")


# ---- The owner's library at 33 meets migration 34 ------------------------------------------------------------------------

class TheOwnersLibraryAt33MeetsMigration34(Migrated):
    """Made and seeded by the app before 34, as the three live libraries are (at 33), with a change of rows made at 33,
    then opened by this one (owner, 2026-10-24; docs/PHASE6_DESIGN.md, section 7)."""

    NAME = "photo fingerprints"
    KEY = "upright-sha256-1"

    def setUp(self):
        with migration_names.up_to(33):
            super().setUp()
            self.change = journal.apply(self.db_path, "add a node",
                                        [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})]).change_id
        self.assertEqual(33, self.version())
        self.assertNotIn("photo_fingerprints", self.tables())
        self.before = (self.objects(), self.counts(), self.dump())
        self.enterContext(migration_names.up_to(34))   # the app with 34 as its latest: 35 is its own class's

    objects = TheOwnersLibraryAt32MeetsMigration33.objects
    counts = TheOwnersLibraryAt32MeetsMigration33.counts

    def test_it_adds_the_empty_table_takes_no_backup_and_a_change_made_at_33_stays_undoable(self):
        objects, counts, dump = self.before
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(34, self.version())
        after = self.objects()
        self.assertEqual({("table", "photo_fingerprints")}, set(after) - set(objects), "the table and nothing else")
        self.assertEqual(objects, {k: v for k, v in after.items() if k != ("table", "photo_fingerprints")})
        counts.update(schema_version=counts["schema_version"] + 1, changes=counts["changes"] + 1, photo_fingerprints=0)
        self.assertEqual(counts, self.counts())
        self.assertEqual(dump, self.dump(), "every row as it was")
        self.assertEqual([], backups(self.db_path), "an additive migration takes none")
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 34: " + self.NAME, 33, "applied", schema.ADDITIVE),
                         (operation, version, status, summary["kind"]))
        self.assertNotIn("backup", summary)
        self.assertIn("nothing that was there changed", summary["checks"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.change))
        journal.undo(self.db_path, self.change)
        self.assertEqual([], self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))
        self.assertEqual([], schema.ensure(self.db_path))

    def test_killed_part_way_it_is_still_at_33_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 34]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the table made, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 34) + (real._replace(apply=killed),)
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(33, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump()), "as it was")
        self.assertEqual([], backups(self.db_path))
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(34, self.version())

    def test_the_app_before_34_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(33), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((34, 33), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")

    def test_after_it_a_photos_fingerprint_is_kept_and_reads_as_none_once_the_photo_is_deleted(self):
        schema.ensure(self.db_path)
        first, second = hashlib.sha256(b"the upright decode").digest(), hashlib.sha256(b"decoded again").digest()
        conn = db.connect(self.db_path)
        try:
            photo_fingerprints.put(conn, self.ids["prize"], self.KEY, first)
            photo_fingerprints.put(conn, self.ids["prize"], self.KEY, second)   # in place of the first
            conn.commit()
            self.assertEqual((self.KEY, second), photo_fingerprints.get(conn, self.ids["prize"]))
            self.assertIsNone(photo_fingerprints.get(conn, self.ids["start"]), "none recorded")
            path = conn.execute("SELECT path FROM photos WHERE id = ?", (self.ids["prize"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(1, store_photos.forget_photo(self.db_path, path)["photos"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertIsNone(photo_fingerprints.get(conn, self.ids["prize"]), "its photo is gone")
        self.assertEqual([(1,)], conn.execute("SELECT COUNT(*) FROM photo_fingerprints").fetchall(),
                         "the row stays: no foreign key, no trigger, nothing the journal accounts for")


# ---- The owner's library at 34 meets migration 35 ------------------------------------------------------------------------

class TheOwnersLibraryAt34MeetsMigration35(Migrated):
    """Made and seeded by the app before 35, as the three live libraries are (at 34, each photo's vector in `embeddings` with
    the stamp of its file), with a change of rows made at 34, then opened by this one (owner, 2026-10-27;
    docs/PLAN_KEEP_IN_STEP.md, cut 1c)."""

    NAME = "the vector stamps in an index"
    INDEX = ("index", "idx_embeddings_stamp")

    def setUp(self):
        with migration_names.up_to(34):
            super().setUp()
            self.change = journal.apply(self.db_path, "add a node",
                                        [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})]).change_id
        self.assertEqual(34, self.version())
        self.assertNotIn(self.INDEX, set(self.objects()))
        self.before = (self.objects(), self.counts(), self.dump())
        self.enterContext(migration_names.up_to(35))   # the app with 35 as its latest: 36 is its own class's

    objects = TheOwnersLibraryAt32MeetsMigration33.objects
    counts = TheOwnersLibraryAt32MeetsMigration33.counts

    def stamp_join(self):
        """What the query a sync asks of the vectors' stamps (store.photos.stamps with `vector_stamps`) answers."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return sorted(store_photos.stamps(conn, vector_stamps=MODEL))
        finally:
            conn.close()

    def test_it_adds_the_index_takes_no_backup_and_a_change_made_at_34_stays_undoable(self):
        objects, counts, dump = self.before
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(35, self.version())
        after = self.objects()
        self.assertEqual({self.INDEX}, set(after) - set(objects), "the index and nothing else")
        self.assertEqual(objects, {k: v for k, v in after.items() if k != self.INDEX})
        counts.update(schema_version=counts["schema_version"] + 1, changes=counts["changes"] + 1)
        self.assertEqual(counts, self.counts())
        self.assertEqual(dump, self.dump(), "every row as it was")
        self.assertEqual([], backups(self.db_path), "an additive migration takes none")
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 35: " + self.NAME, 34, "applied", schema.ADDITIVE),
                         (operation, version, status, summary["kind"]))
        self.assertNotIn("backup", summary)
        self.assertIn("nothing that was there changed", summary["checks"])
        self.assertIn("rows kept in embeddings", summary["checks"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.change))
        journal.undo(self.db_path, self.change)
        self.assertEqual([], self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))
        self.assertEqual([], schema.ensure(self.db_path))

    def test_a_syncs_stamp_join_answers_as_before_and_reads_no_vector(self):
        """The query names the index (INDEXED BY), so it can only run at 35; at 34 the same join is asked by hand."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            answer = sorted(conn.execute(
                "SELECT p.id, p.path, p.mtime, p.size, e.mtime, e.size FROM photos p"
                " LEFT JOIN embeddings e ON e.photo_id = p.id AND e.model = ?", (MODEL,)).fetchall())
        finally:
            conn.close()
        self.assertEqual(3, sum(1 for row in answer if row[-1] is not None), "each seeded photo holds a stamped vector")
        schema.ensure(self.db_path)
        after = sorted((r[0], r[1], r[2], r[3]) + (r[4] or (None, None)) for r in self.stamp_join())
        self.assertEqual(answer, after)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            seen = []
            conn.set_trace_callback(seen.append)
            store_photos.stamps(conn, vector_stamps=MODEL)
            conn.set_trace_callback(None)
            [sql] = [q for q in seen if "embeddings" in q]
            plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall())
        finally:
            conn.close()
        self.assertIn("COVERING INDEX idx_embeddings_stamp", plan)

    def test_killed_part_way_it_is_still_at_34_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 35]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the index made, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 35) + (real._replace(apply=killed),)
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(34, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump()), "as it was")
        self.assertEqual([], backups(self.db_path))
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(35, self.version())
        self.assertIn(self.INDEX, set(self.objects()))

    def test_the_app_before_35_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(34), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((35, 34), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")


# ---- The owner's library at 35 meets migration 36 ------------------------------------------------------------------------

class TheOwnersLibraryAt35MeetsMigration36(Migrated):
    """Made and seeded by the app before 36, as the three live libraries are (at 35, 323, 112 and 96 runs in `job_runs`, one
    of photo_index's running under the owner's server, counted 2026-10-29), with its runs written by the writers of runs and a
    change of rows made at 35, then opened by this one (owner, 2026-10-28; DECISIONS, "ACTIVITY REWORK";
    docs/PLAN_ACTIVITY.md, B2)."""

    NAME = "runs nest and say how far they got"
    INDEX = ("index", "idx_job_runs_parent")
    TABLE = ("table", "job_runs")
    ADDED = ("parent", "heartbeat", "progress")

    def setUp(self):
        with migration_names.up_to(35):
            super().setUp()
            self.change = journal.apply(self.db_path, "add a node",
                                        [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})]).change_id
            # The runs as the app at 35 leaves them: a job that ended, one under way, and a sync's record. Its claim inserted
            # the columns there were at 35 (today's names a parent), its finish and record_sync are today's.
            ended = self.claimed_at_35("snapshots", 1790000000.0)
            job_runs.finish(self.db_path, ended, job_runs.DONE, 1790000060.0, {"changed": 1, "bytes": 4096})
            self.running = self.claimed_at_35("sync", 1790000100.0)
            job_runs.record_sync(self.db_path, job_runs.stamp(1790000200.0), True, True, {"new": 2}, {"new": 2})
        self.assertEqual(35, self.version())
        self.assertNotIn(self.INDEX, set(self.objects()))
        self.before = (self.objects(), self.counts(), self.dump(), self.runs())
        self.read_at_35 = self.readers()
        self.enterContext(migration_names.up_to(36))   # the app with 36 as its latest: 37 is its own class's

    objects = TheOwnersLibraryAt32MeetsMigration33.objects
    counts = TheOwnersLibraryAt32MeetsMigration33.counts

    def claimed_at_35(self, job, now):
        """A run begun by this process as the claim of the app at 35 inserted one. Its id."""
        return self.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES (?, 'harbour', ?, 'running', ?)",
                            (job, job_runs.stamp(now), file_journal.owner()))

    def runs(self, columns=None):
        """Every run, each value with the type SQLite stored it as, by id: the columns there were at 35 unless named."""
        columns = columns or [row[1] for row in self.query("PRAGMA table_info(job_runs)") if row[1] not in self.ADDED]
        typed = ", ".join("%s, typeof(%s)" % (c, c) for c in columns)
        return self.query("SELECT %s FROM job_runs ORDER BY id" % typed)

    def readers(self):
        """What each reader of the runs answers: the pages', the MCP's and the recurring runner's."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            syncs = (job_runs.recent_syncs(conn), job_runs.last_sync(conn))
        finally:
            conn.close()
        return (job_runs.latest(self.db_path), job_runs.runs(self.db_path), job_runs.get(self.db_path, self.running), syncs)

    def test_it_adds_the_columns_takes_no_backup_and_a_change_made_at_35_stays_undoable(self):
        objects, counts, dump, runs = self.before
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(36, self.version())
        after = self.objects()
        self.assertEqual({self.INDEX}, set(after) - set(objects), "the index and nothing else new")
        self.assertEqual({k: v for k, v in objects.items() if k != self.TABLE},
                         {k: v for k, v in after.items() if k not in (self.INDEX, self.TABLE)}, "nothing else changed")
        columns = [row[1] for row in self.query("PRAGMA table_info(job_runs)")]
        self.assertEqual(list(self.ADDED), columns[-3:], "added at the end")
        counts.update(schema_version=counts["schema_version"] + 1, changes=counts["changes"] + 1)
        self.assertEqual(counts, self.counts())
        self.assertEqual(dump, self.dump(), "every row as it was")
        self.assertEqual(runs, self.runs(), "every run as it was")
        self.assertEqual([(3,)], self.query("SELECT COUNT(*) FROM job_runs WHERE parent IS NULL AND heartbeat IS NULL"
                                            " AND progress IS NULL"), "NULL in every run there was")
        self.assertEqual([], backups(self.db_path), "an additive migration takes none")
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 36: " + self.NAME, 35, "applied", schema.ADDITIVE),
                         (operation, version, status, summary["kind"]))
        self.assertNotIn("backup", summary)
        self.assertIn("nothing that was there changed", summary["checks"])
        self.assertIn("rows kept", summary["checks"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.change))
        journal.undo(self.db_path, self.change)
        self.assertEqual([], self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Fresh'"))
        schema.forget()
        self.assertEqual([], schema.ensure(self.db_path), "a second open applies nothing")
        plan = " ".join(str(r[-1]) for r in self.query("EXPLAIN QUERY PLAN SELECT id FROM job_runs WHERE parent = ?", (1,)))
        self.assertIn("idx_job_runs_parent", plan)

    def test_the_readers_of_the_runs_answer_alike_at_35_and_at_36(self):
        """The MCP server and the Activity page read a library without migrating it: at 35 until the owner's server opens it."""
        schema.ensure(self.db_path)
        self.assertEqual(36, self.version())
        self.assertEqual(self.read_at_35, self.readers())
        latest, listed, running, (recent, last) = self.read_at_35
        self.assertEqual(3, len(listed))
        self.assertEqual(job_runs.RUNNING, running.outcome)
        self.assertEqual((1, True), (len(recent), last["last_run"]["in_step"]))

    def test_two_processes_opening_it_at_once_apply_it_once(self):
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema;"
                " schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version <= 36); schema.LATEST = 36;"
                " print(len(schema.ensure(%r)))" % (ROOT, self.db_path))   # each the app with 36 as its latest
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(1, sum(int(answer) for answer in answers), answers)
        self.assertEqual(36, self.version())
        self.assertEqual(self.before[3], self.runs())
        self.assertEqual(["migration 36: " + self.NAME], [op for op, *_ in self.recorded() if op.startswith("migration 36")])

    def test_killed_part_way_it_is_still_at_35_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 36]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the columns and the index made, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 36) + (real._replace(apply=killed),)
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(35, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump(), self.runs()), "as it was")
        self.assertEqual([], backups(self.db_path))
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(36, self.version())
        self.assertIn(self.INDEX, set(self.objects()))

    def test_the_app_before_36_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(35), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((36, 35), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")


# ---- The owner's library at 36 meets migration 37 ------------------------------------------------------------------------

class TheOwnersLibraryAt36MeetsMigration37(Migrated):
    """Made and seeded by the app before 37, as the three live libraries are (at 36: 59,523, 2,065 and 1,309 photos, none
    whose path has a video's extension; 51,451, 4,696 and 4,318 files in `change_files`, none `writing`; counted read-only
    2026-11-02), with a file of a change of photo files written and a photo deleted by a journaled change at 36, then opened
    by this one (owner, 2026-11-02; DECISIONS, "VIDEO: THE OWNERS' PLANS AND THE OWNER'S ANSWERS", 4)."""

    NAME = "photos know their kind; a patch is recorded before it is written"
    PHOTOS, FILES = ("table", "photos"), ("table", "change_files")

    def setUp(self):
        with migration_names.up_to(36):
            super().setUp()
            self.whole = self.dump()
            # A file of a change of photo files as the app at 36 leaves one: planned, marked writing with its stamp, done.
            start = self.query("SELECT path FROM photos WHERE id = ?", (self.ids["start"],))[0][0]
            self.saved, [planned] = file_journal.plan(self.db_path, "save photo", [
                {"photo_id": self.ids["start"], "path": start, "before": {"XMP:Title": ["Start line"]},
                 "after": {"XMP:Title": ["Finish line"]}}])
            # By the statements the app at 36 ran (file_journal.writing, then mark): today's also erase a patch's record,
            # a column a library at 36 does not have, and no app at 37 writes a library before it migrates it.
            conn = db.connect(self.db_path)
            try:
                conn.execute("UPDATE change_files SET state = 'writing', note = NULL, stamp = ? WHERE id = ?",
                             ("[1727000000.5, 4812346]", planned.id))
                conn.commit()
                conn.execute("UPDATE change_files SET state = 'done', note = NULL WHERE id = ?", (planned.id,))
                conn.commit()
            finally:
                conn.close()
            self.deletion = journal.apply(self.db_path, "delete", [journal.delete("photos", (self.ids["prize"],), {})]).change_id
        self.assertEqual(36, self.version())
        self.before = (self.objects(), self.counts(), self.dump())
        self.files_at_36 = file_journal.files_of(self.db_path, self.saved)
        self.enterContext(migration_names.up_to(37))   # the app with 37 as its latest: 38 is its own class's

    objects = TheOwnersLibraryAt32MeetsMigration33.objects
    counts = TheOwnersLibraryAt32MeetsMigration33.counts

    @staticmethod
    def as_37_leaves(dump):
        """`dump` (a library at 36) as 37 leaves it: each photo a 'photo', stored as text."""
        return dict(dump, photos=[row + ("photo", "text") for row in dump["photos"]])

    def test_it_adds_the_columns_takes_no_backup_and_a_photo_deleted_at_36_comes_back_a_photo(self):
        objects, counts, dump = self.before
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(37, self.version())
        after = self.objects()
        self.assertEqual({k: v for k, v in objects.items() if k not in (self.PHOTOS, self.FILES)},
                         {k: v for k, v in after.items() if k not in (self.PHOTOS, self.FILES)}, "nothing else changed")
        self.assertEqual("kind", [row[1] for row in self.query("PRAGMA table_info(photos)")][-1], "added at the end")
        self.assertEqual(["patch", "patch_before"], [row[1] for row in self.query("PRAGMA table_info(change_files)")][-2:])
        counts.update(schema_version=counts["schema_version"] + 1, changes=counts["changes"] + 1)
        self.assertEqual(counts, self.counts())
        self.assertEqual(self.as_37_leaves(dump), self.dump(), "every row as it was, each photo a 'photo'")
        self.assertEqual([(1,)], self.query("SELECT COUNT(*) FROM change_files WHERE patch IS NULL AND patch_before IS NULL"),
                         "no file there was holds a patch")
        self.assertEqual([], backups(self.db_path), "an additive migration takes none")
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 37: " + self.NAME, 36, "applied", schema.ADDITIVE),
                         (operation, version, status, summary["kind"]))
        self.assertNotIn("backup", summary)
        self.assertIn("nothing that was there changed", summary["checks"])
        self.assertIn("rows kept in photos, change_files", summary["checks"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.deletion))
        journal.undo(self.db_path, self.deletion)
        self.assertEqual(self.as_37_leaves(self.whole), self.dump(), "the photo back, a 'photo', with all it took")
        schema.forget()
        self.assertEqual([], schema.ensure(self.db_path), "a second open applies nothing")

    def test_a_photo_added_after_it_is_a_photo_and_no_other_kind_is_stored(self):
        """The writers of photo rows name no kind: the indexer's read and Suggest's row of a photo it never read."""
        schema.ensure(self.db_path)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        read = photo_rows.add_read(conn, os.path.join(self.home.root, "Harbour Regatta", "regatta_004.jpg"),
                                   {"XMP:Subject": ["Activity/Sailing"], "EXIF:DateTimeOriginal": "2024:06:02 10:00:00"})
        unread = photo_rows.add_unread(conn, os.path.join(self.home.root, "Harbour Regatta", "regatta_005.jpg"))
        conn.commit()
        self.assertEqual([("photo",), ("photo",)],
                         conn.execute("SELECT kind FROM photos WHERE id IN (?, ?)", (read, unread)).fetchall())
        for wrong in ("clip", "Video", None):
            with self.subTest(kind=wrong), self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE photos SET kind = ? WHERE id = ?", (wrong, read))
        conn.rollback()
        self.assertEqual(self.files_at_36, file_journal.files_of(self.db_path, self.saved), "the files read alike at 36 and 37")

    def test_two_processes_opening_it_at_once_apply_it_once(self):
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema; print(len(schema.ensure(%r)))"
                % (ROOT, self.db_path))
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(1, sum(int(answer) for answer in answers), answers)
        self.assertEqual(37, self.version())
        self.assertEqual(self.as_37_leaves(self.before[2]), self.dump())
        self.assertEqual(["migration 37: " + self.NAME], [op for op, *_ in self.recorded() if op.startswith("migration 37")])

    def test_killed_part_way_it_is_still_at_36_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 37]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the columns made, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 37) + (real._replace(apply=killed),)
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(36, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump()), "as it was")
        self.assertEqual([], backups(self.db_path))
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(37, self.version())

    def test_the_app_before_37_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(36), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((37, 36), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")


class TheOwnersLibraryAt37MeetsMigration38(Migrated):
    """Made and seeded by the app before 38, as the three live libraries are (at 37: 59,618, 2,065 and 1,309 vectors in
    `embeddings`, one model each; 2,179, 5 and 0 clips, none with a vector; counted read-only 2026-11-06), with a photo and
    its vector deleted by a journaled change at 37, then opened by this one (owner, 2026-11-06; DECISIONS, "MIGRATION 38: A
    CLIP'S FRAMES BESIDE ITS VECTOR")."""

    NAME = "a clip's frames beside its vector"
    MADE = {("table", "embedding_frames"), ("trigger", "embedding_frames_go_with_their_photo"),
            ("trigger", "embedding_frames_go_with_their_vector")}

    def setUp(self):
        with migration_names.up_to(37):
            super().setUp()
            self.whole = self.dump()
            self.deletion = journal.apply(self.db_path, "delete", [journal.delete("photos", (self.ids["prize"],), {})]).change_id
        self.assertEqual(37, self.version())
        self.before = (self.objects(), self.counts(), self.dump())
        self.enterContext(migration_names.up_to(38))   # the app with 38 as its latest

    objects = TheOwnersLibraryAt32MeetsMigration33.objects
    counts = TheOwnersLibraryAt32MeetsMigration33.counts

    def test_it_adds_the_empty_table_keeps_every_vector_byte_for_byte_and_a_deletion_at_37_stays_undoable(self):
        objects, counts, dump = self.before
        self.assertEqual(2, len(dump["embeddings"]), "the vectors of the two photos left, as the indexer stored them")
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(38, self.version())
        after = self.objects()
        self.assertEqual(self.MADE, set(after) - set(objects), "the table and its two triggers, nothing else new")
        self.assertEqual(objects, {k: v for k, v in after.items() if k not in self.MADE}, "nothing else changed")
        counts.update(schema_version=counts["schema_version"] + 1, changes=counts["changes"] + 1, embedding_frames=0)
        self.assertEqual(counts, self.counts())
        self.assertEqual(dump, self.dump(), "every row as it was, each vector byte for byte")
        self.assertEqual([], backups(self.db_path), "an additive migration takes none")
        operation, version, status, summary = self.recorded()[-1]
        self.assertEqual(("migration 38: " + self.NAME, 37, "applied", schema.ADDITIVE),
                         (operation, version, status, summary["kind"]))
        self.assertIn("nothing that was there changed", summary["checks"])
        self.assertIn("rows kept in embeddings", summary["checks"])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual([], journal.refusal(conn, self.deletion))
        journal.undo(self.db_path, self.deletion)
        self.assertEqual(self.whole, self.dump(), "the photo back with its vector, and no frame")
        self.assertEqual([(0,)], self.query("SELECT COUNT(*) FROM embedding_frames"))
        schema.forget()
        self.assertEqual([], schema.ensure(self.db_path), "a second open applies nothing")

    def test_two_processes_opening_it_at_once_apply_it_once(self):
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema; print(len(schema.ensure(%r)))"
                % (ROOT, self.db_path))
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(1, sum(int(answer) for answer in answers), answers)
        self.assertEqual(38, self.version())
        self.assertEqual(self.before[2], self.dump())
        self.assertEqual(["migration 38: " + self.NAME], [op for op, *_ in self.recorded() if op.startswith("migration 38")])

    def test_killed_part_way_it_is_still_at_37_and_migrates_at_the_next_open(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, %(root)r)
            from tagpup.store import schema
            [real] = [m for m in schema.MIGRATIONS if m.version == 38]

            def killed(conn):
                real.apply(conn)
                os._exit(9)   # the power goes: the table made, nothing committed

            schema.MIGRATIONS = tuple(m for m in schema.MIGRATIONS if m.version < 38) + (real._replace(apply=killed),)
            schema.ensure(%(path)r)
            """) % {"root": ROOT, "path": self.db_path}
        child = processes.start([sys.executable, "-c", code], env=dict(os.environ, TAGPUP_HOME=self.home.root),
                                stdout=-1, stderr=-1, text=True)
        _out, err = child.communicate(timeout=120)
        self.assertEqual(9, child.returncode, err)
        self.assertEqual(37, self.version())
        self.assertEqual(self.before, (self.objects(), self.counts(), self.dump()), "as it was")
        schema.forget()
        self.assertEqual([self.NAME], schema.ensure(self.db_path))
        self.assertEqual(38, self.version())

    def test_the_app_before_38_refuses_it_after(self):
        schema.ensure(self.db_path)
        before = digest(self.db_path)
        with migration_names.up_to(37), self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.db_path)
        self.assertEqual((38, 37), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, digest(self.db_path), "nothing written")


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
        schema.forget()
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

    def test_a_current_library_is_opened_without_a_write_a_migration_or_a_backup(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        schema.forget(path)
        before = digest(path)
        self.assertIsNone(schema.newer_problem(path))
        self.assertEqual([], schema.ensure(path))
        self.assertEqual(before, digest(path))
        self.assertEqual([], backups(path))
        self.assertEqual(schema.LATEST, schema.version_of(path))

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
        self.assertEqual([(32,), (33,), (34,), (35,), (36,), (37,)],
                         db_rows(path, "SELECT version FROM schema_version ORDER BY version"))
        self.assertEqual([], db_rows(path, "SELECT name FROM sqlite_master WHERE name IN ('faces_pending', 'idx_faces_name')"),
                         "made at the baseline and taken to 37 by the same runner")
        self.assertEqual([("idx_embeddings_stamp",)], db_rows(path, "SELECT name FROM sqlite_master WHERE name = 'idx_embeddings_stamp'"),
                         "the index from migration 35, not the baseline")
        self.assertEqual([("face_embeddings",)], db_rows(path, "SELECT name FROM sqlite_master WHERE name = 'face_embeddings'"))
        self.assertNotIn("embedding", [row[1] for row in db_rows(path, "PRAGMA table_info(faces)")])
        self.assertEqual(["parent", "heartbeat", "progress"], [row[1] for row in db_rows(path, "PRAGMA table_info(job_runs)")][-3:],
                         "the runs' columns from migration 36")
        self.assertEqual("kind", [row[1] for row in db_rows(path, "PRAGMA table_info(photos)")][-1])
        self.assertEqual(["patch", "patch_before"], [row[1] for row in db_rows(path, "PRAGMA table_info(change_files)")][-2:],
                         "the kind and the patch's record from migration 37")

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
        self.assertEqual(6, sum(int(answer) for answer in answers), answers)
        self.assertEqual([(32,), (33,), (34,), (35,), (36,), (37,)],
                         db_rows(path, "SELECT version FROM schema_version ORDER BY version"))


if __name__ == "__main__":
    unittest.main()

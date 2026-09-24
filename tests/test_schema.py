"""A library's tables come from one place, tagpup.store.schema.

There were four: PhotoIndex.load, TagTuner's start-up, the desktop runner and the tag
tree, each with its own idea of the schema (docs/findings.md, #48). Migration 1 makes the
tables of 2026-09; a library older than them is refused, not converted: every library
the owner has was already that shape, and the conversions retired on 2026-09-24.
"""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from tagpup.store import db, generations, schema


def tables(conn):
    return {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def indexes(conn):
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def make_unmigrated_library(db_path):
    """A library as it stood before phase 3: today's columns, none of the indexes added
    later, no schema_version, and the two generation tables the servers kept."""
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
                     " hidden_from_autocomplete INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO photos (path, mtime, size, tags, people) VALUES ('D:/a.jpg', 1.0, 1, '[]', '[]')")
        conn.execute("INSERT INTO faces (photo_path, box, name, crop_image)"
                     " VALUES ('D:/a.jpg', '[0,0,1,1]', 'Wren Halloway', x'FFD8')")
        for name, value in (("faces", 7), ("taxonomy", 3)):
            conn.execute("CREATE TABLE %s_generation (id INTEGER PRIMARY KEY CHECK (id = 1),"
                         " generation INTEGER NOT NULL)" % name)
            conn.execute("INSERT INTO %s_generation VALUES (1, ?)" % name, (value,))
        conn.execute("CREATE TRIGGER faces_generation_insert AFTER INSERT ON faces"
                     " BEGIN UPDATE faces_generation SET generation = generation + 1 WHERE id = 1; END")
        conn.commit()
    finally:
        conn.close()


def make_runner_library(db_path):
    """A library as the desktop runner made one: a faces table without the columns every
    screen now filters on."""
    conn = db.connect(db_path)
    try:
        conn.execute("CREATE TABLE photos (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT,"
                     " people TEXT, captions TEXT, raw_metadata TEXT, embedding BLOB)")
        conn.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY AUTOINCREMENT, photo_path TEXT, box TEXT,"
                     " embedding BLOB, name TEXT, crop_image BLOB, prob REAL)")
        conn.execute("INSERT INTO faces (photo_path, box, name) VALUES ('D:/a.jpg', '[0,0,1,1]', 'Wren Halloway')")
        conn.commit()
    finally:
        conn.close()


class SchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="schema_")
        self.db_path = os.path.join(self.dir, "library.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def connect(self):
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn


class ANewLibrary(SchemaTestCase):
    def test_has_every_table_and_is_current(self):
        applied = schema.ensure(self.db_path)
        conn = self.connect()
        self.assertEqual([m.name for m in schema.MIGRATIONS], applied)
        self.assertEqual(schema.LATEST, schema.version(conn))
        self.assertEqual({"photos", "faces", "face_crops", "embedding_cache", "tag_taxonomy",
                          "tag_embeddings", "generations", "schema_version"}, tables(conn))

    def test_has_the_document_id_index(self):
        # Only a library that gained the column by migration had it.
        schema.ensure(self.db_path)
        self.assertIn("idx_photos_document_id", indexes(self.connect()))

    def test_is_left_alone_the_second_time(self):
        schema.ensure(self.db_path)
        self.assertEqual([], schema.ensure(self.db_path))
        self.assertEqual([], schema.pending(self.db_path))

    def test_made_again_at_the_same_path_is_made_again(self):
        # ensure() remembers a library it found current. A test, or a person, that
        # deletes a library and makes another in its place has a different file.
        schema.ensure(self.db_path)
        os.remove(self.db_path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.remove(self.db_path + suffix)
        schema.ensure(self.db_path)
        self.assertIn("faces", tables(self.connect()))


class ALibraryOlderThanTheColumns(SchemaTestCase):
    def test_is_refused_and_left_as_it_was(self):
        make_runner_library(self.db_path)
        with self.assertRaises(schema.TooOld) as refused:
            schema.ensure(self.db_path)
        self.assertIn("faces.excluded", str(refused.exception))
        conn = self.connect()
        self.assertEqual(0, schema.version(conn))
        self.assertNotIn("excluded", columns(conn, "faces"))
        self.assertEqual({"photos", "faces", "schema_version"}, tables(conn))


class ALibraryRestoredInPlace(SchemaTestCase):
    def test_is_migrated_again(self):
        # Copying a backup over the file keeps its id and creation time on Windows, and
        # a library restored from before the migration stayed unmigrated (#56).
        schema.ensure(self.db_path)
        old = os.path.join(self.dir, "backup.db")
        make_unmigrated_library(old)
        shutil.copyfile(old, self.db_path)
        schema.ensure(self.db_path)
        self.assertEqual(schema.LATEST, schema.version(self.connect()))


class AnUnmigratedLibrary(SchemaTestCase):
    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)

    def test_gains_the_indexes_the_screens_need(self):
        schema.ensure(self.db_path)
        self.assertLessEqual({"idx_faces_identify", "idx_photos_document_id"}, indexes(self.connect()))

    def test_keeps_its_rows(self):
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertEqual(("Wren Halloway", 0), conn.execute("SELECT name, excluded FROM faces").fetchone())

    def test_is_copied_once_before_it_is_migrated(self):
        # However many pending migrations rewrite data, one copy from before the first
        # holds the library as it was for all of them: a second was another 3 GB of
        # photo_index, and another of the five kept (#76).
        another = schema.Migration(schema.LATEST + 1, "another rewrite", lambda conn: None, changes_data=True)
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS + (another,)):
            schema.ensure(self.db_path)
        self.assertEqual(1, len(os.listdir(os.path.join(self.dir, "backups"))))

    def test_its_crops_move_to_their_own_table_and_go_with_their_face(self):
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertNotIn("crop_image", columns(conn, "faces"))
        self.assertEqual([(b"\xff\xd8",)], conn.execute("SELECT jpeg FROM face_crops").fetchall())
        conn.execute("DELETE FROM faces")    # on a connection without foreign keys
        conn.commit()
        self.assertEqual([], conn.execute("SELECT jpeg FROM face_crops").fetchall())

    def test_carries_its_generations_over_and_drops_the_old_tables(self):
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertEqual(7, generations.value(conn, "faces"))
        self.assertEqual(3, generations.value(conn, "taxonomy"))
        self.assertFalse({"faces_generation", "taxonomy_generation"} & tables(conn))
        triggers = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        self.assertNotIn("faces_generation_insert", triggers)

    def test_opened_by_many_at_once_is_migrated_once(self):
        errors = []

        def open_it():
            try:
                schema._ensure(self.db_path)   # past the in-process memory, as another process would be
            except Exception as e:  # noqa: BLE001 - collected and reported below
                errors.append(e)

        threads = [threading.Thread(target=open_it) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        rows = self.connect().execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        self.assertEqual([(m.version,) for m in schema.MIGRATIONS], rows)


class AnOlderVersionStillRunning(SchemaTestCase):
    def test_its_counters_made_again_are_taken_away_on_the_next_open(self):
        # An older version of the app makes faces_generation again on each load, after
        # migration 2 dropped it, and schema_version says there is nothing left to do
        # (docs/findings.md, #55).
        schema.ensure(self.db_path)
        conn = self.connect()
        conn.execute("CREATE TABLE faces_generation (id INTEGER PRIMARY KEY, generation INTEGER NOT NULL)")
        conn.execute("CREATE TRIGGER faces_generation_insert AFTER INSERT ON faces"
                     " BEGIN UPDATE faces_generation SET generation = generation + 1 WHERE id = 1; END")
        conn.commit()
        schema._current.clear()   # the next process to open it
        schema.ensure(self.db_path)
        left = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'faces_generation%'")}
        self.assertEqual(set(), left)


class Generations(SchemaTestCase):
    def setUp(self):
        super().setUp()
        schema.ensure(self.db_path)
        self.conn = self.connect()
        self.conn.execute("INSERT INTO photos (path, mtime, size, people) VALUES ('D:/a.jpg', 1.0, 1, '[]')")
        self.conn.execute("INSERT INTO faces (photo_path, box) VALUES ('D:/a.jpg', '[0,0,1,1]')")
        self.conn.commit()

    def moved(self, name, statement):
        before = generations.value(self.conn, name)
        self.conn.execute(statement)
        self.conn.commit()
        return generations.value(self.conn, name) != before

    def test_a_change_to_a_photo_row_moves_photos(self):
        self.assertTrue(self.moved("photos", "UPDATE photos SET people = '[\"Wren Halloway\"]'"))

    def test_naming_a_face_moves_faces(self):
        self.assertTrue(self.moved("faces", "UPDATE faces SET name = 'Wren Halloway'"))

    def test_caching_a_crop_does_not_move_faces(self):
        self.assertFalse(self.moved("faces", "INSERT INTO face_crops (face_id, jpeg)"
                                              " SELECT MIN(id), x'00' FROM faces"))

    def test_a_new_node_moves_the_tree(self):
        self.assertTrue(self.moved("taxonomy", "INSERT INTO tag_taxonomy (tag, name) VALUES ('Pets', 'Pets')"))

    def test_a_library_without_them_reads_zero(self):
        other = db.connect(os.path.join(self.dir, "empty.db"))
        self.addCleanup(other.close)
        self.assertEqual((0, 0, 0), generations.values(other))


class GenerationCache(SchemaTestCase):
    def setUp(self):
        super().setUp()
        schema.ensure(self.db_path)
        self.conn = self.connect()
        self.builds = 0

        def build(conn):
            self.builds += 1
            return conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0]

        self.cache = generations.Cache(["taxonomy"], build)

    def test_is_built_once_while_nothing_changes(self):
        self.assertEqual(0, self.cache.get(self.conn, self.db_path))
        self.assertEqual(0, self.cache.get(self.conn, self.db_path))
        self.assertEqual(1, self.builds)

    def test_is_built_again_after_a_change_by_anyone(self):
        self.cache.get(self.conn, self.db_path)
        other = db.connect(self.db_path)
        other.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Pets', 'Pets')")
        other.commit()
        other.close()
        self.assertEqual(1, self.cache.get(self.conn, self.db_path))
        self.assertEqual(2, self.builds)


if __name__ == "__main__":
    unittest.main()

"""A library's tables come from one place, tagpup.store.schema.

There were four: PhotoIndex.load, TagTuner's start-up, the desktop runner and the tag
tree, each with its own idea of the schema (docs/findings.md, #48). A new library is made by the baseline
(migration 14) and brought to the latest by the steps after it; a library below the baseline is refused
(tests/test_migrations.py, ALibraryBelowTheBaseline).
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

from tagpup.store import db, generations, schema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face  # noqa: E402
from test_migrations import at_version  # noqa: E402


def tables(conn):
    return {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def indexes(conn):
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


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
        self.assertEqual({"photos", "faces", "face_crops", "embeddings", "photo_people", "suggestions", "tag_taxonomy",
                          "tag_embeddings", "generations", "schema_version", "changes", "change_rows", "settings",
                          "change_files", "job_runs", "added_folders",
                          "faces_pending", "faces_detected", "roots", "photo_tags", "folders", "photo_folder",
                          "search_words", "search_names"}
                         | {"search_%s_%s" % (table, shadow) for table in ("words", "names")
                            for shadow in ("data", "idx", "docsize", "config")},
                         tables(conn))

    def test_has_the_document_id_index(self):
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

    def test_its_rows_go_with_their_photo_and_their_face_on_any_connection(self):
        # Only one connection in the app turns foreign keys on: the baseline's triggers are what take a deleted photo's
        # vectors, people and suggestion, and a deleted face's crop, on the others.
        schema.ensure(self.db_path)
        conn = self.connect()
        conn.execute("INSERT INTO photos (path, mtime, size) VALUES ('D:/a.jpg', 1.0, 1)")
        photo = conn.execute("SELECT id FROM photos").fetchone()[0]
        face = add_face(conn, "D:/a.jpg")
        conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, x'FFD8')", (face,))
        conn.execute("INSERT INTO embeddings (photo_id, model, vector) VALUES (?, 'm', x'00')", (photo,))
        conn.execute("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, 0, 'Wren Halloway', 'face')", (photo,))
        conn.execute("INSERT INTO suggestions (photo_id, title) VALUES (?, 'Rowing')", (photo,))
        conn.commit()
        conn.execute("DELETE FROM faces")
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM face_crops").fetchone()[0])
        conn.execute("DELETE FROM photos")
        conn.commit()
        for table in ("embeddings", "photo_people", "suggestions"):
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0], table)

    def test_interrupted_while_it_is_made_leaves_no_table_and_the_next_open_makes_it(self):
        # The baseline is one transaction: a crash after half the tables leaves the file as it was (an empty record of
        # migrations at most), which is a library not yet made, not an old one (nothing is refused or backed up).
        def crashes(conn):
            schema._baseline(conn)
            raise RuntimeError("the power went")

        broken = (schema.MIGRATIONS[0]._replace(apply=crashes),) + schema.MIGRATIONS[1:]
        with mock.patch.object(schema, "MIGRATIONS", broken):
            with self.assertRaises(RuntimeError):
                schema.ensure(self.db_path)
        self.assertEqual({"schema_version"}, tables(self.connect()))
        schema._current.clear()
        self.assertEqual([m.name for m in schema.MIGRATIONS], schema.ensure(self.db_path))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "backups")))

    def test_made_by_many_at_once_is_made_once(self):
        # Two processes (the always-on server and a CLI run) opening a library that is not there yet.
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


class ALibraryRestoredInPlace(SchemaTestCase):
    def test_is_migrated_again(self):
        # Copying a backup over the file keeps its id and creation time on Windows, and
        # a library restored from before the migration stayed unmigrated (#56).
        schema.ensure(self.db_path)
        old = os.path.join(self.dir, "backup.db")
        at_version(old, schema.BASELINE)
        shutil.copyfile(old, self.db_path)
        schema.ensure(self.db_path)
        self.assertEqual(schema.LATEST, schema.version(self.connect()))


class ALibraryAtTheBaseline(SchemaTestCase):
    def setUp(self):
        super().setUp()
        at_version(self.db_path, schema.BASELINE)

    def test_is_copied_once_before_it_is_migrated(self):
        # However many pending migrations destroy something, one copy from before the
        # first holds the library as it was for all of them: a second was another 3 GB
        # of photo_index, and another of the five kept (#76).
        destroying = [m for m in schema.MIGRATIONS if m.kind == schema.DESTRUCTIVE]
        self.assertGreater(len(destroying), 1)
        schema.ensure(self.db_path)
        self.assertEqual(1, len(os.listdir(os.path.join(self.dir, "backups"))))

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
        self.assertEqual(1, len(os.listdir(os.path.join(self.dir, "backups"))))


class Generations(SchemaTestCase):
    def setUp(self):
        super().setUp()
        schema.ensure(self.db_path)
        self.conn = self.connect()
        self.conn.execute("INSERT INTO photos (path, mtime, size) VALUES ('D:/a.jpg', 1.0, 1)")
        add_face(self.conn, "D:/a.jpg")
        self.conn.commit()

    def moved(self, name, statement):
        before = generations.value(self.conn, name)
        self.conn.execute(statement)
        self.conn.commit()
        return generations.value(self.conn, name) != before

    def test_a_change_to_a_photo_row_moves_photos(self):
        self.assertTrue(self.moved("photos", "UPDATE photos SET captions = '[]'"))

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

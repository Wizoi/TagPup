"""A library's tables come from one place, tagpup.store.schema.

There were four: PhotoIndex.load, TagTuner's start-up, the desktop runner and the tag
tree, each with its own idea of the schema (docs/findings.md, #48). Migration 1 makes the
tables of 2026-09; a library older than them is refused, not converted: every library
the owner has was already that shape, and the conversions retired on 2026-09-24.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

from tagpup.core import paths
from tagpup.store import db, embeddings, generations, schema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face  # noqa: E402


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
        self.assertEqual({"photos", "faces", "face_crops", "embeddings", "photo_people", "suggestions", "tag_taxonomy",
                          "tag_embeddings", "generations", "schema_version", "changes", "change_rows"}, tables(conn))

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

    def test_its_faces_point_at_their_photo_by_id(self):
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertNotIn("photo_path", columns(conn, "faces"))
        self.assertEqual([("D:/a.jpg", "Wren Halloway")], conn.execute(
            "SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id").fetchall())

    def test_a_photo_keeps_its_row_number_as_its_id(self):
        conn = self.connect()
        rowid = conn.execute("SELECT rowid FROM photos WHERE path = 'D:/a.jpg'").fetchone()[0]
        conn.close()
        schema.ensure(self.db_path)
        self.assertEqual([(rowid,)], self.connect().execute(
            "SELECT id FROM photos WHERE path = 'D:/a.jpg'").fetchall())

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


class ALibraryWhoseFacesNamedTheirPhotoByPath(SchemaTestCase):
    """Before migration 4 a face named its photo by path: one it was never indexed for,
    or spelled the photo apart from its row. Each face finds its photo, and none is lost."""

    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)
        conn = self.connect()
        conn.execute("INSERT INTO faces (photo_path, box, name) VALUES ('D:/never indexed.jpg', '[0,0,1,1]', NULL)")
        conn.execute("INSERT INTO faces (photo_path, box, name) VALUES ('D:/never indexed.jpg', '[2,2,3,3]', NULL)")
        conn.commit()

    def faces(self):
        return self.connect().execute("SELECT p.path, f.box FROM faces f JOIN photos p ON p.id = f.photo_id"
                                      " ORDER BY f.id").fetchall()

    def test_a_photo_never_indexed_gets_one_row_holding_its_path(self):
        # In the stored form, as photos.ensure_row makes it, or a lookup misses it (#82).
        schema.ensure(self.db_path)
        stored = paths.stored("D:/never indexed.jpg")
        self.assertEqual([(stored, None, None)], self.connect().execute(
            "SELECT path, mtime, size FROM photos WHERE path = ?", (stored,)).fetchall())
        self.assertEqual(3, len(self.faces()))

    @unittest.skipIf(paths.key("D:/a.jpg") != paths.key("d:/A.JPG"), "paths differ by case only on Windows")
    def test_a_face_stays_on_the_row_it_names_exactly(self):
        # Two rows for one file: a face goes to the one it spells, not the first (#82).
        conn = self.connect()
        conn.execute("INSERT INTO photos (path, mtime, size, tags, people) VALUES ('d:/A.JPG', 1.0, 1, '[]', '[]')")
        conn.execute("INSERT INTO faces (photo_path, box) VALUES ('d:/A.JPG', '[4,4,5,5]')")
        conn.commit()
        schema.ensure(self.db_path)
        self.assertIn(("d:/A.JPG", "[4,4,5,5]"), self.faces())

    def test_a_face_that_names_no_photo_stops_the_migration_and_changes_nothing(self):
        conn = self.connect()
        conn.execute("INSERT INTO faces (photo_path, box) VALUES (NULL, '[4,4,5,5]')")
        conn.commit()
        with self.assertRaises(RuntimeError):
            schema.ensure(self.db_path)
        conn = self.connect()
        self.assertEqual(3, schema.version(conn))
        self.assertIn("photo_path", columns(conn, "faces"))

    def test_ids_of_deleted_faces_are_not_given_out_again(self):
        # Rebuilt from the faces still there, the counter fell back to their highest id
        # (#80).
        conn = self.connect()
        gone = conn.execute("INSERT INTO faces (photo_path, box) VALUES ('D:/a.jpg', '[6,6,7,7]')").lastrowid
        conn.execute("DELETE FROM faces WHERE id = ?", (gone,))
        conn.commit()
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertGreater(add_face(conn, "D:/a.jpg"), gone)

    def test_the_id_of_a_deleted_photo_is_not_given_out_again(self):
        # A face left on it would join the new photo, with its name (#81).
        schema.ensure(self.db_path)
        conn = self.connect()
        highest = conn.execute("SELECT MAX(id) FROM photos").fetchone()[0]
        conn.execute("DELETE FROM photos WHERE id = ?", (highest,))
        new = conn.execute("INSERT INTO photos (path) VALUES ('D:/other.jpg')").lastrowid
        self.assertGreater(new, highest)

    @unittest.skipIf(paths.COLLATE == "BINARY", "paths differ by case only where the filesystem ignores it")
    def test_a_face_spelled_apart_from_its_photo_finds_it(self):
        conn = self.connect()
        conn.execute("INSERT INTO faces (photo_path, box) VALUES ('d:/A.JPG', '[4,4,5,5]')")
        conn.commit()
        schema.ensure(self.db_path)
        self.assertEqual(["D:/a.jpg"], sorted({path for path, _box in self.faces() if path.lower() == "d:/a.jpg"}))
        self.assertEqual(1, self.connect().execute(
            "SELECT COUNT(*) FROM photos WHERE path = 'D:/a.jpg' COLLATE NOCASE").fetchone()[0])

    @unittest.skipIf(paths.key("D:/a.jpg") != paths.key("D:" + chr(92) + "a.jpg"),
                     "both separators name one file only on Windows")
    def test_a_face_spelled_with_the_other_separator_finds_its_photo(self):
        # The stub rows matched by paths.key and the faces by collation, which does not
        # equate the separators: the face found no photo and the library would not open.
        conn = self.connect()
        conn.execute("INSERT INTO faces (photo_path, box) VALUES (?, '[4,4,5,5]')", ("D:" + chr(92) + "a.jpg",))
        conn.commit()
        schema.ensure(self.db_path)
        self.assertEqual(["D:/a.jpg", "D:/a.jpg"], [path for path, _box in self.faces() if "a.jpg" in path])
        self.assertEqual(2, self.connect().execute("SELECT COUNT(*) FROM photos").fetchone()[0])

    def test_a_face_keeps_its_id_and_so_its_crop(self):
        conn = self.connect()
        before = conn.execute("SELECT id, box FROM faces ORDER BY id").fetchall()
        conn.close()
        schema.ensure(self.db_path)
        conn = self.connect()
        self.assertEqual(before, conn.execute("SELECT id, box FROM faces ORDER BY id").fetchall())
        self.assertEqual([(before[0][0],)], conn.execute("SELECT face_id FROM face_crops").fetchall())


class ALibraryWithTwoCopiesOfEachVector(SchemaTestCase):
    """Before migration 5 a photo's CLIP vector was in photos.embedding, and again in
    embedding_cache, which alone said which model made it (#62, #65)."""

    VECTOR = b"\x00\x00\x80\x3f" * 4

    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)
        conn = self.connect()
        conn.execute("CREATE TABLE embedding_cache (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,"
                     " model_name TEXT, pretrained TEXT, preserve_full_frame INTEGER,"
                     " max_aspect_ratio REAL, force_image_size INTEGER, embedding BLOB)")
        conn.execute("UPDATE photos SET embedding = ? WHERE path = 'D:/a.jpg'", (self.VECTOR,))
        conn.execute("INSERT INTO photos (path, mtime, size, tags, people, embedding)"
                     " VALUES ('D:/b.jpg', 2.0, 2, '[]', '[]', ?)", (self.VECTOR,))
        # a.jpg's cache row fell behind its row's stamp at a keyword write; gone.jpg left
        # the library.
        for path in ("D:/a.jpg", "D:/gone.jpg"):
            conn.execute("INSERT INTO embedding_cache VALUES (?, 0.5, 9, 'ViT-T', 'tiny', 1, 1.4, 512, ?)",
                         (path, self.VECTOR))
        conn.commit()
        schema.ensure(self.db_path)

    def test_a_vector_keeps_its_model_and_the_stamp_its_row_kept(self):
        model = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
        self.assertEqual([("D:/a.jpg", model, 1.0, 1, self.VECTOR)], self.connect().execute(
            "SELECT p.path, e.model, e.mtime, e.size, e.vector FROM embeddings e"
            " JOIN photos p ON p.id = e.photo_id").fetchall())

    def test_the_old_stores_go(self):
        conn = self.connect()
        self.assertNotIn("embedding_cache", tables(conn))
        self.assertNotIn("embedding", columns(conn, "photos"))

    def test_deleting_a_photo_takes_its_vectors_on_any_connection(self):
        conn = self.connect()
        conn.execute("DELETE FROM photos WHERE path = 'D:/a.jpg'")
        conn.commit()
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


class ALibraryWithAListOfPeoplePerPhoto(SchemaTestCase):
    """Before migration 6 a photo's people were a JSON list on its row, patched by
    whichever action changed them (#63)."""

    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)
        conn = self.connect()
        # The keyword names Oda; the list, written by hand at some point, also names
        # someone neither the keywords nor the faces do.
        conn.execute("UPDATE photos SET tags = ?, people = ? WHERE path = 'D:/a.jpg'",
                     (json.dumps(["People/Oda Castellane"]), json.dumps(["Oda Castellane", "Nobody Here"])))
        conn.commit()
        schema.ensure(self.db_path)

    def test_each_photo_lists_whom_its_keywords_and_faces_name(self):
        self.assertEqual([("Oda Castellane", "keyword"), ("Wren Halloway", "face")], self.connect().execute(
            "SELECT name, source FROM photo_people ORDER BY position").fetchall())

    def test_the_list_goes(self):
        self.assertNotIn("people", columns(self.connect(), "photos"))


class ALibraryWithItsSuggestionsInAFile(SchemaTestCase):
    """Before migration 7 what Suggest offered was kept in a JSON file beside the
    library, keyed by path (#64)."""

    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)
        entry = {"tags": [{"tag": "Activity/Rowing", "score": 0.8}], "people": [], "title": "Rowing",
                 "raw_suggestions": {"path": "D:/a.jpg", "suggested_tags": []}, "raw_before_consensus": True}
        saved = {"D:/": {"status": "completed", "completed": 2, "total": 2,
                         "suggestions": {"D:/a.jpg": entry, "D:/gone.jpg": dict(entry, title="Gone")}}}
        self.file = os.path.join(self.dir, "gui_suggestions_cache_library.json")
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        schema.ensure(self.db_path)

    def test_the_photos_the_library_has_keep_theirs(self):
        self.assertEqual([("D:/a.jpg", "Rowing", 1)], self.connect().execute(
            "SELECT p.path, s.title, s.before_consensus FROM suggestions s JOIN photos p ON p.id = s.photo_id"
        ).fetchall())

    def test_the_file_is_left_where_it_was(self):
        self.assertTrue(os.path.exists(self.file))


class APhotoSavedUnderTwoFolders(SchemaTestCase):
    def test_keeps_its_success_over_an_error(self):
        # The last entry won, an error over a success (#96).
        make_unmigrated_library(self.db_path)
        good = {"tags": [], "people": [], "title": "Rowing", "raw_suggestions": {"suggested_tags": []}}
        failed = dict(good, title=None, error="timed out")
        with open(os.path.join(self.dir, "gui_suggestions_cache_library.json"), "w", encoding="utf-8") as f:
            json.dump({"D:/": {"suggestions": {"D:/a.jpg": good}},
                       "D:/b": {"suggestions": {"D:/a.jpg": failed}}}, f)
        schema.ensure(self.db_path)
        self.assertEqual([("Rowing", None)], self.connect().execute("SELECT title, error FROM suggestions").fetchall())


class ALibraryWhoseDatesAreInTheMetadata(SchemaTestCase):
    """Before migration 8 when a photo was taken was only in its raw metadata, parsed by
    every reader (#67)."""

    def setUp(self):
        super().setUp()
        make_unmigrated_library(self.db_path)
        conn = self.connect()
        conn.execute("UPDATE photos SET raw_metadata = ? WHERE path = 'D:/a.jpg'",
                     (json.dumps({"EXIF:DateTimeOriginal": "2019:05:04 10:00:00"}),))
        conn.execute("INSERT INTO photos (path, tags, people, raw_metadata) VALUES ('D:/2016 Regatta/b.jpg', '[]', '[]', '{}')")
        conn.commit()
        schema.ensure(self.db_path)

    def test_each_photo_says_when_it_was_taken(self):
        self.assertEqual([("D:/a.jpg", "2019:05:04 10:00:00", 2019), ("D:/2016 Regatta/b.jpg", None, 2016)],
                         self.connect().execute("SELECT path, taken, year FROM photos ORDER BY id").fetchall())


class ThePhotoIdMigration(SchemaTestCase):
    def test_refuses_a_connection_with_foreign_keys_on(self):
        # Dropping the tables to rebuild them would delete every face and crop first (#83).
        make_unmigrated_library(self.db_path)
        conn = db.connect(self.db_path, foreign_keys=True)
        self.addCleanup(conn.close)
        with self.assertRaises(RuntimeError):
            schema._photo_ids(conn)
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])


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

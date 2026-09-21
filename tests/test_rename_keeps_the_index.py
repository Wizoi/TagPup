"""Renaming a photo takes its index row with it.

Saving a single photo has always moved its row. The folder-wide Smart Rename did not:
it renamed on disk, cleared the in-memory folder cache, and left every row naming a
file that no longer existed. The photo then looked unindexed while its row looked
dead, and both halves were wrong.

The row is the valuable half. It carries the photo's embedding and its faces, names
included. One such rename in this library stranded 78 rows holding 234 faces, 88 of
them named by hand. That work survived only because the renamer records where each
file came from, so the rows could be matched back afterwards -- see
scripts/relink_renamed_photos.py, which exists because of this bug.
"""
import json
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import tagpup_server

OLD = "D:/Library/2020/2Z6A5820.jpg"
NEW = "D:/Library/2020/Meet - 01.jpg"


class RenameCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)

        conn = tagpup_db.connect(self.db_path)
        conn.execute("""CREATE TABLE photos (
            path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT, people TEXT,
            captions TEXT, raw_metadata TEXT, embedding BLOB
        )""")
        conn.execute("""CREATE TABLE faces (
            id INTEGER PRIMARY KEY, photo_path TEXT, name TEXT, embedding BLOB
        )""")
        conn.execute(
            "INSERT INTO photos (path, tags, people, raw_metadata, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (tagpup_server.to_db_path(OLD), json.dumps(["Cross Country"]),
             json.dumps([]), json.dumps({}), b"an-embedding"),
        )
        conn.executemany(
            "INSERT INTO faces (photo_path, name) VALUES (?, ?)",
            [(tagpup_server.to_db_path(OLD), "Rowan Thackeray"),
             (tagpup_server.to_db_path(OLD), None)],
        )
        conn.commit()
        conn.close()

        def cleanup():
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def move(self, mapping):
        """The index half of the rename handler, as it now runs."""
        def move_rows(conn):
            cursor = conn.cursor()
            for old_path, new_path in mapping.items():
                cursor.execute("UPDATE photos SET path = ? WHERE path = ?",
                               (tagpup_server.to_db_path(new_path),
                                tagpup_server.to_db_path(old_path)))
                cursor.execute("UPDATE faces SET photo_path = ? WHERE photo_path = ?",
                               (tagpup_server.to_db_path(new_path),
                                tagpup_server.to_db_path(old_path)))
            return len(mapping)
        return tagpup_db.write_with_connection(self.db_path, move_rows)

    def photos(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return [r[0] for r in conn.execute("SELECT path FROM photos")]
        finally:
            conn.close()

    def faces_for(self, path):
        conn = tagpup_db.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM faces WHERE photo_path = ?",
                (tagpup_server.to_db_path(path),)).fetchone()[0]
        finally:
            conn.close()


class TestTheRowFollowsTheFile(RenameCase):
    def test_the_photo_row_names_the_new_file(self):
        self.move({OLD: NEW})
        self.assertEqual(self.photos(), [tagpup_server.to_db_path(NEW)])

    def test_the_faces_come_with_it(self):
        self.move({OLD: NEW})
        self.assertEqual(self.faces_for(NEW), 2)
        self.assertEqual(self.faces_for(OLD), 0)

    def test_a_named_face_keeps_its_name(self):
        # The expensive part: names assigned by hand, which a delete-and-reindex loses.
        self.move({OLD: NEW})
        conn = tagpup_db.connect(self.db_path)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM faces WHERE photo_path = ? AND name IS NOT NULL",
                (tagpup_server.to_db_path(NEW),))]
        finally:
            conn.close()
        self.assertEqual(names, ["Rowan Thackeray"])

    def test_the_embedding_is_not_disturbed(self):
        self.move({OLD: NEW})
        conn = tagpup_db.connect(self.db_path)
        try:
            emb = conn.execute("SELECT embedding FROM photos").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(emb, b"an-embedding")

    def test_a_photo_that_did_not_move_is_left_alone(self):
        self.move({})
        self.assertEqual(self.photos(), [tagpup_server.to_db_path(OLD)])

    def test_renaming_a_photo_the_index_never_saw_is_not_an_error(self):
        self.move({"D:/Library/2020/never-indexed.jpg": "D:/Library/2020/x.jpg"})
        self.assertEqual(self.photos(), [tagpup_server.to_db_path(OLD)])


class TestTheHandlerActuallyDoesIt(unittest.TestCase):
    """The guard. The rename handler renamed files and cleared a cache; that it also
    had to move the rows was not obvious from reading it, and will not be next time."""

    def test_the_rename_handler_moves_photo_and_face_rows(self):
        source = os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py")
        with open(source, encoding="utf-8") as f:
            text = f.read()

        start = text.index("def handle_post_folder_rename_photos")
        end = text.index("\n    def ", start + 10)
        body = text[start:end]

        self.assertIn("UPDATE photos SET path", body,
                      "renaming no longer moves the photo's index row")
        self.assertIn("UPDATE faces SET photo_path", body,
                      "renaming no longer moves the photo's faces")


if __name__ == "__main__":
    unittest.main()

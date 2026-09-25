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

Then the move itself looked rows up with forward slashes while the index holds native
paths, so on Windows it matched nothing and reported success. The rows here are seeded
the way the indexer writes them (os.path.abspath), never through the code under test.
The mover is tagpup.store.photos.move_rows.
"""
import inspect
import json
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import schema  # noqa: E402
from tagpup.store.photos import move_rows  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import FACES_WITH_PATHS, VECTORS_WITH_PATHS, add_face, add_vector  # noqa: E402

OLD = "D:/Library/2020/2Z6A5820.jpg"
NEW = "D:/Library/2020/Meet - 01.jpg"


def native(path):
    """What the indexer writes: os.path.abspath, backslashes on Windows."""
    return os.path.abspath(path)


class RenameCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)

        schema.ensure(self.db_path)
        self.seed(OLD, b"an-embedding", ["Rowan Thackeray", None])

        def cleanup():
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def seed(self, path, embedding, face_names):
        conn = tagpup_db.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO photos (path, tags, raw_metadata) "
                "VALUES (?, ?, ?)",
                (native(path), json.dumps(["Cross Country"]), json.dumps({})),
            )
            add_vector(conn, native(path), embedding)
            for name in face_names:
                add_face(conn, native(path), name=name)
            conn.commit()
        finally:
            conn.close()

    def move(self, mapping):
        return move_rows(self.db_path, mapping)

    def photos(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return sorted(r[0] for r in conn.execute("SELECT path FROM photos"))
        finally:
            conn.close()

    def embedding_at(self, path):
        conn = tagpup_db.connect(self.db_path)
        try:
            row = conn.execute("SELECT e.vector FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?",
                               (native(path),)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def face_names_at(self, path):
        conn = tagpup_db.connect(self.db_path)
        try:
            return sorted((r[0] or "") for r in conn.execute(
                "SELECT f.name FROM " + FACES_WITH_PATHS + " WHERE p.path = ?", (native(path),)))
        finally:
            conn.close()


class TestTheRowFollowsTheFile(RenameCase):
    def test_the_photo_row_names_the_new_file(self):
        self.move({OLD: NEW})
        self.assertEqual(self.photos(), [native(NEW)])

    def test_the_faces_come_with_it(self):
        self.move({OLD: NEW})
        self.assertEqual(len(self.face_names_at(NEW)), 2)
        self.assertEqual(self.face_names_at(OLD), [])

    def test_a_named_face_keeps_its_name(self):
        # The expensive part: names assigned by hand, which a delete-and-reindex loses.
        self.move({OLD: NEW})
        self.assertEqual(self.face_names_at(NEW), ["", "Rowan Thackeray"])

    def test_the_embedding_is_not_disturbed(self):
        self.move({OLD: NEW})
        self.assertEqual(self.embedding_at(NEW), b"an-embedding")

    def test_a_photo_that_did_not_move_is_left_alone(self):
        self.move({})
        self.assertEqual(self.photos(), [native(OLD)])

    def test_renaming_a_photo_the_index_never_saw_is_not_an_error(self):
        moved, skipped = self.move({"D:/Library/2020/never-indexed.jpg": "D:/Library/2020/x.jpg"})
        self.assertEqual((moved, skipped), (0, []))
        self.assertEqual(self.photos(), [native(OLD)])

    def test_it_reports_the_rows_it_moved_not_the_renames_it_was_given(self):
        moved, _ = self.move({OLD: NEW, "D:/Library/2020/never-indexed.jpg": "D:/Library/2020/x.jpg"})
        self.assertEqual(moved, 1)

    @unittest.skipUnless(os.name == "nt", "separators and case only differ on Windows")
    def test_any_spelling_of_the_old_name_finds_the_native_row(self):
        self.assertIn("\\", native(OLD))
        moved, _ = self.move({"d:\\library/2020/2z6a5820.JPG": NEW})
        self.assertEqual(moved, 1)
        self.assertEqual(self.photos(), [native(NEW)])
        self.assertEqual(len(self.face_names_at(NEW)), 2)


class TestADestinationThatIsTaken(RenameCase):
    """Re-pointing rows at a path that already had rows once made 233 duplicate faces."""

    def test_an_occupied_destination_is_reported_not_merged_into(self):
        self.seed(NEW, b"someone-else", ["Ada Marchetti"])
        moved, skipped = self.move({OLD: NEW})
        self.assertEqual(moved, 0)
        self.assertEqual(skipped, [(OLD, NEW)])
        self.assertEqual(self.face_names_at(NEW), ["Ada Marchetti"])
        self.assertEqual(self.face_names_at(OLD), ["", "Rowan Thackeray"])
        self.assertEqual(self.embedding_at(OLD), b"an-embedding")

    def test_renumbering_a_run_moves_every_row_to_its_own_file(self):
        # Smart Rename shifting indices: -01 becomes -02 while the old -02 becomes
        # -03. Done one row at a time, the first update hit the second row's primary
        # key and the whole transaction rolled back, so nothing moved at all.
        first, second, third = ("D:/Library/Run - 1.jpg", "D:/Library/Run - 2.jpg",
                                "D:/Library/Run - 3.jpg")
        self.seed(first, b"first", ["Rowan Thackeray"])
        self.seed(second, b"second", ["Ada Marchetti", "Tobias Wren"])
        moved, skipped = self.move({first: second, second: third})
        self.assertEqual((moved, skipped), (2, []))
        self.assertEqual(self.embedding_at(second), b"first")
        self.assertEqual(self.embedding_at(third), b"second")
        self.assertIsNone(self.embedding_at(first))
        self.assertEqual(self.face_names_at(second), ["Rowan Thackeray"])
        self.assertEqual(self.face_names_at(third), ["Ada Marchetti", "Tobias Wren"])

    def test_swapping_two_names_swaps_their_rows(self):
        a, b = "D:/Library/Run - 1.jpg", "D:/Library/Run - 2.jpg"
        self.seed(a, b"a", ["Rowan Thackeray"])
        self.seed(b, b"b", ["Ada Marchetti"])
        moved, skipped = self.move({a: b, b: a})
        self.assertEqual((moved, skipped), (2, []))
        self.assertEqual(self.embedding_at(a), b"b")
        self.assertEqual(self.face_names_at(a), ["Ada Marchetti"])
        self.assertEqual(self.face_names_at(b), ["Rowan Thackeray"])

    def test_a_name_moved_aside_in_the_same_call_is_free(self):
        # The file already on the target name is renamed to _conflict_1 first; its row
        # goes with it, and the renamed photo can then take the name.
        target, aside = "D:/Library/Run - 1.jpg", "D:/Library/Run - 1_conflict_1.jpg"
        self.seed(target, b"occupant", ["Ada Marchetti"])
        moved, skipped = self.move({target: aside, OLD: target})
        self.assertEqual((moved, skipped), (2, []))
        self.assertEqual(self.embedding_at(aside), b"occupant")
        self.assertEqual(self.embedding_at(target), b"an-embedding")
        self.assertEqual(self.face_names_at(target), ["", "Rowan Thackeray"])

    def test_a_case_only_rename_is_not_blocked_by_itself(self):
        moved, skipped = self.move({OLD: OLD.lower()})
        self.assertEqual((moved, skipped), (1, []))


class TestTheRoutesActuallyDoIt(unittest.TestCase):
    """The guard. The rename handler renamed files and cleared a cache; that it also
    had to move the rows was not obvious from reading it, and will not be next time."""

    def test_the_rename_route_moves_photo_and_face_rows(self):
        # Through the service, which moves them.
        from tagpup.services import photos
        from tagpup.web import tagpup_routes

        from tagpup.services import file_changes

        self.assertIn("smart_rename(", inspect.getsource(tagpup_routes.folder_rename_photos))
        # As a change of photo files, whose renames are marked done in the transaction
        # that moves their rows (phase 7.5).
        self.assertIn("file_changes.rename(", inspect.getsource(photos.smart_rename),
                      "renaming no longer goes through the journal of photo files")
        self.assertIn("photos.move_rows_in(", inspect.getsource(file_changes._record_renames),
                      "renaming no longer moves the photo's index rows")

    def test_saving_a_renamed_photo_moves_its_rows(self):
        # Through the service, which moves them.
        from tagpup.services import tagging
        from tagpup.web import tagpup_routes

        self.assertIn("save_photo(", inspect.getsource(tagpup_routes.photo_save_metadata))
        self.assertIn("photos.move_rows(", inspect.getsource(tagging.save_photo),
                      "a caption rename no longer moves the photo's index rows")


if __name__ == "__main__":
    unittest.main()

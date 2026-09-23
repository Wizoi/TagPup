"""scripts/paths.py: the two spellings a photo path has, and nothing else."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import paths  # noqa: E402

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class StoredIsWhatTheIndexerWrites(unittest.TestCase):
    def test_forward_slashes_become_native(self):
        self.assertEqual(paths.stored("D:/Pictures/2026/Run/a.jpg"), r"D:\Pictures\2026\Run\a.jpg")

    def test_mixed_separators_become_one(self):
        # What the indexer stored when handed a folder typed with forward slashes.
        self.assertEqual(paths.stored("D:/Pictures/Meets/2025-11 Classic\\01.jpg"),
                         r"D:\Pictures\Meets\2025-11 Classic\01.jpg")

    def test_native_is_unchanged(self):
        native = r"D:\Pictures\2026\Run\a.jpg"
        self.assertEqual(paths.stored(native), native)

    def test_unc_share_is_kept(self):
        self.assertEqual(paths.stored("//fileserver/Pictures/2010/a.JPG"),
                         r"\\fileserver\Pictures\2010\a.JPG")

    def test_dot_segments_collapse(self):
        self.assertEqual(paths.stored(r"D:\Pictures\x\..\Run\.\a.jpg"), r"D:\Pictures\Run\a.jpg")

    def test_case_is_preserved(self):
        # Stored is for writing and opening, so it keeps the name as the disk has it.
        self.assertEqual(paths.stored(r"D:\Pictures\Run\IMG_01.JPG"), r"D:\Pictures\Run\IMG_01.JPG")


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class KeyComparesAsTheFilesystemDoes(unittest.TestCase):
    def test_every_spelling_of_one_file_is_one_key(self):
        spellings = [
            r"D:\Pictures\Run\a.jpg",
            "D:/Pictures/Run/a.jpg",
            "d:/pictures/run/A.JPG",
            "D:/Pictures/Run\\a.jpg",
            r"D:\Pictures\x\..\Run\a.jpg",
        ]
        self.assertEqual(len({paths.key(s) for s in spellings}), 1)
        self.assertTrue(all(paths.same(spellings[0], s) for s in spellings))

    def test_different_files_differ(self):
        self.assertFalse(paths.same(r"D:\Pictures\Run\a.jpg", r"D:\Pictures\Run\b.jpg"))

    def test_nothing_is_not_the_same_as_nothing(self):
        self.assertFalse(paths.same("", ""))
        self.assertFalse(paths.same(None, r"D:\a.jpg"))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class FolderMembership(unittest.TestCase):
    def test_inside_at_any_depth_and_any_spelling(self):
        self.assertTrue(paths.is_under("d:/pictures/run/sub/a.jpg", r"D:\Pictures\Run"))

    def test_a_sibling_sharing_a_prefix_is_not_inside(self):
        self.assertFalse(paths.is_under(r"D:\Pictures\Run 2\a.jpg", r"D:\Pictures\Run"))

    def test_a_sibling_share_is_not_inside_a_share_root(self):
        # os.path.join(r"\\nas\photos", "") adds no separator after a share root.
        self.assertTrue(paths.is_under(r"\\nas\photos\a.jpg", r"\\nas\photos"))
        self.assertFalse(paths.is_under(r"\\nas\photos2\a.jpg", r"\\nas\photos"))

    def test_a_bare_drive_means_the_drive(self):
        # The drive the process is running on is the one abspath resolves to its
        # working directory; other drives happen to resolve to their root.
        drive = os.getcwd()[:2]
        self.assertEqual(paths.stored(drive), drive + "\\")
        self.assertTrue(paths.is_under(drive + r"\Pictures\a.jpg", drive))

    def test_a_folder_is_not_inside_itself(self):
        self.assertFalse(paths.is_under(r"D:\Pictures\Run", r"D:\Pictures\Run"))


def _table(rows):
    import db
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE photos (path TEXT)")
    conn.execute("CREATE INDEX idx_photos_path ON photos(path COLLATE %s)" % paths.COLLATE)
    conn.executemany("INSERT INTO photos VALUES (?)", [(r,) for r in rows])
    return conn


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class SqlEqualsMatchesStoredRows(unittest.TestCase):
    ROWS = [r"D:\Pictures\Run\IMG_0001.jpg", r"D:\Pictures\Run\IMGX0001.jpg"]

    def found(self, path):
        conn = _table(self.ROWS)
        clause, params = paths.sql_equals("path", path)
        try:
            return [r for (r,) in conn.execute("SELECT path FROM photos WHERE " + clause, params)]
        finally:
            conn.close()

    def test_any_spelling_finds_the_stored_row(self):
        for spelling in [r"D:\Pictures\Run\IMG_0001.jpg", "d:/pictures/run/img_0001.JPG"]:
            self.assertEqual(self.found(spelling), [r"D:\Pictures\Run\IMG_0001.jpg"])

    def test_underscore_is_not_a_wildcard(self):
        # `photo_path LIKE ?` matched both rows here.
        self.assertEqual(self.found(r"D:\Pictures\Run\IMG_0001.jpg"), [r"D:\Pictures\Run\IMG_0001.jpg"])

    def test_uses_the_path_index(self):
        conn = _table(self.ROWS)
        clause, params = paths.sql_equals("path", self.ROWS[0])
        plan = " ".join(str(row) for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT path FROM photos WHERE " + clause, params))
        conn.close()
        self.assertIn("idx_photos_path", plan)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class SqlUnderMatchesStoredRows(unittest.TestCase):
    def rows_under(self, folder, rows):
        conn = _table(rows)
        clause, params = paths.sql_under("path", folder)
        try:
            return [r for (r,) in conn.execute(
                "SELECT path FROM photos WHERE " + clause + " ORDER BY path", params)]
        finally:
            conn.close()

    def test_a_sibling_share_is_not_found_under_a_share_root(self):
        rows = [r"\\nas\photos\a.jpg", r"\\nas\photos2\b.jpg", r"\\nas\photosArchive\c.jpg"]
        self.assertEqual(self.rows_under(r"\\nas\photos", rows), [r"\\nas\photos\a.jpg"])

    def test_folder_typed_in_another_case_finds_the_rows(self):
        self.assertEqual(self.rows_under("d:/pictures/run", [r"D:\Pictures\Run\a.jpg"]),
                         [r"D:\Pictures\Run\a.jpg"])

    def test_finds_native_rows_from_a_forward_slash_folder(self):
        rows = [r"D:\Pictures\Run\a.jpg", r"D:\Pictures\Run\sub\b.jpg", r"D:\Pictures\Other\c.jpg"]
        self.assertEqual(self.rows_under("D:/Pictures/Run", rows),
                         [r"D:\Pictures\Run\a.jpg", r"D:\Pictures\Run\sub\b.jpg"])

    def test_underscore_in_a_folder_name_is_literal(self):
        rows = [r"D:\Pictures\meet_1\a.jpg", r"D:\Pictures\meetX1\a.jpg"]
        self.assertEqual(self.rows_under(r"D:\Pictures\meet_1", rows), [r"D:\Pictures\meet_1\a.jpg"])

    def test_percent_in_a_folder_name_is_literal(self):
        rows = [r"D:\Pictures\100% fun\a.jpg", r"D:\Pictures\100 more fun\a.jpg"]
        self.assertEqual(self.rows_under(r"D:\Pictures\100% fun", rows), [r"D:\Pictures\100% fun\a.jpg"])

    def test_a_sibling_sharing_a_prefix_is_not_found(self):
        rows = [r"D:\Pictures\Run\a.jpg", r"D:\Pictures\Run 2\a.jpg"]
        self.assertEqual(self.rows_under(r"D:\Pictures\Run", rows), [r"D:\Pictures\Run\a.jpg"])


class EmptyInEmptyOut(unittest.TestCase):
    def test_empty(self):
        for fn in (paths.stored, paths.key):
            self.assertEqual(fn(""), "")
            self.assertEqual(fn(None), "")


if __name__ == "__main__":
    unittest.main()

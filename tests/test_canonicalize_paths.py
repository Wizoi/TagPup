"""scripts/canonicalize_paths.py on a library holding every kind of bad spelling seen in a real one."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import canonicalize_paths  # noqa: E402
import db  # noqa: E402

FOLDER = r"D:\Pictures\Meets\2025-11 Classic"
EMB = b"\x00" * 16


@unittest.skipUnless(os.name == "nt", "the bad spellings are Windows ones")
class CanonicalizePaths(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="canon_")
        self.db = os.path.join(self.dir, "lib.db")
        conn = db.connect(self.db)
        conn.executescript("""
            CREATE TABLE photos (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT,
                people TEXT, captions TEXT, raw_metadata TEXT, embedding BLOB, document_id TEXT);
            CREATE TABLE faces (id INTEGER PRIMARY KEY AUTOINCREMENT, photo_path TEXT, box TEXT,
                embedding BLOB, name TEXT, crop_image BLOB, prob REAL, name_source TEXT,
                excluded INTEGER DEFAULT 0, excluded_reason TEXT);
            CREATE TABLE embedding_cache (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,
                model_name TEXT, pretrained TEXT, preserve_full_frame INTEGER,
                max_aspect_ratio REAL, force_image_size INTEGER, embedding BLOB);
        """)

        def photo(path, tags, emb=EMB, mtime=1.0):
            conn.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?,?)",
                         (path, mtime, 10, json.dumps(tags), "[]", "[]", "{}", emb, None))

        def face(path, box, name=None, source=None):
            conn.execute("INSERT INTO faces (photo_path, box, embedding, name, name_source)"
                         " VALUES (?,?,?,?,?)", (path, box, EMB, name, source))

        # 1. Mixed separators: the indexer handed a forward-slash folder.
        self.mixed = "D:/Pictures/Meets/2025-11 Classic\\01.jpg"
        photo(self.mixed, ["Activity/Running"])
        face(self.mixed, "[1,2,3,4]", "Rowan Thackeray", "manual")

        # 2. Twin: the real row, and a stub a save-with-rename inserted beside it.
        self.real = FOLDER + r"\02.jpg"
        photo(self.real, ["Activity/Running"], mtime=1.0)
        face(self.real, "[5,6,7,8]", "Imogen Vale", "manual")
        photo("D:/Pictures/Meets/2025-11 Classic/02.jpg", ["Activity/Running", "Event/Classic"],
              emb=None, mtime=2.0)

        # 3. Twin where both spellings were indexed: the same face detected twice.
        self.seed = FOLDER + r"\03.jpg"
        photo(self.seed, ["People/Tobin Marsh"])
        face(self.seed, "[9,9,9,9]", "Tobin Marsh", "manual")
        photo("D:/Pictures/Meets/2025-11 Classic\\03.jpg", ["People/Tobin Marsh"])
        face("D:/Pictures/Meets/2025-11 Classic\\03.jpg", "[9,9,9,9]", "Tobin Marsh", "manual")

        # 4. Twin whose two copies of a face disagree about who it is.
        self.disputed = FOLDER + r"\04.jpg"
        photo(self.disputed, [])
        face(self.disputed, "[1,1,1,1]", "Wren Adair", "manual")
        photo("D:/Pictures/Meets/2025-11 Classic/04.jpg", [])
        face("D:/Pictures/Meets/2025-11 Classic/04.jpg", "[1,1,1,1]", "Quinn Adair", "manual")

        # 5. Already right: must be left exactly as it is.
        self.good = FOLDER + r"\05.jpg"
        photo(self.good, ["Activity/Running"])
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_script(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            canonicalize_paths.main(["--db", self.db, *extra])
        return out.getvalue()

    def rows(self, sql, *params):
        conn = db.connect("file:%s?mode=ro" % self.db.replace("\\", "/"), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_dry_run_changes_nothing(self):
        before = self.rows("SELECT * FROM photos ORDER BY path"), self.rows("SELECT * FROM faces ORDER BY id")
        out = self.run_script()
        self.assertIn("Dry run", out)
        after = self.rows("SELECT * FROM photos ORDER BY path"), self.rows("SELECT * FROM faces ORDER BY id")
        self.assertEqual(before, after)

    def apply(self):
        from unittest import mock
        with mock.patch.object(canonicalize_paths.tagpup_db, "backup", return_value="(skipped in test)"):
            return self.run_script("--apply")

    def test_mixed_separators_are_rewritten_with_their_faces(self):
        self.apply()
        fixed = FOLDER + r"\01.jpg"
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos WHERE path = ?", fixed), [(1,)])
        self.assertEqual(self.rows("SELECT name FROM faces WHERE photo_path = ?", fixed),
                         [("Rowan Thackeray",)])

    def test_stub_twin_merges_into_the_real_row_keeping_the_newer_tags(self):
        self.apply()
        rows = self.rows("SELECT path, tags, embedding IS NOT NULL FROM photos WHERE path LIKE ?",
                         "%02.jpg")
        self.assertEqual(rows, [(self.real, json.dumps(["Activity/Running", "Event/Classic"]), 1)])
        self.assertEqual(self.rows("SELECT name FROM faces WHERE photo_path = ?", self.real),
                         [("Imogen Vale",)])

    def test_a_face_detected_twice_is_kept_once(self):
        self.apply()
        self.assertEqual(self.rows("SELECT path FROM photos WHERE path LIKE ?", "%03.jpg"),
                         [(self.seed,)])
        self.assertEqual(self.rows("SELECT name FROM faces WHERE photo_path LIKE ?", "%03.jpg"),
                         [("Tobin Marsh",)])

    def test_disagreeing_twins_are_left_for_a_person(self):
        out = self.apply()
        self.assertIn("left alone (both have faces): 1", out)
        self.assertEqual(len(self.rows("SELECT path FROM photos WHERE path LIKE ?", "%04.jpg")), 2)
        self.assertEqual(
            sorted(self.rows("SELECT name FROM faces WHERE photo_path LIKE ?", "%04.jpg")),
            [("Quinn Adair",), ("Wren Adair",)])

    def test_rows_already_right_are_untouched_and_nothing_else_is_lost(self):
        self.apply()
        self.assertEqual(self.rows("SELECT tags FROM photos WHERE path = ?", self.good),
                         [(json.dumps(["Activity/Running"]),)])
        # 5 files, one pair left alone on purpose -> 6 rows; no forward slashes left
        # except in the pair a person has to settle.
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos"), [(6,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos WHERE instr(path, '/') > 0"), [(1,)])

    def test_reports_rows_changed_not_rows_planned(self):
        out = self.apply()
        self.assertRegex(out, r"photos renamed\s+1\b")
        self.assertRegex(out, r"duplicate faces removed\s+1\b")
        self.assertRegex(out, r"twins: stub rows removed\s+2\b")
        # A second run finds nothing left to do.
        self.assertRegex(self.run_script(), r"photos re-spelled in place : 0")


if __name__ == "__main__":
    unittest.main()

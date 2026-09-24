"""A library's rules, and the doctor that reports them (tagpup.store.checks, tools/doctor.py).

Each rule is a thing that has actually been wrong in a library: face names missing from
their photo's people (docs/findings.md, #42), faces both named and excluded (#3), rows
for a file under two spellings, a tree node whose parent is gone (#37).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

from tagpup.store import checks, db, schema

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402


class Library(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="doctor_")
        self.db_path = os.path.join(self.dir, "library.db")
        schema.ensure(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def photo(self, name, people=(), tags=(), on_disk=True):
        path = os.path.join(self.dir, name)
        if on_disk:
            with open(path, "wb") as f:
                f.write(b"photo")
        self.conn.execute("INSERT INTO photos (path, mtime, size, tags, people) VALUES (?, 1, 1, ?, ?)",
                          (path, json.dumps(list(tags)), json.dumps(list(people))))
        self.conn.commit()
        return path

    def face(self, photo_path, name=None, excluded=0):
        self.conn.execute("INSERT INTO faces (photo_path, box, name, excluded) VALUES (?, '[0,0,1,1]', ?, ?)",
                          (photo_path, name, excluded))
        self.conn.commit()

    def broken(self):
        return {check.name: check.count for check in checks.run(self.conn) if check.count}


class TheRules(Library):
    def test_a_library_made_new_keeps_them_all(self):
        self.face(self.photo("a.jpg", people=["Wren Halloway"]), name="Wren Halloway")
        self.assertEqual({}, self.broken())

    def test_a_face_name_its_photo_does_not_list(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway")
        self.assertEqual({"face names missing from their photo's people": 1}, self.broken())

    def test_a_face_named_and_excluded(self):
        self.face(self.photo("a.jpg", people=["Wren Halloway"]), name="Wren Halloway", excluded=1)
        self.assertEqual({"faces named and excluded": 1}, self.broken())

    def test_a_face_whose_photo_has_no_row(self):
        self.face(os.path.join(self.dir, "never-indexed.jpg"))
        self.assertEqual({"faces with no photo row": 1}, self.broken())

    def test_one_file_under_two_spellings(self):
        if os.path.normcase("A") == "A":
            self.skipTest("paths compare with case here")
        self.photo("a.jpg")
        self.photo("A.JPG", on_disk=False)
        self.assertEqual({"photos with two rows": 1}, self.broken())

    def test_a_tree_node_whose_parent_is_gone(self):
        self.conn.execute("INSERT INTO tag_taxonomy (tag, name, parent_id) VALUES ('People/Wren Halloway', 'Wren Halloway', 999)")
        self.conn.commit()
        self.assertEqual({"tree nodes whose parent is missing": 1}, self.broken())

    def test_a_library_not_yet_migrated(self):
        self.conn.execute("DELETE FROM schema_version WHERE version = ?", (schema.LATEST,))
        self.conn.commit()
        self.assertEqual({"migrations not applied": 1}, self.broken())

    def test_a_trigger_that_keeps_a_generation_gone(self):
        # Three rows in `generations` say nothing about whether anything moves them (#60).
        self.conn.execute("DROP TRIGGER generation_faces_update")
        self.conn.commit()
        self.assertEqual({"generations not kept": 1}, self.broken())

    def test_the_counters_an_older_version_made_again(self):
        self.conn.execute("CREATE TABLE faces_generation (id INTEGER PRIMARY KEY, generation INTEGER)")
        self.conn.commit()
        self.assertEqual({"generations not kept": 1}, self.broken())

    def test_missing_files_are_counted_by_folder_not_as_broken(self):
        self.photo("gone.jpg", on_disk=False)
        self.assertEqual({}, self.broken())
        self.assertEqual([(self.dir, 1, True)], checks.missing_files(self.conn))


class TheDoctor(Library):
    def run_doctor(self, *args):
        lines = []
        code = doctor.report(self.db_path, *args, out=lines.append)
        return code, lines

    def test_says_nothing_is_broken_when_nothing_is(self):
        self.face(self.photo("a.jpg", people=["Wren Halloway"]), name="Wren Halloway")
        broken, lines = self.run_doctor()
        self.assertEqual(0, broken)
        self.assertEqual(0, doctor.main(["--db", self.db_path]))

    def test_counts_without_naming_anyone_unless_asked(self):
        self.face(self.photo("Wren Halloway at the lake.jpg"), name="Wren Halloway")
        broken, lines = self.run_doctor()
        self.assertEqual(1, broken)
        self.assertNotIn("Wren", "\n".join(lines))
        _broken, shown = self.run_doctor(5)
        self.assertIn("Wren Halloway at the lake.jpg", "\n".join(shown))
        self.assertEqual(1, doctor.main(["--db", self.db_path]))

    def test_changes_nothing(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway")
        self.conn.close()

        def digest():
            with open(self.db_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        before = digest()
        self.run_doctor(5)
        self.assertEqual(before, digest())
        self.assertFalse(os.path.exists(self.db_path + "-wal") and os.path.getsize(self.db_path + "-wal"))


if __name__ == "__main__":
    unittest.main()

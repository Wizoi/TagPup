"""A library's rules, and the doctor that reports them (tagpup.store.checks, tools/doctor.py).

Each rule is a thing that has actually been wrong in a library: a photo's people not
what its keywords, faces and tree make them (docs/findings.md, #42, #63), faces both
named and excluded (#3), rows for a file under two spellings, a tree node whose parent
is gone (#37), a trigger that keeps a generation gone (#60). The derived tables
(photo_tags, folders, photo_folder, the rows that belong to a photo) are held to what the photos and the
tag tree give, and `--rebuild-derived --apply` makes them so and writes nothing else.

Lifted: test_derived_checks and test_doctor_sees_the_derived_tables (each rule is one scenario here: a writer
forgot, the doctor counts it, the repair settles it). Break that fails the kept test: checks.photo_tags_out_of_date
returns [] -> test_a_writer_that_changed_a_photos_keywords_and_did_not_refresh fails. Deleted with no scenario, since
no library below migration 4 exists (the three live ones are at 32, the baseline, and one below it is refused): the doctor
reading a library "from before photo ids" (#78); the not-yet-migrated arm stays in the doctor's test that says it (a
step ahead of the library, migration_names.ahead), not in one that deleted the version row, which no library has.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tagpup.store import checks, db, derived, people, roots_check, schema, taxonomy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face, add_vector  # noqa: E402


class Library(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="doctor_")
        self.db_path = os.path.join(self.dir, "library.db")
        schema.ensure(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def photo(self, name, tags=(), on_disk=True):
        path = os.path.join(self.dir, name)
        if on_disk:
            with open(path, "wb") as f:
                f.write(b"photo")
        added = self.conn.execute("INSERT INTO photos (path, mtime, size, tags) VALUES (?, 1, 1, ?)",
                                  (path, json.dumps(list(tags))))
        derived.make_nodes(self.conn, [list(tags)])   # the nodes of its keywords, as the index makes them when it reads the file
        people.rebuild_photos(self.conn, [path])
        derived.refresh_photos(self.conn, [added.lastrowid])   # a library at rest: its derived tables are right
        self.conn.commit()
        return path

    def face(self, photo_path, name=None, excluded=0, rebuilt=True):
        """A face, and the photo's people rebuilt as the store does -- or, without
        `rebuilt`, left as they were: a writer that named a face by hand."""
        add_face(self.conn, photo_path, box="[0,0,1,1]", name=name, excluded=excluded)
        if rebuilt:
            people.rebuild_photos(self.conn, [photo_path])
        self.conn.commit()

    def broken(self):
        return {check.name: check.count for check in checks.run(self.conn) if check.count}


class TheRules(Library):
    def test_a_library_made_new_keeps_them_all(self):
        self.face(self.photo("a.jpg", tags=["People/Ansel Ditmore"]), name="Wren Halloway")
        self.assertEqual({}, self.broken())

    def test_a_face_name_its_photo_does_not_list(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway", rebuilt=False)
        self.assertEqual({"photos whose people are out of date": 1}, self.broken())

    def test_people_written_by_hand(self):
        # Rows the rule would not make: someone no keyword or face names, a person the
        # rule lists missing, and the right names in the wrong order.
        taxonomy.add_path(self.conn, "People/Somebody Else", root_has_face=1)
        for name, broken in (("a.jpg", "INSERT INTO photo_people (photo_id, position, name, source, tag_id)"
                                       " SELECT id, 9, 'Somebody Else', 'keyword',"
                                       " (SELECT id FROM tag_taxonomy WHERE tag = 'People/Somebody Else') FROM photos WHERE path = ?"),
                             ("b.jpg", "DELETE FROM photo_people WHERE name = 'Wren Halloway'"
                                       " AND photo_id = (SELECT id FROM photos WHERE path = ?)"),
                             ("c.jpg", "UPDATE photo_people SET name = CASE name WHEN 'Wren Halloway'"
                                       " THEN 'Ansel Ditmore' ELSE 'Wren Halloway' END"
                                       " WHERE photo_id = (SELECT id FROM photos WHERE path = ?)")):
            photo = self.photo(name, tags=["People/Ansel Ditmore"])
            self.face(photo, name="Wren Halloway")
            self.conn.execute(broken, (photo,))
            self.conn.commit()
        # The people are words of the word index, kept by people.rebuild: two of the three hand-made lists hold a name the
        # index does not (the third holds the same names in another order); the third carries two names that are not their
        # node's, since the hand-made rows hold a name and the id of another.
        self.assertEqual({"photos whose people are out of date": 3, "photos whose word index rows are out of date": 2,
                          "people listed whose name is not their person's": 2}, self.broken())

    def test_a_face_named_and_excluded(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway", excluded=1)
        self.assertEqual({"faces named and excluded": 1}, self.broken())

    def test_a_face_whose_photo_has_no_row(self):
        # A face points at its photo by id; the row goes on a connection without
        # foreign keys, by something other than the store's own deletes.
        photo = self.photo("deleted-by-hand.jpg")
        self.face(photo)
        self.conn.execute("DELETE FROM photos WHERE path = ?", (photo,))
        self.conn.commit()
        self.assertEqual({"faces with no photo row": 1}, self.broken())

    def test_rows_that_belong_to_a_photo_that_is_gone(self):
        # Written past the store, or on a copy restored without its triggers.
        gone = 999
        self.conn.execute("INSERT INTO embeddings (photo_id, model, vector) VALUES (?, 'm', x'00')", (gone,))
        oda = taxonomy.add_path(self.conn, "People/Oda Castellane", root_has_face=1)
        self.conn.execute("INSERT INTO photo_people (photo_id, position, name, source, tag_id)"
                          " VALUES (?, 0, 'Oda Castellane', 'keyword', ?)", (gone, oda))
        self.conn.execute("INSERT INTO suggestions (photo_id, tags) VALUES (?, '[]')", (gone,))
        self.assertEqual({"vectors whose photo is gone": 1, "people listed for a photo that is gone": 1,
                          "suggestions for a photo that is gone": 1}, self.broken())

    def test_photos_without_a_vector_for_the_model_are_counted_not_broken(self):
        for name in ("a.jpg", "b.jpg"):
            self.photo(name)
        add_vector(self.conn, os.path.join(self.dir, "a.jpg"), b"\x00" * 16, model="m")
        self.assertEqual({}, self.broken())
        self.assertEqual((1, 2), (checks.without_a_vector(self.conn, "m"), checks.without_a_vector(self.conn, "another model")))

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

    def test_a_trigger_that_keeps_a_generation_gone(self):
        # Three rows in `generations` say nothing about whether anything moves them (#60).
        self.conn.execute("DROP TRIGGER generation_faces_update")
        self.conn.commit()
        self.assertEqual({"generations not kept": 1}, self.broken())

    def test_missing_files_are_counted_by_folder_not_as_broken(self):
        self.photo("gone.jpg", on_disk=False)
        self.assertEqual({}, self.broken())
        said = []
        self.assertEqual(0, doctor.report(self.db_path, 1, out=said.append))
        self.assertIn("rows whose file is not on disk: 1, in 1 folder(s); 0 folder(s) are gone entirely; 0 row(s) not "
                      "looked at", " | ".join(said))
        self.assertIn("         1  %s" % self.dir, said)


class TheRootedRowsAreReadOnce(Library):
    """The doctor's two rules of the rooted rows (rooted_rows_convert, native_rows_under_a_root) read one scan of every
    path row, not one each (#1383); a write on the connection, its rollback, or another's commit, reads them again."""

    def test_one_scan_for_both_and_another_after_each_write(self):
        self.photo("a.jpg")
        rooted = "INSERT INTO photos (path, mtime, size, tags) VALUES ('@nowhere/b.jpg', 1, 1, '[]')"
        with mock.patch.object(roots_check, "broken", wraps=roots_check.broken) as scan:
            self.assertEqual({}, self.broken())
            self.assertEqual(1, scan.call_count, "the two rules scanned the rows each")
            self.conn.execute(rooted)
            self.assertEqual(1, checks.rooted_rows_convert(self.conn).count, "its own write was not read")
            self.conn.rollback()
            self.assertEqual(0, checks.rooted_rows_convert(self.conn).count, "a row rolled back was still counted")
            self.conn.execute(rooted)
            self.conn.commit()
            self.assertEqual(1, checks.rooted_rows_convert(self.conn).count)
            other = db.connect(self.db_path)
            try:
                other.execute("INSERT INTO photos (path, mtime, size, tags) VALUES ('@nowhere/c.jpg', 1, 1, '[]')")
                other.commit()
            finally:
                other.close()
            self.assertEqual(2, checks.rooted_rows_convert(self.conn).count, "another connection's commit was not read")
            self.assertEqual(0, checks.native_rows_under_a_root(self.conn).count)
            self.assertEqual(5, scan.call_count)


class TheDoctor(Library):
    def run_doctor(self, *args):
        lines = []
        code = doctor.report(self.db_path, *args, out=lines.append)
        return code, lines

    def test_says_nothing_is_broken_when_nothing_is(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway")
        broken, lines = self.run_doctor()
        self.assertEqual(0, broken)
        self.assertEqual(0, doctor.main(["--db", self.db_path]))

    def test_a_library_not_yet_migrated_is_said_so_and_nothing_else_is_read(self):
        import migration_names
        older = os.path.join(self.dir, "older.db")
        schema.ensure(older)
        lines = []
        with migration_names.ahead():   # the code knows a step the library has not had
            self.assertEqual(1, doctor.report(older, out=lines.append))
        self.assertIn("migrations not applied", "\n".join(lines))

    def test_a_library_below_the_baseline_is_said_to_be_too_old_not_to_be_migrated(self):
        too_old = os.path.join(self.dir, "too_old.db")
        schema.ensure(too_old)
        conn = db.connect(too_old)
        conn.execute("UPDATE schema_version SET version = 31")
        conn.commit()
        conn.close()
        lines = []
        self.assertEqual(1, doctor.report(too_old, out=lines.append))
        said = "\n".join(lines)
        self.assertIn("older than TagPup can bring forward", said)
        self.assertNotIn("start TagPup", said)
        with self.assertRaises(SystemExit) as refused:
            doctor.main(["--db", too_old, "--rebuild-derived"])
        self.assertIn("older than TagPup can bring forward", str(refused.exception))

    def test_counts_without_naming_anyone_unless_asked(self):
        self.face(self.photo("Wren Halloway at the lake.jpg"), name="Wren Halloway", rebuilt=False)
        broken, lines = self.run_doctor()
        self.assertEqual(1, broken)
        self.assertNotIn("Wren", "\n".join(lines))
        _broken, shown = self.run_doctor(5)
        self.assertIn("Wren Halloway at the lake.jpg", "\n".join(shown))
        self.assertEqual(1, doctor.main(["--db", self.db_path]))

    def test_counts_the_photos_whose_file_fails_to_open_and_names_them_only_when_asked(self):
        import damaged_photos
        for name, make in (("whole.jpg", damaged_photos.whole_jpeg), ("incomplete copy.jpg", damaged_photos.second_half_zeros),
                           ("nothing.jpg", lambda path: open(path, "wb").close()),
                           ("zeros.jpg", damaged_photos.all_zeros)):
            self.photo(name, on_disk=False)
            make(os.path.join(self.dir, name))
        self.photo("gone.jpg", on_disk=False)
        _broken, lines = self.run_doctor()
        said = [line for line in lines if line.startswith("photos whose file fails to open")]
        self.assertEqual(1, len(said), lines)
        self.assertIn("fails to open: 3 ", said[0])   # the copy that stops in zeros, the empty file, the file of zeros
        self.assertNotIn("incomplete copy", "\n".join(lines))
        _broken, shown = self.run_doctor(5)
        listed = "\n".join(shown)
        for name in ("incomplete copy.jpg", "nothing.jpg", "zeros.jpg"):
            self.assertIn(name, listed)
        self.assertNotIn("whole.jpg", listed)

    def test_changes_nothing(self):
        self.face(self.photo("a.jpg"), name="Wren Halloway", rebuilt=False)
        self.conn.close()

        def digest():
            with open(self.db_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        before = digest()
        self.run_doctor(5)
        self.assertEqual(before, digest())
        self.assertFalse(os.path.exists(self.db_path + "-wal") and os.path.getsize(self.db_path + "-wal"))


class ALibraryAtRest(unittest.TestCase):
    """Three photos read as the indexer reads them, in two folders, over a small tag tree."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        self.pictures = os.path.join(self.home.root, "Pictures")
        for tag in ("Trips/Coast", "Trips/Lake"):
            taxonomy.add_node(self.conn, tag)
        self.ids = {}
        for folder, name, tags, fields in (("2024", "a.jpg", ["Trips/Coast"], {"XMP:Rating": 3}),
                                           ("2024", "b.jpg", ["Trips/Lake", "Mystery"], {}),
                                           ("2025", "c.jpg", [], {"EXIF:Make": "Harbourlight"})):
            path = os.path.join(self.pictures, folder, name)
            photo_rows.add_read(self.conn, path, dict({"XMP:Subject": tags}, **fields))
            self.ids[name] = self.conn.execute("SELECT id FROM photos WHERE path = ?", (path,)).fetchone()[0]
        self.conn.commit()

    def counts(self):
        return {check.name: check.count for check in checks.run(self.conn) if check.count}

    def digest(self):
        self.conn.close()
        with open(self.path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def update(self, sql, *params):
        self.conn.execute(sql, params)
        self.conn.commit()


class TheDerivedTablesAtRest(ALibraryAtRest):
    def test_a_library_that_was_written_through_the_store_breaks_none(self):
        self.assertEqual({}, self.counts())
        for rule in ("photo_tags_out_of_date", "photo_folders_out_of_date", "folders_out_of_date"):
            self.assertIn(getattr(checks, rule), checks.RULES, rule)

    def test_a_writer_that_changed_a_photos_keywords_and_did_not_refresh(self):
        self.update("UPDATE photos SET tags = '[\"Trips/Lake\"]' WHERE id = ?", self.ids["a.jpg"])
        # The word index is kept from the same writes (tagpup.store.search_index): the keyword's words are out of date too.
        self.assertEqual({"photos whose keyword rows are out of date": 1, "photos whose word index rows are out of date": 1},
                         self.counts())
        self.assertEqual([self.ids["a.jpg"]], checks.photo_tags_out_of_date(self.conn).examples, "an id, never a tag")
        self.update("INSERT INTO photo_tags (photo_id, tag_id) VALUES (999, 1)")
        self.assertEqual(2, self.counts()["photos whose keyword rows are out of date"], "a row for a photo that is gone too")

    def test_a_writer_that_changed_a_photos_path_and_did_not_refresh(self):
        self.update("UPDATE photos SET path = ? WHERE id = ?", os.path.join(self.pictures, "2026", "a.jpg"), self.ids["a.jpg"])
        found = self.counts()
        self.assertEqual(1, found["photos not in the folder their path names"])
        self.assertEqual(self.ids["a.jpg"], checks.photo_folders_out_of_date(self.conn).examples[0])
        self.assertEqual(1, found["folders that are not what the photos' paths give"], "the new folder has no row")

    def test_a_photo_with_no_folder_row_and_a_folder_whose_chain_is_broken(self):
        self.update("DELETE FROM photo_folder WHERE photo_id = ?", self.ids["c.jpg"])
        self.assertEqual({"photos not in the folder their path names": 1}, self.counts())
        folder = os.path.join(self.pictures, "2024")
        for parent in ("NULL", "999999"):
            self.update("UPDATE folders SET parent_id = %s WHERE path = ?" % parent, folder)
            self.assertEqual(1, self.counts()["folders that are not what the photos' paths give"], parent)

    def test_a_folder_with_nothing_in_it_is_reported_not_broken(self):
        # A delete that did not come through the store takes the photo's rows by trigger and leaves the folders.
        self.update("DELETE FROM photos WHERE id = ?", self.ids["c.jpg"])
        self.assertEqual({}, self.counts())
        self.assertEqual(1, len(checks.empty_folders(self.conn)))
        lines = []
        self.assertEqual(0, doctor.report(self.path, out=lines.append))
        self.assertTrue([line for line in lines if line.startswith("folders holding no photo: 1")], lines)
        derived.prune(self.conn)
        self.conn.commit()
        self.assertEqual([], checks.empty_folders(self.conn))

    def test_a_keyword_with_no_node_is_counted_and_never_broken(self):
        # A keyword the index reads gets its node (2026-10-10); one with none is a photo read before that.
        self.update("DELETE FROM tag_taxonomy WHERE tag = 'Mystery'")
        self.assertEqual({}, self.counts())
        self.assertEqual((1, 1, 1, [("Mystery", 1)]), checks.tags_without_a_node(self.conn))
        nodes = self.conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0]
        lines = []
        self.assertEqual(0, doctor.report(self.path, out=lines.append), "reported, not broken")
        said = [line for line in lines if line.startswith("photo tags with no tree node")]
        self.assertEqual(1, len(said))
        self.assertIn("1 keyword(s), 1 use(s) on 1 photo(s)", said[0])
        self.assertNotIn("Mystery", "\n".join(lines))
        shown = []
        doctor.report(self.path, 5, out=shown.append)
        self.assertIn("Mystery", "\n".join(shown))
        self.assertEqual(nodes, self.conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0], "looking changes no tree")
        photo_rows.add_read(self.conn, os.path.join(self.pictures, "2025", "d.jpg"), {"XMP:Subject": ["Another Stray"]})
        self.conn.commit()
        self.assertEqual(nodes + 1, self.conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0])
        self.update("DELETE FROM photos WHERE id = ?", self.ids["b.jpg"])
        lines = []
        doctor.report(self.path, out=lines.append)
        self.assertEqual([], [line for line in lines if line.startswith("photo tags with no tree node")], "none: says nothing")


class TheRepair(ALibraryAtRest):
    def break_everything(self):
        self.conn.execute("UPDATE photos SET tags = '[\"Trips/Lake\"]' WHERE id = ?", (self.ids["a.jpg"],))
        self.conn.execute("UPDATE photos SET path = ? WHERE id = ?",
                          (os.path.join(self.pictures, "2026", "b.jpg"), self.ids["b.jpg"]))
        self.conn.execute("DELETE FROM photos WHERE id = ?", (self.ids["c.jpg"],))
        self.conn.commit()

    def test_a_dry_run_writes_nothing_and_apply_makes_the_tables_what_the_photos_say_and_only_those(self):
        self.break_everything()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            kept = {table: conn.execute("SELECT * FROM %s ORDER BY 1" % table).fetchall()
                    for table in ("photos", "faces", "tag_taxonomy", "settings", "changes", "change_rows")}
        finally:
            conn.close()
        before = self.digest()
        lines = []
        self.assertEqual(1, doctor.rebuild_derived(self.path, out=lines.append))
        self.assertIn("a dry run: nothing was written", "\n".join(lines))
        self.assertEqual(1, doctor.main(["--db", self.path, "--rebuild-derived"]))
        with open(self.path, "rb") as handle:
            self.assertEqual(before, hashlib.sha256(handle.read()).hexdigest())
        lines = []
        self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True, out=lines.append))
        self.assertIn("rebuilt from 2 photo(s)", "\n".join(lines))
        self.assertNotIn("still wrong", "\n".join(lines))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual({}, {check.name: check.count for check in checks.run(conn) if check.count})
            self.assertEqual([], checks.empty_folders(conn))
            self.assertEqual(kept, {table: conn.execute("SELECT * FROM %s ORDER BY 1" % table).fetchall() for table in kept})
        finally:
            conn.close()

    def test_a_library_that_is_right_is_left_alone(self):
        lines = []
        before = self.digest()
        self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True, out=lines.append))
        self.assertEqual(["the derived tables are what the photos say, and every person's name what their node is called"], lines)
        with open(self.path, "rb") as handle:
            self.assertEqual(before, hashlib.sha256(handle.read()).hexdigest(), "nothing written")


@unittest.skipUnless(os.name == "nt", "spellings below are Windows paths")
class TheFoldersOfALibraryWithRoots(unittest.TestCase):
    def test_a_rooted_folder_that_is_not_the_one_its_photos_name_is_reported(self):
        import roots_library as rl
        home = own_home.for_test(self, prefix="derived_roots_")
        side = rl.Side(home, "adopted", real=1, bulk=2, outside=1)
        self.assertTrue(side.adopt().ok)
        conn = db.connect(side.db_path)
        try:
            broken = {check.name for check in checks.run(conn) if check.count}
            self.assertFalse(broken & {"rooted rows that do not convert back", "native rows under a root",
                                       "folders that are not what the photos' paths give",
                                       "photos not in the folder their path names"}, broken)
            conn.execute("UPDATE folders SET path = '@nowhere/2024' WHERE id = (SELECT MIN(id) FROM folders"
                         " WHERE path LIKE '@%/%')")
            conn.commit()
            broken = {check.name: check.count for check in checks.run(conn) if check.count}
            self.assertGreater(broken["photos not in the folder their path names"], 0)
            self.assertGreater(broken["folders that are not what the photos' paths give"], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""The rules the derived tables are held to (tagpup.store.checks, tools/doctor.py, the MCP's checks):
photo_tags, folders, photo_folder and photo_meta are what the photos and the tag tree give, or the
doctor says which photos are not. Each rule is a thing that goes wrong when a writer forgets
(docs/ARCHITECTURE.md, phase 9a), and each has a repair, `tools/doctor.py --rebuild-derived --apply`,
which makes the tables what the photos say and writes nothing else.

Fictional names only: the libraries are photographs of real people, many of them minors.
"""
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.store import checks, db, derived, schema, taxonomy  # noqa: E402

RULES = ("photo_tags_out_of_date", "photo_folders_out_of_date", "folders_out_of_date", "photo_meta_out_of_date")


class ALibraryAtRest(unittest.TestCase):
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

    def rule(self, name):
        return getattr(checks, name)(self.conn)

    def digest(self):
        self.conn.close()
        with open(self.path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()


class TheRules(ALibraryAtRest):
    def test_a_library_that_was_written_through_the_store_breaks_none(self):
        self.assertEqual({}, self.counts())
        for name in RULES:
            self.assertIn(getattr(checks, name), checks.RULES, name)

    def test_a_writer_that_changed_a_photos_keywords_and_did_not_refresh(self):
        self.conn.execute("UPDATE photos SET tags = '[\"Trips/Lake\"]' WHERE id = ?", (self.ids["a.jpg"],))
        self.conn.commit()
        # The word index is kept from the same writes (tagpup.store.search_index): the keyword's words are out of date too.
        self.assertEqual({"photos whose keyword rows are out of date": 1, "photos whose word index rows are out of date": 1},
                         self.counts())
        found = self.rule("photo_tags_out_of_date")
        self.assertEqual([self.ids["a.jpg"]], found.examples, "an id, never a tag")

    def test_a_row_for_a_photo_that_is_gone_is_found_too(self):
        self.conn.execute("INSERT INTO photo_tags (photo_id, tag_id) VALUES (999, 1)")
        self.conn.commit()
        self.assertEqual({"photos whose keyword rows are out of date": 1}, self.counts())

    def test_a_writer_that_changed_a_photos_path_and_did_not_refresh(self):
        self.conn.execute("UPDATE photos SET path = ? WHERE id = ?",
                          (os.path.join(self.pictures, "2026", "a.jpg"), self.ids["a.jpg"]))
        self.conn.commit()
        found = self.counts()
        self.assertEqual(1, found["photos not in the folder their path names"])
        self.assertEqual(self.ids["a.jpg"], self.rule("photo_folders_out_of_date").examples[0])
        self.assertEqual(1, found["folders that are not what the photos' paths give"], "the new folder has no row")

    def test_a_photo_with_no_folder_row(self):
        self.conn.execute("DELETE FROM photo_folder WHERE photo_id = ?", (self.ids["c.jpg"],))
        self.conn.commit()
        self.assertEqual({"photos not in the folder their path names": 1}, self.counts())

    def test_a_folder_whose_parent_chain_is_not_complete(self):
        self.conn.execute("UPDATE folders SET parent_id = NULL WHERE path = ?", (os.path.join(self.pictures, "2024"),))
        self.conn.commit()
        self.assertEqual({"folders that are not what the photos' paths give": 1}, self.counts())
        self.assertEqual(1, len(self.rule("folders_out_of_date").examples))
        self.conn.execute("UPDATE folders SET parent_id = 999999 WHERE path = ?", (os.path.join(self.pictures, "2024"),))
        self.conn.commit()
        self.assertEqual({"folders that are not what the photos' paths give": 1}, self.counts())

    def test_a_folder_a_photo_needs_and_has_not_is_counted(self):
        self.conn.execute("DELETE FROM photo_folder")
        self.conn.execute("DELETE FROM folders")
        self.conn.commit()
        found = self.counts()
        self.assertEqual(3, found["photos not in the folder their path names"])
        self.assertGreater(found["folders that are not what the photos' paths give"], 0)

    def test_a_folder_with_nothing_in_it_is_reported_not_broken(self):
        """A delete that did not come through the store takes the photo's rows by trigger and leaves the
        folders: nothing counted is wrong, and the doctor says so."""
        self.conn.execute("DELETE FROM photos WHERE id = ?", (self.ids["c.jpg"],))
        self.conn.commit()
        self.assertEqual({}, self.counts())
        self.assertEqual(1, len(checks.empty_folders(self.conn)))
        lines = []
        self.assertEqual(0, doctor.report(self.path, out=lines.append))
        self.assertTrue([line for line in lines if line.startswith("folders holding no photo: 1")], lines)
        derived.prune(self.conn)
        self.conn.commit()
        self.assertEqual([], checks.empty_folders(self.conn))

    def test_a_writer_that_changed_a_photos_metadata_and_did_not_refresh(self):
        self.conn.execute("UPDATE photos SET raw_metadata = ? WHERE id = ?", ('{"XMP:Rating": 5}', self.ids["c.jpg"]))
        self.conn.commit()
        self.assertEqual({"photos whose metadata rows are out of date": 1}, self.counts())
        self.assertEqual([self.ids["c.jpg"]], self.rule("photo_meta_out_of_date").examples)

    def test_a_row_of_metadata_that_is_not_what_the_photo_says(self):
        self.conn.execute("UPDATE photo_meta SET rating = 1 WHERE photo_id = ?", (self.ids["a.jpg"],))
        self.conn.commit()
        self.assertEqual({"photos whose metadata rows are out of date": 1}, self.counts())

    def test_the_new_rules_wait_for_the_migration(self):
        from test_migrations import at_version
        older = self.home.library("older.db")
        at_version(older, 18)
        conn = db.connect(db.readonly_uri(older), uri=True)
        try:
            for name in RULES:
                self.assertEqual(0, getattr(checks, name)(conn).count, name)
            self.assertEqual((0, 0, 0, []), checks.tags_without_a_node(conn))
            self.assertEqual([], checks.empty_folders(conn))
        finally:
            conn.close()


class KeywordsWithNoNode(ALibraryAtRest):
    def test_they_are_counted_by_keyword_use_and_photo_and_never_broken(self):
        self.assertEqual({}, self.counts())
        distinct, uses, photos, ranked = checks.tags_without_a_node(self.conn)
        self.assertEqual((1, 1, 1, [("Mystery", 1)]), (distinct, uses, photos, ranked))

    def test_the_doctor_says_how_many_and_names_none_unless_asked(self):
        lines = []
        self.assertEqual(0, doctor.report(self.path, out=lines.append), "reported, not broken")
        said = [line for line in lines if line.startswith("photo tags with no tree node")]
        self.assertEqual(1, len(said))
        self.assertIn("1 keyword(s), 1 use(s) on 1 photo(s)", said[0])
        self.assertNotIn("Mystery", "\n".join(lines))
        shown = []
        doctor.report(self.path, 5, out=shown.append)
        self.assertIn("Mystery", "\n".join(shown))

    def test_the_tree_is_not_changed_by_reporting_them_or_by_indexing_them(self):
        nodes = self.conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0]
        photo_rows.add_read(self.conn, os.path.join(self.pictures, "2025", "d.jpg"), {"XMP:Subject": ["Another Stray"]})
        self.conn.commit()
        self.assertEqual(nodes, self.conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0])
        self.assertEqual(2, checks.tags_without_a_node(self.conn)[0])

    def test_the_doctor_says_nothing_of_them_when_there_are_none(self):
        self.conn.execute("DELETE FROM photos WHERE id = ?", (self.ids["b.jpg"],))
        self.conn.commit()
        lines = []
        doctor.report(self.path, out=lines.append)
        self.assertEqual([], [line for line in lines if line.startswith("photo tags with no tree node")])


class TheRepair(ALibraryAtRest):
    def break_everything(self):
        self.conn.execute("UPDATE photos SET tags = '[\"Trips/Lake\"]' WHERE id = ?", (self.ids["a.jpg"],))
        self.conn.execute("UPDATE photos SET raw_metadata = '{\"XMP:Rating\": 5}' WHERE id = ?", (self.ids["c.jpg"],))
        self.conn.execute("UPDATE photos SET path = ? WHERE id = ?",
                          (os.path.join(self.pictures, "2026", "b.jpg"), self.ids["b.jpg"]))
        self.conn.execute("DELETE FROM photos WHERE id = ?", (self.ids["c.jpg"],))
        self.conn.commit()

    def test_a_dry_run_says_what_is_wrong_and_writes_nothing(self):
        self.break_everything()
        before = self.digest()
        lines = []
        self.assertEqual(1, doctor.rebuild_derived(self.path, out=lines.append))
        self.assertIn("a dry run: nothing was written", "\n".join(lines))
        self.assertEqual(before, self.digest())
        self.assertEqual(1, doctor.main(["--db", self.path, "--rebuild-derived"]))

    def test_it_makes_the_tables_what_the_photos_say_and_verifies_it(self):
        self.break_everything()
        lines = []
        self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True, out=lines.append))
        self.assertIn("rebuilt from 2 photo(s)", "\n".join(lines))
        self.assertNotIn("still wrong", "\n".join(lines))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual({}, {check.name: check.count for check in checks.run(conn) if check.count})
            self.assertEqual([], checks.empty_folders(conn))
        finally:
            conn.close()

    def test_it_writes_only_derived_rows(self):
        self.break_everything()
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            kept = {table: conn.execute("SELECT * FROM %s ORDER BY 1" % table).fetchall()
                    for table in ("photos", "faces", "tag_taxonomy", "settings", "changes", "change_rows")}
        finally:
            conn.close()
        self.assertEqual(0, doctor.main(["--db", self.path, "--rebuild-derived", "--apply"]))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(kept, {table: conn.execute("SELECT * FROM %s ORDER BY 1" % table).fetchall() for table in kept})
        finally:
            conn.close()

    def test_a_library_that_is_right_is_left_alone(self):
        lines = []
        before = self.digest()
        self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True, out=lines.append))
        self.assertEqual(["the derived tables are what the photos say, and every person's name what their node is called"], lines)
        self.assertEqual(before, self.digest(), "nothing written")

    def test_a_library_behind_the_migration_is_told_to_open_it_and_not_migrated(self):
        from test_migrations import at_version
        older = self.home.library("older.db")
        at_version(older, 18)
        def digest():
            with open(older, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()

        before = digest()
        lines = []
        self.assertEqual(1, doctor.rebuild_derived(older, apply=True, out=lines.append))
        self.assertIn("schema 18", lines[0])
        self.assertEqual(before, digest())

    def test_apply_goes_with_rebuild_derived(self):
        with self.assertRaises(SystemExit):
            doctor.main(["--db", self.path, "--apply"])


@unittest.skipUnless(os.name == "nt", "spellings below are Windows paths")
class TheFoldersOfAnAdoptedLibrary(unittest.TestCase):
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

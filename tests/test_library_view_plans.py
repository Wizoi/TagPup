"""The plans of what the library views ask of the database (tagpup.store.library_view; docs/ARCHITECTURE.md, phase 9a-2).

A page of a source must SEARCH an index, never SCAN photos: on the owner's library of 68,466 photos a scan of the
table is what made "everything under Trips/" and a folder's count slow once, and a 20,000-photo source must page
without one. The plans do not depend on how many rows a library has (it has no ANALYZE statistics, as photo_index has
none), so a library of some hundreds, made by the code that makes the real thing, shows the plan the real one gets; the
real scale is measured by hand on a synthetic library of photo_index's size (docs/ARCHITECTURE.md, phase 9a-2).

Each statement the store runs is captured as SQLite expands it and explained. Fictional names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.store import db, derived, photos, taxonomy  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402


class Plans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Case(unittest.TestCase):
            def runTest(self):
                pass
        cls.case = Case()
        cls.vl = ViewLibrary(cls.case)
        cls.addClassCleanup(cls.case.doCleanups)
        vl = cls.vl
        for name in ("Wren Halloway", "Rowan Thackeray", "Cora Ingersoll"):
            taxonomy.add_path(vl.conn, "People/" + name, root_has_face=1)
        for tag in ("Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Activity/Sailing"):
            taxonomy.add_path(vl.conn, tag)
        batch = derived.Batch(vl.conn)
        known = taxonomy.read_people_vocabulary(vl.conn)
        tags = ["Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Activity/Sailing", "People/Wren Halloway",
                "People/Rowan Thackeray", "People/Cora Ingersoll"]
        for number in range(900):
            path = os.path.join(vl.pictures, "%d" % (2015 + number % 8), "Event %02d" % (number % 40), "IMG_%04d.jpg" % number)
            fields = {"XMP:Subject": [tags[number % 7], tags[(number * 3) % 7]]}
            if number % 50:
                fields["EXIF:DateTimeOriginal"] = "%d:%02d:%02d 10:%02d:00" % (2015 + number % 8, number % 12 + 1, number % 28 + 1,
                                                                              number % 60)
            photos.record_indexed(vl.conn, path, photo_rows.as_read(path, fields), known=known, batch=batch)
        vl.conn.commit()
        cls.folder = os.path.join(vl.pictures, "2015", "Event 00")
        cls.top = os.path.join(vl.pictures, "2015")

    def plan_of(self, run):
        """[(statement, [plan lines])] of every statement `run(conn)` runs."""
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            run(conn)
        finally:
            conn.set_trace_callback(None)
        found = []
        for statement in statements:
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                lines = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + statement)]
                found.append((statement, lines))
        conn.close()
        return found

    def assert_searches(self, run, index):
        """Every page statement seeks `index` or another and scans no table; a count scans at most an index."""
        found = self.plan_of(run)
        text = "\n".join("\n".join(lines) for _statement, lines in found)
        self.assertIn(index, text)
        for statement, lines in found:
            if "sqlite_master" in statement or "FROM roots" in statement or "damaged_files" in statement:
                continue
            for line in lines:
                if line.startswith("SCAN"):
                    self.assertTrue(statement.lstrip().upper().startswith("SELECT COUNT") and "COVERING INDEX" in line,
                                    "a scan: %s\n%s" % (statement, "\n".join(lines)))
        return text

    # ---- A page of each kind of source ------------------------------------------------------

    def page(self, source, cursor=None):
        return lambda conn: store.view(conn, source, cursor, 500)

    def test_all_seeks_the_date_index_and_needs_no_sort(self):
        text = self.assert_searches(self.page(store.Source(store.ALL)), "idx_photos_taken")
        self.assertNotIn("TEMP B-TREE", text)

    def test_a_page_deep_into_it_is_a_seek_too(self):
        cursor = store.Cursor(0, "2018:06:15 10:00:00", 400)
        text = self.assert_searches(self.page(store.Source(store.ALL), cursor), "idx_photos_taken")
        self.assertNotIn("TEMP B-TREE", text)

    def test_a_year_seeks_its_index_in_order(self):
        text = self.assert_searches(self.page(store.Source(store.YEAR, 2016)), "idx_photos_year")
        self.assertNotIn("TEMP B-TREE", text)

    def test_a_month_is_a_range_of_the_date_index(self):
        text = self.assert_searches(self.page(store.Source(store.MONTH, "2016-02")), "idx_photos_taken (taken>? AND taken<?)")
        self.assertNotIn("TEMP B-TREE", text)
        asked = [statement for statement, _lines in self.plan_of(self.page(store.Source(store.MONTH, "2016-02")))]
        self.assertEqual([], [each for each in asked if "taken IS NULL" in each], "every photo of a month has a date")

    def test_a_folder_and_its_subfolders_is_a_range_of_the_path_index(self):
        self.assert_searches(self.page(store.Source(store.FOLDER, self.top, True)), "idx_photos_path_nocase (path>? AND path<?)")
        self.assert_searches(self.page(store.Source(store.FOLDER, self.folder, True)), "idx_photos_path_nocase")

    def test_a_folder_alone_is_its_rows_of_photo_folder_by_folder_id(self):
        text = self.assert_searches(self.page(store.Source(store.FOLDER, self.folder, False)), "idx_photo_folder_folder (folder_id=?)")
        self.assertIn("sqlite_autoindex_folders_1 (path=?)", text)

    def test_a_keyword_is_two_seeks_of_the_tree_and_one_of_photo_tags_for_each_node(self):
        text = self.assert_searches(self.page(store.Source(store.KEYWORD, "Trips")), "idx_photo_tags_tag (tag_id=?)")
        self.assertIn("sqlite_autoindex_tag_taxonomy_1 (tag=?)", text)
        self.assertIn("idx_tag_taxonomy_tag (tag>? AND tag<?)", text)

    def test_a_person_is_the_name_index(self):
        found = self.plan_of(self.page(store.Source(store.PERSON, "Wren Halloway")))
        text = "\n".join("\n".join(lines) for _statement, lines in found)
        self.assertIn("idx_photo_people_name (name=?)", text)
        for statement, lines in found:
            for line in lines:
                if line.startswith("SCAN"):
                    # the names the person is spelled as: a pass of the name index (400 names on photo_index), covering
                    self.assertIn("SELECT DISTINCT name FROM photo_people", statement)
                    self.assertIn("COVERING INDEX idx_photo_people_name", line)

    def test_the_undated_photos_come_from_the_date_index_too(self):
        cursor = store.Cursor(1, None, 0)
        text = self.assert_searches(self.page(store.Source(store.ALL), cursor), "idx_photos_taken (taken=? AND id>?)")
        self.assertNotIn("TEMP B-TREE", text)

    def test_the_totals_are_counts_of_an_index(self):
        for source in (store.Source(store.ALL), store.Source(store.YEAR, 2016), store.Source(store.MONTH, "2016-02"),
                       store.Source(store.FOLDER, self.top, True), store.Source(store.FOLDER, self.folder),
                       store.Source(store.KEYWORD, "Trips")):
            with self.subTest(kind=source.kind):
                self.assert_searches(lambda conn, s=source: store.total(conn, s), "INDEX")

    # ---- A source's whole id list (phase 9b-2) --------------------------------------------------

    def ids(self, source):
        return lambda conn: store.all_ids(conn, source, 200000)

    def test_the_whole_id_list_of_all_year_and_month_is_one_read_of_the_date_index_in_order(self):
        for source, index in ((store.Source(store.ALL), "idx_photos_taken"), (store.Source(store.YEAR, 2016), "idx_photos_year"),
                              (store.Source(store.MONTH, "2016-02"), "idx_photos_taken (taken>? AND taken<?)")):
            with self.subTest(kind=source.kind):
                text = self.assert_searches(self.ids(source), index)
                self.assertNotIn("TEMP B-TREE", text)

    def test_the_whole_id_list_of_a_folder_a_keyword_and_a_person_scans_no_table(self):
        self.assert_searches(self.ids(store.Source(store.FOLDER, self.top, True)), "idx_photos_path_nocase (path>? AND path<?)")
        self.assert_searches(self.ids(store.Source(store.FOLDER, self.folder, False)), "idx_photo_folder_folder (folder_id=?)")
        self.assert_searches(self.ids(store.Source(store.KEYWORD, "Trips")), "idx_photo_tags_tag (tag_id=?)")
        found = self.plan_of(self.ids(store.Source(store.PERSON, "Wren Halloway")))
        self.assertIn("idx_photo_people_name (name=?)", "\n".join("\n".join(lines) for _statement, lines in found))

    def test_id_plans_gives_each_statement_with_its_plan_for_the_measurement_script(self):
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            statements, elapsed = store.id_plans(conn, store.Source(store.ALL), 1000)
        finally:
            conn.close()
        self.assertEqual(2, len(statements), "the dated photos, then the undated")
        self.assertTrue(all(lines and lines[0].startswith("SEARCH") for _sql, lines in statements))
        self.assertGreaterEqual(elapsed, 0)

    def test_the_whole_id_list_reads_the_ids_and_nothing_else(self):
        for source in (store.Source(store.ALL), store.Source(store.KEYWORD, "Trips"), store.Source(store.FOLDER, self.top, True)):
            for statement, _lines in self.plan_of(self.ids(source)):
                for column in ("raw_metadata", "tags", "captions", "embedding", "vector"):
                    self.assertNotRegex(statement, r"\b%s\b" % column)

    # ---- The cards and the navigator ----------------------------------------------------------

    def test_the_cards_are_a_lookup_by_primary_key(self):
        text = self.assert_searches(lambda conn: store.card_rows(conn, range(1, 501)), "INTEGER PRIMARY KEY")
        self.assertNotIn("SCAN photos", text)

    def test_the_navigators_reads_scan_covering_indexes_only(self):
        for name, run in (("folders", store.folders), ("keywords", store.keyword_counts), ("people", store.people_counts),
                          ("dates", store.date_counts)):
            with self.subTest(section=name):
                for statement, lines in self.plan_of(run):
                    if "FROM roots" in statement:
                        continue   # the library's roots: a row or two
                    for line in lines:
                        if line.startswith("SCAN"):
                            # photo_tags is WITHOUT ROWID: its primary key is the table, read in photo order
                            self.assertTrue("COVERING INDEX" in line or line == "SCAN photo_tags" or "tag_taxonomy" in line
                                            or "folders" in line, "%s\n%s" % (statement, line))


if __name__ == "__main__":
    unittest.main()

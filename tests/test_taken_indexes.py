"""Migration 20: the two indexes the library views page by (tagpup.store.schema; docs/DATABASE.md, `photos`).

Additive and index-only: a library at 19 gets `idx_photos_taken` (taken, id) and `idx_photos_year` (year, taken, id),
no row of any table changes, and a change journaled before it can still be undone."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.store import db, journal, schema  # noqa: E402

INDEXES = {"idx_photos_taken", "idx_photos_year"}


def look(path, sql):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class MigrationTwenty(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 19)
        conn = db.connect(self.path)
        try:
            for number in range(3):
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", "a%d.jpg" % number),
                                    {"EXIF:DateTimeOriginal": "2024:06:0%d 10:00:00" % (number + 1)})
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def indexes(self):
        return {name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'index'")} & INDEXES

    def test_a_library_at_19_gets_the_two_indexes_and_no_row_changes(self):
        self.assertEqual(set(), self.indexes())
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        # the migration's own record (its version, and its entry in the journal) aside
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        self.assertEqual(["photos by when they were taken"], schema.ensure(self.path))
        self.assertEqual(INDEXES, self.indexes())
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables})
        self.assertEqual(20, look(self.path, "SELECT MAX(version) FROM schema_version")[0][0])

    def test_they_are_made_once_and_the_migration_is_not_run_again(self):
        schema.ensure(self.path)
        schema._current.clear()
        self.assertEqual([], schema.ensure(self.path))

    def test_a_change_made_at_19_is_still_undoable_after_it(self):
        applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        schema.ensure(self.path)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
        finally:
            conn.close()

    def test_the_planner_pages_the_whole_library_by_the_index(self):
        schema.ensure(self.path)
        plan = look(self.path, "EXPLAIN QUERY PLAN SELECT taken, id FROM photos WHERE taken IS NOT NULL "
                               "AND (taken, id) > ('2024:06:01', 0) ORDER BY taken, id LIMIT 10")
        text = " ".join(row[3] for row in plan)
        self.assertIn("idx_photos_taken", text)
        self.assertNotIn("SCAN", text)
        self.assertNotIn("TEMP B-TREE", text)


if __name__ == "__main__":
    unittest.main()

"""Migration 22: the index a library view orders by file name with (tagpup.store.schema; docs/DATABASE.md, `photos`;
docs/ARCHITECTURE.md, phase 9, #671).

Additive and index-only, as migration 20 is: a library at 21 gets `idx_photos_name` and nothing else -- no column, no
trigger, no row of any table changes (its declared `touches` is empty, and nothing checks that by itself: findings #464,
so this does) -- and a change journaled before it can still be undone."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class MigrationTwentyTwo(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 21)
        conn = db.connect(self.path)
        try:
            for folder, name in (("Coast", "b.jpg"), ("Coast", "A.jpg"), ("Lakes", "c.JPG")):
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", folder, name),
                                    {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"})
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def schema_rows(self):
        return look(self.path, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")

    def test_a_library_at_21_gets_the_index_and_nothing_else(self):
        before_schema = self.schema_rows()
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        # the migration's own record (its version, and its entry in the journal) aside
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:22]), mock.patch.object(schema, "LATEST", 22):
            self.assertEqual(migration_names.named(22), schema.ensure(self.path))
        added = [row for row in self.schema_rows() if row not in before_schema]
        self.assertEqual([("index", store.NAME_INDEX, "photos")], [row[:3] for row in added], "one index, no column or trigger")
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables})
        migration = [m for m in schema.MIGRATIONS if m.version == 22][0]
        self.assertEqual((schema.ADDITIVE, ()), (migration.kind, migration.touches))

    def test_a_change_made_at_21_is_still_undoable_after_it(self):
        applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        schema.ensure(self.path)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
        finally:
            conn.close()

    def test_the_index_holds_each_file_name_and_the_planner_walks_it(self):
        schema.ensure(self.path)
        names = [name for (name,) in look(self.path, "SELECT %s FROM photos ORDER BY %s COLLATE NOCASE, id"
                                          % (store.name_sql(), store.name_sql()))]
        self.assertEqual(sorted(names, key=str.lower), names)
        self.assertTrue({"A.jpg", "b.jpg", "c.JPG"} <= set(names), "the name, never a folder of it")
        plan = " ".join(row[3] for row in look(
            self.path, "EXPLAIN QUERY PLAN SELECT p.id FROM photos p WHERE %s COLLATE NOCASE >= 'b' ORDER BY %s COLLATE NOCASE, p.id"
            % (store.name_sql("p.path"), store.name_sql("p.path"))))
        self.assertIn(store.NAME_INDEX, plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_a_native_path_with_the_machines_separator_is_named_too(self):
        schema.ensure(self.path)
        separator = chr(92)
        found = look(self.path, "SELECT %s FROM (SELECT ? AS path)" % store.name_sql(),
                     (separator.join(["D:", "Photos", "2024", "IMG_0001.jpg"]),))
        self.assertEqual("IMG_0001.jpg", found[0][0])
        self.assertEqual("plain.jpg", look(self.path, "SELECT %s FROM (SELECT 'plain.jpg' AS path)" % store.name_sql())[0][0])


if __name__ == "__main__":
    unittest.main()

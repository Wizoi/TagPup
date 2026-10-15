"""The indexes a library view pages by (tagpup.store.schema, migrations 20 and 22; docs/DATABASE.md, `photos`; #464, #671).

Additive and index-only: a library at 19 gets `idx_photos_taken` (taken, id) and `idx_photos_year` (year, taken, id), and
one at 21 gets `idx_photos_name` and nothing else -- no column, no trigger, no row of any table changes (a declared
`touches` that is empty is not checked by itself, so this does) -- and a change journaled before them can still be
undone. The planner then walks each by its index with no sort.

Lifted: test_taken_indexes (migration 20) and test_name_index (migration 22). Break that fails the kept test: a
migration 22 that makes no index (`CREATE INDEX` removed) fails test_a_library_at_19_gets_the_indexes_one_step_at_a_time.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

TAKEN_INDEXES = {"idx_photos_taken", "idx_photos_year"}


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class TheIndexesALibraryViewPagesBy(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 19)
        conn = db.connect(self.path)
        try:
            for folder, name in (("Coast", "b.jpg"), ("Coast", "A.jpg"), ("Lakes", "c.JPG")):
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", folder, name),
                                    {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"})
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def objects(self):
        return {(kind, name) for kind, name in look(self.path, "SELECT type, name FROM sqlite_master")}

    def rows(self):
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        # the migration's own record (its version, and its entry in the journal) aside
        return {table: look(self.path, "SELECT * FROM %s" % table) for table in tables
                if table not in ("schema_version", "changes", "change_rows")}

    def migrate_to(self, version):
        before_objects, before_rows = self.objects(), self.rows()
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:version]), mock.patch.object(schema, "LATEST", version):
            applied = schema.ensure(self.path)
        schema._current.clear()
        return applied, self.objects() - before_objects, before_rows

    def test_a_library_at_19_gets_the_indexes_one_step_at_a_time(self):
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:19]), mock.patch.object(schema, "LATEST", 19):
            change = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        applied, added, was = self.migrate_to(20)
        self.assertEqual(migration_names.named(20), applied)
        self.assertEqual({("index", name) for name in TAKEN_INDEXES}, added)
        self.assertEqual(was, self.rows())
        self.migrate_to(21)
        applied, added, was = self.migrate_to(22)
        self.assertEqual(migration_names.named(22), applied)
        self.assertEqual({("index", store.NAME_INDEX)}, added, "one index, no column or trigger")
        self.assertEqual(was, self.rows())
        for version in (20, 22):
            migration = [m for m in schema.MIGRATIONS if m.version == version][0]
            self.assertEqual((schema.ADDITIVE, ()), (migration.kind, migration.touches))
        # Made once: the migrations are not run again; and the change made at 19 is still undoable.
        schema.ensure(self.path)
        schema._current.clear()
        self.assertEqual([], schema.ensure(self.path))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, change.change_id))
        finally:
            conn.close()

    def test_the_planner_walks_each_index_with_no_sort(self):
        schema.ensure(self.path)
        plan = " ".join(row[3] for row in look(
            self.path, "EXPLAIN QUERY PLAN SELECT taken, id FROM photos WHERE taken IS NOT NULL "
                       "AND (taken, id) > ('2024:06:01', 0) ORDER BY taken, id LIMIT 10"))
        self.assertIn("idx_photos_taken", plan)
        self.assertNotIn("SCAN", plan)
        self.assertNotIn("TEMP B-TREE", plan)
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
        found = look(self.path, "SELECT %s FROM (SELECT ? AS path)" % store.name_sql(),
                     (chr(92).join(["D:", "Photos", "2024", "IMG_0001.jpg"]),))
        self.assertEqual("IMG_0001.jpg", found[0][0])
        self.assertEqual("plain.jpg", look(self.path, "SELECT %s FROM (SELECT 'plain.jpg' AS path)" % store.name_sql())[0][0])


if __name__ == "__main__":
    unittest.main()

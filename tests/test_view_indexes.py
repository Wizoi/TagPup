"""The indexes a library view pages by (tagpup.store.schema, the baseline; docs/DATABASE.md, `photos`; #464, #671): a library
is made with `idx_photos_taken` (taken, id), `idx_photos_year` (year, taken, id) and `idx_photos_name`, and the planner walks
each by its index with no sort. (The steps that added them, migrations 20 and 22, are in the baseline; their tests of a library
at 19 went with them.)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.store import db, schema  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

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
        schema.ensure(self.path)
        conn = db.connect(self.path)
        try:
            for folder, name in (("Coast", "b.jpg"), ("Coast", "A.jpg"), ("Lakes", "c.JPG")):
                photo_rows.add_read(conn, os.path.join(self.home.root, "Pictures", folder, name),
                                    {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"})
            conn.commit()
        finally:
            conn.close()
        schema.forget()

    def test_the_planner_walks_each_index_with_no_sort(self):
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

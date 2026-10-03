"""A loop of per-photo writes shares ONE read of the tag tree (findings #494), and a damaged read is no
keywords (#495). Timings are bounds, generous against the machine: the old cost was 1 ms a photo for the
tree alone, which is what a Smart Rename of thousands of photos paid under the write lock.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
from tagpup.store import db, derived, photos, schema, taxonomy  # noqa: E402

TAGS = ["%s/Branch %d/Leaf %d" % (root, i, j) for root in ("People", "Trips", "Activity", "School", "Pets", "Scenic")
        for i in range(30) for j in range(5)]


class WithABigTree(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.path = home.library("harbour.db")
        schema.ensure(self.path)
        self.library = Library(self.path)
        self.folder = os.path.join(home.root, "Pictures", "2024")
        conn = db.connect(self.path)
        try:
            for tag in TAGS:
                taxonomy.add_path(conn, tag)
            conn.commit()
        finally:
            conn.close()

    def seed(self, count):
        """`count` photos as the index records them, in one batch; their paths."""
        found = []
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            batch = derived.Batch(conn)
            for n in range(count):
                path = os.path.join(self.folder, "IMG_%05d.jpg" % n)
                photos.record_indexed(conn, path, {"tags": [TAGS[n % len(TAGS)]], "raw_metadata": {}}, batch=batch)
                found.append(path)
            conn.commit()
        finally:
            conn.close()
        return found

    def timed(self, work):
        began = time.perf_counter()
        work()
        return time.perf_counter() - began


class OneBatchForALoop(WithABigTree):
    def test_five_thousand_follows_of_one_photo_each_with_a_shared_batch_take_under_a_second(self):
        self.seed(50)
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            batch = derived.Batch(conn)
            took = self.timed(lambda: [derived.refresh_photos(conn, [1 + n % 50], batch) for n in range(5000)])
            conn.rollback()
        finally:
            conn.close()
        self.assertLess(took, 1.0)

    def test_a_rename_of_two_thousand_photos_does_not_read_the_tree_for_each(self):
        paths = self.seed(2000)
        renames = {path: path.replace("IMG_", "Regatta_") for path in paths}
        took = self.timed(lambda: photos.move_rows(self.path, renames))
        self.assertLess(took, 1.2, "the write lock was held for %.2f s" % took)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
        finally:
            conn.close()

    def test_files_left_alone_and_followed_read_the_tree_once(self):
        paths = self.seed(2000)
        followed = [(path, {"XMP:Subject": [TAGS[(n + 1) % len(TAGS)]]}) for n, path in enumerate(paths)]
        took = self.timed(lambda: file_changes._follow(self.library, followed))
        self.assertLess(took, 2.5, "the write lock was held for %.2f s" % took)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
        finally:
            conn.close()

    def test_a_call_with_several_ids_reads_the_tree_once(self):
        self.seed(500)
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            reads = []
            real = derived.Tree.read
            derived.Tree.read = classmethod(lambda cls, c: reads.append(1) or real.__func__(cls, c))
            try:
                derived.refresh_photos(conn, range(1, 501))
            finally:
                derived.Tree.read = real
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(1, len(reads))


class ADamagedRead(WithABigTree):
    def test_a_record_whose_tags_are_none_or_not_a_list_is_no_keywords_and_the_batch_goes_on(self):
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            batch = derived.Batch(conn)
            for n, tags in enumerate((None, "People/Branch 1/Leaf 1", {"a": 1}, 7)):
                photos.record_indexed(conn, os.path.join(self.folder, "bad%d.jpg" % n),
                                      {"tags": tags, "raw_metadata": {}}, batch=batch)
            photos.record_indexed(conn, os.path.join(self.folder, "good.jpg"),
                                  {"tags": [TAGS[0]], "raw_metadata": {}}, batch=batch)
            conn.commit()
            self.assertEqual(5, conn.execute("SELECT COUNT(*) FROM photo_meta").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0])
            self.assertEqual([], derived.problems(conn))
        finally:
            conn.close()


class NothingReadsBackWithoutAnOwner(unittest.TestCase):
    def test_record_reads_is_gone(self):
        self.assertFalse(hasattr(photos, "record_reads"))


if __name__ == "__main__":
    unittest.main()

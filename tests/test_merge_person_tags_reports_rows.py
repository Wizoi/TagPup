"""merge_duplicate_person_tags reports the nodes it deleted, not the ones it planned.

It printed "Removed N" from the length of its plan, and deleted through its own
connection outside the library's write lock.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
import merge_duplicate_person_tags as merge  # noqa: E402


class MergeReportsRows(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merge_tags_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        conn = tagpup_db.connect(self.db)
        conn.execute("CREATE TABLE tag_taxonomy (id INTEGER PRIMARY KEY, tag TEXT, name TEXT,"
                     " parent_id INTEGER, has_face INTEGER)")
        conn.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('Rowan Thackeray', 'Rowan Thackeray', 1)")
        conn.commit()
        conn.close()

    def test_it_counts_what_it_deleted_under_the_write_lock(self):
        with mock.patch.object(merge.tagpup_db, "write_with_connection",
                               wraps=tagpup_db.write_with_connection) as locked:
            # One node exists; the other was already gone.
            removed = merge.apply_plan(self.db, ["Rowan Thackeray", "Imogen Vale"], {})
        self.assertEqual(1, removed)
        self.assertTrue(locked.called, "deleted outside the write lock")


if __name__ == "__main__":
    unittest.main()

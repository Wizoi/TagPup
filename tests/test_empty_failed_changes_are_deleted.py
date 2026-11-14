"""The one-off that deletes the failed changes holding nothing (scripts/delete_empty_failed_changes.py, #2996), as the owner
runs it: a dry run that writes nothing, then --apply that deletes exactly those and says how many it wrote (D2).

The empty failed changes are made as the writer made them: a plan whose every file was taken out again, then finished
(file_journal.plan, withdraw, finish: status `failed`). A failed change that still holds a file, or a row of change_rows,
must stay; those two are a failed change as above with a file or a row put in beside it. A change that applied stays.
The script did not exist before this file: each test fails on the trunk before it for want of it.
"""
import contextlib
import io
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import delete_empty_failed_changes  # noqa: E402
import own_home  # noqa: E402

from tagpup.store import db, file_journal, schema  # noqa: E402


def planned_file(name):
    return {"photo_id": None, "path": name, "before": {}, "after": {}}


class EmptyFailedChanges(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="empty_failed_")
        self.path = self.home.library("journal.db")
        schema.ensure(self.path)

    def rows(self, sql):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def failed_change(self, operation, files=1, also=None):
        """A change as a write that wrote nothing left it: every file taken out again, then finished."""
        change_id, rows = file_journal.plan(self.path, operation, [planned_file("p%d.jpg" % n) for n in range(files)],
                                            also=also)
        file_journal.withdraw(self.path, [row.id for row in rows])
        self.assertEqual(file_journal.finish(self.path, change_id), {})
        return change_id

    def run_script(self, apply):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = delete_empty_failed_changes.main(["--library", self.path] + (["--apply"] if apply else []))
        return code, out.getvalue()

    def make_library(self):
        self.empty = [self.failed_change("save photo"), self.failed_change("save photo", files=2),
                      self.failed_change("bake photos")]
        # A failed change that still holds a file, and one that holds a row: put in beside what the writer made.
        self.with_file = self.failed_change("save photo")
        db.write(self.path, lambda: self.insert_file(self.with_file))
        self.with_row = self.failed_change("bulk tags")
        db.write(self.path, lambda: self.insert_row(self.with_row))
        # An applied change (one file stayed done) is not failed.
        change_id, rows = file_journal.plan(self.path, "save photo", [planned_file("kept.jpg")])
        file_journal.mark(self.path, [rows[0].id], "done")
        self.assertEqual(file_journal.finish(self.path, change_id), {"done": 1})
        self.applied = change_id

    def insert_file(self, change_id):
        conn = db.connect(self.path)
        try:
            conn.execute("INSERT INTO change_files (change_id, photo_id, path, fields_before, fields_after, state)"
                         " VALUES (?, NULL, 'x.jpg', '{}', '{}', 'planned')", (change_id,))
            conn.commit()
        finally:
            conn.close()

    def insert_row(self, change_id):
        conn = db.connect(self.path)
        try:
            conn.execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, 'update', 'photos', '1', 'rating', '1', '2')", (change_id,))
            conn.commit()
        finally:
            conn.close()

    def change_ids(self):
        return {row[0] for row in self.rows("SELECT id FROM changes")}

    def test_a_dry_run_counts_them_and_writes_nothing(self):
        self.make_library()
        before = self.change_ids()
        code, said = self.run_script(apply=False)
        self.assertEqual(code, 0)
        self.assertIn("3 empty failed change(s): bake photos 1, save photo 2.", said)
        self.assertEqual(self.change_ids(), before)

    def test_apply_deletes_exactly_the_empty_ones_and_says_what_it_wrote(self):
        self.make_library()
        code, said = self.run_script(apply=True)
        self.assertEqual(code, 0)
        self.assertIn("wrote 3 (deleted bake photos 1, save photo 2).", said)
        self.assertIn("dry run now: 0 left.", said)
        self.assertEqual(self.change_ids(), {self.with_file, self.with_row, self.applied})
        self.assertEqual(self.rows("SELECT COUNT(*) FROM change_files WHERE change_id = %d" % self.with_file), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM change_rows WHERE change_id = %d" % self.with_row), [(1,)])
        _, again = self.run_script(apply=True)
        self.assertIn("wrote 0 (deleted none).", again)

    def test_a_change_that_gained_a_file_before_the_delete_stays(self):
        self.make_library()
        found = file_journal.empty_failed(self.path)
        self.assertEqual(sum(found.values()), 3)
        self.insert_file(self.empty[0])
        self.assertEqual(file_journal.erase_empty_failed(self.path), {"bake photos": 1, "save photo": 1})
        self.assertIn(self.empty[0], self.change_ids())

    def test_no_library_at_the_path_is_refused(self):
        code = delete_empty_failed_changes.run(self.home.library("missing.db"), True, out=lambda line: None)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

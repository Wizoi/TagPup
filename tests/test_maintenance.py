"""The maintenance scaffold: a dry run plans, rehearses and changes nothing; an apply
records one change in the library's journal, copies nothing, writes what the plan said
only while every row is what the plan read, and counts what the writes changed.

tagpup.services.maintenance is what the maintenance operations run on (docs/ARCHITECTURE.md,
phase 7): the scripts it replaced printed what they planned as what they had removed, and six
wrote without a backup (#54). The operations on it are tested through their own services (sync: tests/test_sync.py;
faces from tags: tests/test_faces_from_tags.py).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import maintenance  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="maintenance_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        self.library = Library(self.db)
        schema.ensure(self.db)

    def execute(self, sql, params=()):
        conn = db.connect(self.db)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def backups(self):
        folder = self.library.backups
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class TheScaffold(LibraryCase):
    """run() with an operation of the test's own: it captions photos."""

    def operation(self, size):
        ids = [self.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')",
                            (r"D:\Pictures\Regatta\%d.jpg" % n,)) for n in range(size)]
        self.planned = []

        def plan(library):
            return maintenance.Plan(size=size, counts={"things": size}, ids={"things": ids},
                                    reveal={"names": ["Rowan Thackeray"]}, work=ids)

        def edits(planned):
            self.planned.append(planned.work)
            return [journal.update("photos", (i,), {"captions": "[]"}, {"captions": '["Harbour"]'})
                    for i in planned.work]

        return plan, edits

    def captions(self):
        return [c for (c,) in self.query("SELECT captions FROM photos ORDER BY id")]

    def changes(self):
        return self.query("SELECT id, operation, status FROM changes ORDER BY id")

    def test_a_dry_run_rehearses_and_writes_nothing(self):
        plan, edits = self.operation(3)
        result = maintenance.run(self.library, "test_op", plan, edits)
        self.assertEqual(["[]"] * 3, self.captions())
        self.assertEqual([], self.changes())
        self.assertEqual([], self.backups())
        self.assertEqual((3, 0), (result.attempted, result.changed))
        self.assertTrue(result.details["dry_run"])
        self.assertIsNone(result.details["change"])
        self.assertEqual({"things": 3}, result.details["counts"])
        self.assertEqual({"things": [1, 2, 3]}, result.details["ids"])
        self.assertEqual({"names": ["Rowan Thackeray"]}, result.details["reveal"])
        rehearsal = result.details["rehearsal"]
        self.assertEqual((3, True, True, None), (rehearsal["rows"], rehearsal["exact"], rehearsal["derived_exact"],
                                                 rehearsal["refused"]))

    def test_an_apply_records_one_change_and_copies_nothing(self):
        plan, edits = self.operation(3)
        with mock.patch.object(db, "backup", wraps=db.backup) as backup:
            result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        backup.assert_not_called()
        self.assertEqual([], self.backups())
        self.assertEqual(['["Harbour"]'] * 3, self.captions())
        change = result.details["change"]
        self.assertEqual([(change, "test_op", "applied")], self.changes())
        self.assertEqual([(3,)], self.query("SELECT COUNT(*) FROM change_rows WHERE change_id = ?", (change,)))
        self.assertFalse(result.details["dry_run"])
        self.assertEqual((3, 3), (result.attempted, result.changed))
        self.assertEqual([], maintenance.failed(result))

    def test_changed_is_what_the_write_changed_not_the_plan(self):
        plan, _edits = self.operation(3)
        self.execute("UPDATE photos SET captions = '[\"Harbour\"]' WHERE id = 1")

        def edits(planned):
            # The first already holds what the edit writes: nothing to change there.
            return [journal.update("photos", (i,), {}, {"captions": '["Harbour"]'}) for i in planned.work]

        result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        self.assertEqual((3, 2), (result.attempted, result.changed))
        self.assertEqual([(2,)], self.query("SELECT COUNT(*) FROM change_rows"))

    def test_an_empty_plan_is_neither_rehearsed_nor_written(self):
        plan, edits = self.operation(0)
        for apply in (False, True):
            result = maintenance.run(self.library, "test_op", plan, edits, apply=apply)
            self.assertIsNone(result.details["change"])
            self.assertNotIn("rehearsal", result.details)
        self.assertEqual([], self.planned)
        self.assertEqual([], self.changes())

    def test_a_rehearsal_that_failed_is_said_to_have_failed(self):
        plan, edits = self.operation(2)
        with mock.patch.object(journal, "rehearse", side_effect=sqlite3.OperationalError("database is locked")):
            result = maintenance.run(self.library, "test_op", plan, edits, apply=False)
        self.assertTrue(result.errors)
        said = maintenance.rehearsed(result)
        self.assertIn("database is locked", said)
        self.assertNotIn("Nothing to rehearse", said)

    def test_a_refused_plan_is_neither_rehearsed_nor_written(self):
        def plan(library):
            return maintenance.Plan(size=2, refused="no tree")
        asked = []
        result = maintenance.run(self.library, "test_op", plan, lambda planned: asked.append(planned), apply=True)
        self.assertEqual("no tree", result.refused)
        self.assertEqual([], asked)
        self.assertEqual([], self.changes())

    def test_a_row_changed_since_the_plan_refuses_the_whole_change(self):
        plan, edits = self.operation(3)

        def plan_then_the_app_saves(library):
            planned = plan(library)
            self.execute("UPDATE photos SET captions = '[\"Quay\"]' WHERE id = 2")
            return planned

        for apply in (False, True):
            result = maintenance.run(self.library, "test_op", plan_then_the_app_saves, edits, apply=apply)
            self.assertIn("photos 2 is not what the plan read: captions changed", result.refused)
            self.assertNotIn("Quay", result.refused, "a refusal names columns, never values")
            self.assertEqual(0, result.changed)
            self.assertIsNone(result.details["change"])
        self.assertEqual(["[]", '["Quay"]', "[]"], self.captions())
        self.assertEqual([], self.changes())

    def test_a_failed_rehearsal_write_or_rebuild_is_said_with_what_was_recorded(self):
        """#3: each operation carries its failures in result.errors, as the line a script prints (maintenance.failed), and
        says what was recorded. Every operation runs on this scaffold, so the cases are held here once."""
        plan, edits = self.operation(2)
        with mock.patch.object(journal, "rehearse", side_effect=RuntimeError("disk I/O error")):
            result = maintenance.run(self.library, "test_op", plan, edits)
        self.assertEqual(["FAILED: the rehearsal: RuntimeError: disk I/O error"], maintenance.failed(result))

        with mock.patch.object(journal, "_write", side_effect=sqlite3.OperationalError("disk I/O error")):
            result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        self.assertEqual(["FAILED: the write: OperationalError: disk I/O error"], maintenance.failed(result))
        self.assertEqual(0, result.changed)
        self.assertEqual("Nothing was recorded: no change was written.", maintenance.recorded(result, self.db))
        self.assertEqual((["[]"] * 2, []), (self.captions(), self.changes()))

        with mock.patch.object(journal, "_derive", side_effect=RuntimeError("database is locked")):
            with self.assertLogs("tagpup.store.journal", "ERROR"):
                result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        self.assertEqual(["FAILED: the people and dates of the photos it touched: not rebuilt yet; they are, the next time "
                          "the library is opened"], maintenance.failed(result))
        change = result.details["change"]
        self.assertEqual([(change, "test_op", "derived_pending")], self.changes())
        self.assertEqual(['["Harbour"]'] * 2, self.captions(), "the rows were written; only their derived rows wait")
        self.assertIn("Recorded as change %d" % change, maintenance.recorded(result, self.db))


if __name__ == "__main__":
    unittest.main()

"""A library that still holds rows in `folder_ids` and `library_identity` -- the tables the folder markers
wrote, unused since 2026-10-10 and dropped by a later migration -- opens, syncs, verifies, is reported by the
doctor and has its one old `mark_folders` change listed and undone, as one with no such rows or no such tables
does. Nothing reads or writes those tables, and a `.tagpup` file in a folder is only a file.

The library is made as the roots tests make it (tests/roots_library.py), the rows as the old `folder-ids mark`
wrote them: ids recorded through the journal for the folders, and the identity stamped beside them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import roots_verify, sync  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

LIBRARY_ID = "11111111-2222-4333-8444-555555555555"
FOLDER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000001"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="unused_ids_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=1)
        result = self.side.adopt()
        self.assertIsNone(result.refused, result.refused)
        self.machine = rl.machine()
        self.folder = os.path.join(self.side.pictures, rl.FOLDERS[0])

    def marked(self):
        """The library as the old command left it: an id for a folder, recorded as one journaled change, a
        stamped identity, and the marker file in the folder."""
        applied = journal.apply(self.side.db_path, "mark_folders", [journal.insert(
            "folder_ids", {"id": FOLDER_ID, "path": self.folder, "marked": "2026-10-01 10:00:00"}, kind="marked")],
            {"folders": 1})
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("INSERT INTO library_identity (slot, id, stamped) VALUES (1, ?, '2026-10-01 10:00:00')",
                         (LIBRARY_ID,))
            conn.commit()
        finally:
            conn.close()
        with open(os.path.join(self.folder, ".tagpup"), "w", encoding="utf-8") as handle:
            handle.write(LIBRARY_ID + " " + FOLDER_ID + "\n")
        return applied.change_id

    def query(self, sql):
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def tables(self):
        return (self.query("SELECT id, path, marked FROM folder_ids"),
                self.query("SELECT slot, id, stamped FROM library_identity"))

    def doctor(self):
        said = []
        broken = rl.doctor().report(self.side.db_path, show=0, out=said.append)
        return broken, "\n".join(str(line) for line in said)


class ALibraryThatHoldsRowsInThem(Case):
    def test_it_opens_syncs_verifies_and_is_reported_and_the_rows_are_left_as_they_were(self):
        self.marked()
        before = self.tables()
        self.assertEqual(1, len(before[0]))
        schema.ensure(self.side.db_path)
        result = sync.sync(self.side.library, apply=True, exiftool_path=rl.EXIFTOOL, roots=[self.side.pictures])
        self.assertIsNone(result.refused, result.refused)
        self.assertNotIn("folder_markers", result.details)
        self.assertNotIn("folders_marked", result.details)
        answer = roots_verify.verify(self.side.library, "pictures", self.side.pictures, self.machine)
        self.assertNotIn("markers", answer)
        self.assertEqual(answer["checked"], answer["matches"] + answer["differs"] + answer["missing"]
                         + answer["unreadable"] + answer["unread"])
        broken, report = self.doctor()
        self.assertNotIn("library identity", report)
        self.assertNotIn("folder-ids", report)
        self.assertEqual(before, self.tables())
        self.assertTrue(os.path.exists(os.path.join(self.folder, ".tagpup")), "the marker file is left alone")

    def test_a_folder_renamed_with_a_marker_in_it_is_flagged_missing_and_the_rows_are_left(self):
        self.marked()
        rows = self.query("SELECT id, path FROM photos ORDER BY id")
        renamed = self.folder + " renamed"
        os.rename(self.folder, renamed)
        for name in os.listdir(renamed):
            if name.lower().endswith(".jpg"):
                os.utime(os.path.join(renamed, name), (1_700_000_005, 1_700_000_005))
        result = sync.sync(self.side.library, apply=True, exiftool_path=rl.EXIFTOOL, roots=[self.side.pictures])
        self.assertIsNone(result.refused, result.refused)
        self.assertGreater(result.details["counts"]["missing"], 0)
        self.assertEqual(rows, self.query("SELECT id, path FROM photos ORDER BY id"))
        self.assertEqual(0, self.query("SELECT COUNT(*) FROM changes WHERE operation = 'follow_folder_markers'")[0][0])
        self.assertGreater(result.details["counts"]["missing_folders"], 0)

    def test_the_old_change_is_listed_and_undone_and_takes_its_rows_back(self):
        change = self.marked()
        library = self.side.library
        listed = journal_service.history(library, limit=50)
        self.assertIn("mark_folders", str(listed))
        undone = journal_service.undo(library, change, apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual([], self.query("SELECT id FROM folder_ids"))
        self.assertTrue(os.path.exists(os.path.join(self.folder, ".tagpup")))


class ALibraryWithoutThem(Case):
    def test_the_doctor_and_verify_work_with_the_tables_empty_and_with_them_gone(self):
        self.assertEqual(([], []), self.tables())
        broken, report = self.doctor()
        self.assertNotIn("library identity", report)
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("DROP TABLE folder_ids")
            conn.execute("DROP TABLE library_identity")
            conn.commit()
        finally:
            conn.close()
        again, report = self.doctor()
        self.assertEqual(broken, again)
        self.assertNotIn("library identity", report)
        answer = roots_verify.verify(self.side.library, "pictures", self.side.pictures, self.machine)
        self.assertNotIn("markers", answer)


if __name__ == "__main__":
    unittest.main()

"""A change of photo files whose every file was taken out again is finished, as failed
(docs/findings.md, #274).

A file is withdrawn from its change when its write failed and left it as it was, or when
an earlier file stopped the batch. A process stopped after withdrawing the last of them
and before finishing the change left it `planned` with no files: settling listed only
changes with files, so it stayed planned in the history for good. Rows are made through
the file journal's own store, as a bulk edit plans them; no file is touched.
"""
import os
import socket
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
from tagpup.store import db, file_journal, schema  # noqa: E402

#: A process that has gone: no process has this id.
GONE = "%s:%d" % (socket.gethostname(), 0x7FFFFFF0)


class AChangeEveryFileWasWithdrawnFrom(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="withdrawn_")
        self.library = Library(home.library("files.db"))
        schema.ensure(self.library.path)
        photo = os.path.join(home.root, "a.jpg")
        self.change, rows = file_journal.plan(self.library.path, "add to all selected", [
            {"photo_id": None, "path": photo, "before": {"XMP:Subject": []}, "after": {"XMP:Subject": ["Harbour"]}}])
        # Its one file could not be written and was taken out; then the process stopped.
        file_journal.withdraw(self.library.path, [row.id for row in rows])
        self.set_owner(GONE)

    def set_owner(self, owner):
        conn = db.connect(self.library.path)
        try:
            conn.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, self.change))
            conn.commit()
        finally:
            conn.close()

    def status(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute("SELECT status, owner FROM changes WHERE id = ?", (self.change,)).fetchone()
        finally:
            conn.close()

    def test_is_listed_as_unfinished(self):
        self.assertEqual([self.change], [c.id for c in file_journal.unfinished(self.library.path)])

    def test_is_settled_as_failed(self):
        self.assertEqual(1, file_changes.settle(self.library, None))
        self.assertEqual(("failed", None), self.status())
        self.assertEqual([], file_journal.unfinished(self.library.path))

    def test_is_left_to_a_live_process_still_carrying_it_out(self):
        self.set_owner(file_journal.owner())
        self.assertEqual(0, file_changes.settle(self.library, None))
        self.assertEqual("planned", self.status()[0])


if __name__ == "__main__":
    unittest.main()

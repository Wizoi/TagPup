"""The process carrying out a change of photo files is named by its host, its id and when
it started (docs/findings.md, #275).

Windows reuses a process id soon after the process ends, so a change left by one that
crashed was taken to be carried out by whatever process had its id since -- this one
included -- and was never settled. The start time beside the id tells the two apart.
An owner named the old way, host and id alone, is still read. No file is touched: the
change's one file was taken out again, so settling it is finishing it.
"""
import os
import socket
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
from tagpup.store import db, file_journal, schema  # noqa: E402

HOST = socket.gethostname()


class TheStartOfAProcess(unittest.TestCase):
    def test_is_read_for_this_one_and_the_same_each_time(self):
        started = processes.started(os.getpid())
        self.assertIsInstance(started, int)
        self.assertEqual(started, processes.started(os.getpid()))

    def test_is_none_for_one_that_is_not_running(self):
        self.assertIsNone(processes.started(0x7FFFFFF0))

    def test_names_the_owner(self):
        self.assertEqual("%s:%d:%d" % (HOST, os.getpid(), processes.started(os.getpid())), file_journal.owner())


class AChangeLeftBy(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="owner_")
        self.library = Library(home.library("files.db"))
        schema.ensure(self.library.path)
        self.change, rows = file_journal.plan(self.library.path, "add to all selected", [
            {"photo_id": None, "path": os.path.join(home.root, "a.jpg"), "before": {"XMP:Subject": []},
             "after": {"XMP:Subject": ["Harbour"]}}])
        file_journal.withdraw(self.library.path, [row.id for row in rows])

    def left_by(self, owner):
        conn = db.connect(self.library.path)
        try:
            conn.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, self.change))
            conn.commit()
        finally:
            conn.close()
        return file_changes.settle(self.library, None)

    def test_an_earlier_process_with_this_ones_id_is_settled(self):
        earlier = processes.started(os.getpid()) - 1
        self.assertEqual(1, self.left_by("%s:%d:%d" % (HOST, os.getpid(), earlier)))

    def test_an_earlier_process_with_a_running_ones_id_is_settled(self):
        running = os.getppid()
        started = processes.started(running)
        self.assertIsNotNone(started, "the parent of this test is running")
        self.assertEqual(1, self.left_by("%s:%d:%d" % (HOST, running, started - 1)))

    def test_this_process_is_left_to_it(self):
        self.assertEqual(0, self.left_by(file_journal.owner()))

    def test_a_running_process_is_left_to_it(self):
        running = os.getppid()
        self.assertEqual(0, self.left_by("%s:%d:%d" % (HOST, running, processes.started(running))))

    def test_an_owner_named_the_old_way_is_read(self):
        self.assertEqual(0, self.left_by("%s:%d" % (HOST, os.getpid())))
        self.assertEqual(1, self.left_by("%s:%d" % (HOST, 0x7FFFFFF0)))

    def test_another_machines_is_left_to_it(self):
        self.assertEqual(0, self.left_by("another-machine:4242:1234567"))


if __name__ == "__main__":
    unittest.main()

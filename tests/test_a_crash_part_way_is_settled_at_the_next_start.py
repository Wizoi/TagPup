"""A crash part-way through a write is finished by the next start (scenario D3).

The owner's server dies between a photo file being written and its change being marked done, and, in the same
minute, a change of rows dies between its commit and the rebuild of the derived rows (`derived_pending`). The next
start finishes both: the file change by what the files hold, the rows by rebuilding. Windows has meanwhile given
the dead process's id to the process that starts: a change owned by "host:pid:start" with an earlier start is not
that process's, and is settled; one owned by this very process is left to it.

Lifted: test_a_changes_owner_is_known_by_its_start (#275) and test_a_withdrawn_change_is_finished (#274). Break that
fails the kept tests: tagpup.store.file_journal.owner_alive ignoring the start time (the start read as None) fails
test_the_next_start_finishes_what_the_crash_left_also_when_windows_reused_the_process_id. The start-less owner
("host:pid", the old form) is not tested: the live libraries hold none (0 of 3, counted 2026-10-15), and the one writer
that makes it is owner() when the OS cannot read a start (finding, not tested).
"""
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402
from test_file_journal import Crash, FilesCase, crash_at, keywords_of, requires_exiftool  # noqa: E402

from tagpup import runtime  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
from tagpup.store import db, file_journal, journal, people, schema  # noqa: E402

HOST = socket.gethostname()
#: A process that has gone: no process has this id.
GONE = "%s:%d:1" % (HOST, 0x7FFFFFF0)


@requires_exiftool
class ACrashAndTheNextStart(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make("a.jpg"), self.make("b.jpg")
        self.c = os.path.join(self.folder, "c.jpg")
        conn = db.connect(self.library.path)
        try:
            self.c_id = photo_rows.add_read(conn, self.c, {"XMP:Subject": ["Beach"]})
            self.face = add_face(conn, self.c, name="Maren Oakhollow")
            people.rebuild_photos(conn, [self.c])
            conn.commit()
        finally:
            conn.close()

    def listed(self):
        return self.rows("SELECT name FROM photo_people WHERE photo_id = ?", (self.c_id,))

    def owned_by(self, owner, change):
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, change))

    def start_again(self):
        """What the next process does with the library: opens it (rows settled), reads its settings (files settled)."""
        journal._settled.clear()
        schema._current.clear()
        schema.ensure(self.library.path)
        with mock.patch.object(file_changes, "_settled", set()):
            runtime.library_settings(self.library)

    def test_the_next_start_finishes_what_the_crash_left_also_when_windows_reused_the_process_id(self):
        # The server dies after writing a file and before marking it done, ...
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([self.a, self.b])
        files = self.last_change()
        self.assertEqual(["writing", "planned"], self.states(files))
        # ... and after committing a change of rows, before its derived rows are rebuilt.
        with mock.patch.object(journal, "_reached", side_effect=crash_at("forward committed")):
            with self.assertRaises(Crash):
                journal.apply(self.library.path, "delete a face", [journal.delete(
                    "faces", (self.face,), {"name": "Maren Oakhollow"})])
        rows = self.last_change()
        self.assertEqual("derived_pending", self.status(rows))
        self.assertEqual([("Maren Oakhollow",)], self.listed(), "the photo's people are not rebuilt yet")
        # A live process's change is left to it; the process id is this one's but the start is an earlier one's.
        self.owned_by(file_journal.owner(), files)
        self.assertEqual(0, self.settle())
        earlier = processes.started(os.getpid()) - 1
        self.owned_by("%s:%d:%d" % (HOST, os.getpid(), earlier), files)
        self.start_again()
        self.assertEqual(("applied", ["done", "done"]), (self.status(files), self.states(files)))
        for path in (self.a, self.b):
            self.assertEqual((["Beach", "Harbour"], ["Beach", "Harbour"]), (keywords_of(path), self.indexed(path)), path)
        self.assertEqual("applied", self.status(rows))
        self.assertEqual([], self.listed())

    def test_a_running_process_that_took_the_id_is_not_the_owner_and_another_machines_is_left_to_it(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("plan committed")):
            with self.assertRaises(Crash):
                self.add([self.a])
        change = self.last_change()
        running = os.getppid()
        self.owned_by("%s:%d:%d" % (HOST, running, processes.started(running)), change)
        self.assertEqual(0, self.settle(), "the live parent of this test is running and holds it")
        self.owned_by("another-machine:4242:1234567", change)
        self.assertEqual(0, self.settle())
        self.owned_by("%s:%d:%d" % (HOST, running, processes.started(running) - 1), change)
        self.assertEqual(1, self.settle(), "an earlier process with a running one's id")
        self.assertEqual("applied", self.status(change))

    def test_a_change_every_file_was_withdrawn_from_is_finished_as_failed(self):
        # Its one file could not be written and was taken out; then the process stopped, leaving it planned with no files.
        change, planned = file_journal.plan(self.library.path, "add to all selected", [
            {"photo_id": None, "path": self.a, "before": {"XMP:Subject": []}, "after": {"XMP:Subject": ["Harbour"]}}])
        file_journal.withdraw(self.library.path, [row.id for row in planned])
        self.owned_by(GONE, change)
        self.assertEqual([change], [c.id for c in file_journal.unfinished(self.library.path)])
        self.assertEqual(1, self.settle())
        self.assertEqual("failed", self.status(change))
        self.assertEqual([], file_journal.unfinished(self.library.path))


if __name__ == "__main__":
    unittest.main()

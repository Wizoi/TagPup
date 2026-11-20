"""A library view's bulk Delete stopped part way: by the owner, by an update, by a kill (scenario F8; docs/plans/one-write-path.md,
cut 1b).

The owner selects thirty photos and clicks Delete. The whole selection is ONE change of the file journal (file_journal.DELETE,
internal: History never lists it), carried out a chunk of batches.CHUNK photos at a time with the lock let go between chunks
(tagpup.services.photos.delete_in_chunks). Stopped at a chunk:
- by the owner's Cancel: the photos not reached are taken out of the change, nothing of them deleted, each told so, and nothing
  is left planned;
- by an update handing over to a new version (batches.stop_all): they are left planned, the change released with how many in its
  summary, each told it is deleted when TagPup starts again; the new version's settle deletes them, once -- only while each is
  the file the Delete looked at (another put at its path is left, its row kept); a settle while this process is still handing
  over leaves them, and one gone by then (removed, its folder renamed, its drive not connected) or unreadable is never
  deleted nor forgotten: it was never begun, so gone is not the Delete's doing (#3266); Cancel and an update in one chunk are
  the owner's Cancel (#3268); a kill during that resume leaves the rest withdrawn (#3271);
- by a kill (a process that has gone, no `finally` run): the next settle withdraws what was planned (the owner's answer 4: a
  Delete is never resumed after an unclean stop) and finishes the one it was sending; nothing more is deleted.

Fails on the old code: each photo was a change of its own, Deleted at once or not at all, so an update's stop left nothing for
the next start (test_an_update_stops_it_and_the_next_start_deletes_the_rest_once) and Cancel's sentence was bulk_edit's own.

The Recycle Bin is the test's (send_to_recycle_bin stood in for). Photos are real small files and rows as the indexer records them
(tests/photo_rows.py through ViewLibrary). Fictional names only.
"""
import json
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from test_bulk_delete import Delete  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import batches, bulk_edit, file_changes  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import file_journal  # noqa: E402

#: A process that has gone: no process has this id.
GONE = "%s:%d:1" % (socket.gethostname(), 0x7FFFFFF0)


class Crash(BaseException):
    """The process stopping at a step: not an Exception, so nothing catches it."""


class ABulkDeleteStoppedPartWay(Delete):
    def setUp(self):
        super().setUp()
        batches.reopen()
        self.addCleanup(batches.reopen)
        self.files_of = {photo_id: self.vl.path_of(photo_id) for photo_id in self.coast}

    def stop_at(self, sent, stop):
        """Ask `stop` just before the Bin is sent its `sent`-th photo."""
        self.gate = lambda: stop() if len(self.sent) == sent - 1 else None

    def changes(self):
        return self.vl.rows("SELECT id, owner, summary FROM changes WHERE operation = ?", file_journal.DELETE)

    def planned(self):
        return self.vl.rows("SELECT path FROM change_files WHERE state = 'planned'")

    def settle(self):
        return file_changes.settle(self.library, None)

    def cancel(self):
        [running] = batches.listing(self.library)
        self.assertTrue(batches.cancel(self.library, running["ticket"]))

    def test_cancel_at_a_chunk_deletes_the_chunk_and_leaves_the_rest_with_nothing_planned(self):
        self.stop_at(10, self.cancel)
        done = self.start({"ids": self.coast})
        self.assertEqual((batches.CHUNK, 30 - batches.CHUNK), (done["changed"], done["error_count"]))
        self.assertEqual({photo_actions.STOPPED_NOT_DELETED}, {error["why"] for error in done["errors"]})
        self.assertEqual(self.coast[batches.CHUNK:], self.held(self.coast), "the first chunk went, the rest are kept")
        self.assertTrue(all(os.path.exists(self.vl.path_of(photo_id)) for photo_id in self.coast[batches.CHUNK:]))
        self.assertEqual(([], []), (self.changes(), self.planned()), "nothing is left for a settle to resume")
        self.assertEqual(0, self.settle())

    def test_an_update_stops_it_and_the_next_start_deletes_the_rest_once(self):
        self.stop_at(10, batches.stop_all)
        done = self.start({"ids": self.coast})
        left = 30 - batches.CHUNK
        self.assertEqual((batches.CHUNK, left), (done["changed"], done["error_count"]))
        self.assertEqual({photo_actions.DELETED_WHEN_TAGPUP_STARTS}, {error["why"] for error in done["errors"]})
        [(_change_id, owner, summary)] = self.changes()
        self.assertIsNone(owner, "released for the next start")
        self.assertEqual(left, json.loads(summary)[photo_actions.LEFT_FOR_THE_NEXT_START])
        self.assertEqual(left, len(self.planned()))
        self.assertEqual(self.coast[batches.CHUNK:], self.held(self.coast), "nothing deleted until the next start")

        self.assertEqual(0, self.settle(), "a settle of the process still handing over leaves them")
        self.assertEqual((30 - left, left), (len(self.sent), len(self.planned())))

        batches.reopen()        # the new version
        with self.assertLogs(photo_actions.logger, "INFO") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("deleting the %d photo(s) a Delete stopped for an update left" % left, "\n".join(said.output))
        self.assertEqual([], self.held(self.coast))
        self.assertFalse(any(os.path.exists(path) for path in self.files_of.values()))
        self.assertEqual(([], []), (self.changes(), self.planned()))
        self.assertEqual(0, self.settle(), "once")
        self.assertEqual(30, len({paths.key(path) for path in self.sent}))
        self.assertEqual(30, len(self.sent), "each photo sent to the Bin once")

    def test_a_photo_changed_before_the_next_start_is_not_deleted_and_keeps_its_row(self):
        self.stop_at(10, batches.stop_all)
        self.start({"ids": self.coast})
        changed = self.vl.path_of(self.coast[-1])
        with open(changed, "ab") as handle:
            handle.write(b"\0" * 64)        # another program wrote it while TagPup was closed: not the file the Delete looked at
        batches.reopen()
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("1 photo(s) it was deleting had another file put at their path", "\n".join(said.output))
        self.assertEqual([self.coast[-1]], self.held(self.coast))
        self.assertTrue(os.path.exists(changed))
        self.assertNotIn(paths.key(changed), {paths.key(path) for path in self.sent})
        self.assertEqual([], self.changes())

    def test_a_photo_gone_or_moved_before_the_next_start_keeps_its_row_and_faces(self):
        chosen = self.coast + self.lakes
        self.stop_at(10, batches.stop_all)
        self.start({"ids": chosen})
        self.assertEqual(chosen[batches.CHUNK:], self.held(chosen), "the first chunk went")
        removed, lakes = self.vl.path_of(self.coast[-1]), os.path.dirname(self.vl.path_of(self.lakes[0]))
        for photo_id in (self.coast[-1], self.lakes[0]):
            face_rows.add_face(self.vl.conn, self.vl.path_of(photo_id))
        self.vl.conn.commit()
        # While TagPup was closed: one file removed, and the folder of five renamed in Explorer (or its drive not connected).
        os.remove(removed)
        os.rename(lakes, lakes + " renamed")
        batches.reopen()
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("6 photo(s) it was to delete were not found or could not be read", "\n".join(said.output))
        self.assertEqual([self.coast[-1]] + self.lakes, self.held(chosen), "never begun, so never forgotten")
        self.assertEqual(2, len(self.vl.rows("SELECT id FROM faces")), "their faces kept")
        self.assertEqual(len(chosen) - 6, len(self.sent), "the others deleted")
        self.assertEqual(([], []), (self.changes(), self.planned()))

    def test_a_photo_whose_share_does_not_answer_at_the_next_start_is_kept(self):
        self.stop_at(10, batches.stop_all)
        self.start({"ids": self.coast})
        away, real = paths.key(self.vl.path_of(self.coast[-1])), shares.stamp_of
        batches.reopen()
        with mock.patch.object(shares, "stamp_of", lambda path: shares.UNANSWERED if paths.key(path) == away else real(path)), \
                self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("1 photo(s) it was to delete were on a share that did not answer", "\n".join(said.output))
        self.assertEqual([self.coast[-1]], self.held(self.coast))
        self.assertEqual(29, len(self.sent))
        self.assertEqual(([], []), (self.changes(), self.planned()), "the count was taken off: not left for another start")

    def test_cancel_and_an_update_in_one_chunk_is_the_owners_cancel(self):
        self.stop_at(10, lambda: (self.cancel(), batches.stop_all()))
        done = self.start({"ids": self.coast})
        self.assertEqual((batches.CHUNK, 30 - batches.CHUNK), (done["changed"], done["error_count"]))
        self.assertEqual({photo_actions.STOPPED_NOT_DELETED}, {error["why"] for error in done["errors"]})
        self.assertEqual(([], []), (self.changes(), self.planned()), "the cancelled photos are withdrawn, not left planned")
        batches.reopen()
        self.assertEqual(0, self.settle())
        self.assertEqual(self.coast[batches.CHUNK:], self.held(self.coast), "nothing more deleted at the next start")

    def test_an_update_and_then_the_cancel_in_one_chunk_is_the_owners_cancel(self):
        self.stop_at(10, lambda: (batches.stop_all(), self.cancel()))
        done = self.start({"ids": self.coast})
        self.assertEqual((batches.CHUNK, 30 - batches.CHUNK), (done["changed"], done["error_count"]))
        self.assertEqual({photo_actions.STOPPED_NOT_DELETED}, {error["why"] for error in done["errors"]})
        self.assertEqual(([], []), (self.changes(), self.planned()), "the cancelled photos are withdrawn, not left planned")
        batches.reopen()
        self.assertEqual(0, self.settle())
        self.assertEqual(self.coast[batches.CHUNK:], self.held(self.coast), "nothing more deleted at the next start")

    def test_killed_during_the_resume_the_next_start_deletes_nothing_more(self):
        self.stop_at(10, batches.stop_all)
        self.start({"ids": self.coast})
        batches.reopen()
        count = []

        def reached(step):
            if step == "delete sent":
                count.append(step)
                if len(count) == 3:
                    raise Crash(step)
        with mock.patch.object(photo_actions, "_reached", side_effect=reached):
            with self.assertRaises(Crash):
                self.settle()
        self.vl.conn.execute("UPDATE changes SET owner = ?", (GONE,))
        self.vl.conn.commit()
        sent = batches.CHUNK + 3
        self.assertEqual(sent, len(self.sent))
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("%d photo(s) were still to be deleted when TagPup stopped" % (30 - sent), "\n".join(said.output))
        self.assertEqual(sent, len(self.sent), "nothing more deleted")
        self.assertEqual(self.coast[sent:], self.held(self.coast), "the one it was sending is forgotten, the rest kept")
        self.assertEqual(([], []), (self.changes(), self.planned()))

    def test_a_photo_that_cannot_be_read_is_its_own_error_and_the_rest_go(self):
        unreadable, real = paths.key(self.vl.path_of(self.coast[1])), shares.stamp_of
        with mock.patch.object(shares, "stamp_of",
                               lambda path: shares.CANNOT_READ if paths.key(path) == unreadable else real(path)):
            done = self.start({"ids": self.coast[:3]})
        self.assertEqual((2, 1), (done["changed"], done["error_count"]))
        self.assertEqual(photo_actions.NOTHING_DELETED_NO_LOOK, done["errors"][0]["why"])
        self.assertEqual([self.coast[1]], self.held(self.coast[:3]))
        self.assertEqual([], self.changes())

    def test_killed_part_way_the_next_start_deletes_nothing_more(self):
        count = []

        def reached(step):
            if step == "delete sent":
                count.append(step)
                if len(count) == 3:
                    raise Crash(step)
        # A kill runs no `finally`: end_deletes never runs, the change stays this (now gone) process's.
        with mock.patch.object(photo_actions, "_reached", side_effect=reached), \
                mock.patch.object(photo_actions, "end_deletes"):
            with self.assertRaises(Crash):
                bulk_edit.run(self.library, bulk_edit.Edit(bulk_edit.DELETE, token="asked"), self.coast, None)
        self.vl.conn.execute("UPDATE changes SET owner = ?", (GONE,))
        self.vl.conn.commit()
        self.assertEqual(3, len(self.sent))
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("%d photo(s) were still to be deleted when TagPup stopped" % 27, said.output[0])
        self.assertEqual(3, len(self.sent), "nothing more deleted")
        self.assertEqual(self.coast[3:], self.held(self.coast), "the one it was sending is forgotten, the planned ones kept")
        self.assertEqual(([], []), (self.changes(), self.planned()))

    def test_a_photo_whose_share_did_not_answer_at_the_start_is_not_planned_and_promised_nothing(self):
        away, real = paths.key(self.vl.path_of(self.coast[1])), shares.stamp_of
        with mock.patch.object(shares, "stamp_of", lambda path: shares.UNANSWERED if paths.key(path) == away else real(path)):
            done = self.start({"ids": self.coast[:3]})
        self.assertEqual((2, 1), (done["changed"], done["error_count"]))
        self.assertEqual(file_changes.AWAY_WHY, done["errors"][0]["why"])
        self.assertEqual([self.coast[1]], self.held(self.coast[:3]))
        self.assertEqual([], self.changes(), "not in the change: nothing left for the next start")


if __name__ == "__main__":
    unittest.main()

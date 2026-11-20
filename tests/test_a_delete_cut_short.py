"""A Delete cut short is finished, or left alone, by the next settle (scenario F8; docs/plans/one-write-path.md, cut 1a).

The owner clicks Delete on a photo of a folder the library holds. The Delete is ONE change of the file journal
(file_journal.DELETE, internal: History never lists it, Undo says the Recycle Bin is the way back), planned, its file
marked `writing` as the Bin step begins, and marked done in the transaction that removes the photo's rows. It runs under
the lock of changes of photo files, so a Delete waits for a chunk of a write under way. The server is killed between the
Bin and the rows, or between the Bin's copy (a share with no Bin goes through this PC) and the original's removal; or a
share stops answering part-way; or an update stops a Delete it had planned and not begun. The next settle looks at each
file the Delete left `writing`: gone, its rows go; there, it is sent again; on a share that does not answer, it waits. A
Delete planned and never begun is withdrawn, nothing deleted (the owner's answer 4), whoever released it: a settle stopped
by an update releases one a dead process planned, as a clean stop would (#3256; a bulk Delete's resume after a clean stop,
by a count of its own in the change: test_a_bulk_delete_stopped_part_way). A file left `writing` is sent again only while it is the file the Delete stamped:
another put at its path meanwhile is withdrawn, its row and faces kept (#3257). A Delete the share stopped answering for
says, in the route's reply the page shows, that it is done when the share answers (#3258). A photo of a folder the library
does not hold (Just look) has no record.

Fails on the old code: tagpup.services.photos had no `_reached`, no plan and no lock -- the kill tests stop nowhere, a
Delete beside a held lock runs at once (test_a_delete_waits_for_the_chunk_that_holds_the_lock), and a Delete whose rows
step failed left the rows of a photo the Bin holds with nothing to finish it (test_killed_after_the_bin_...).

The Recycle Bin is the test's (send_to_recycle_bin stood in for), and the Downloads folder the test home's.
"""
import os
import socket
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import batches, file_changes  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db, file_journal, journal  # noqa: E402

#: A process that has gone: no process has this id.
GONE = "%s:%d:1" % (socket.gethostname(), 0x7FFFFFF0)


class Crash(BaseException):
    """The process stopping at a step: not an Exception, so nothing catches it."""


def crash_at(step):
    def reached(name):
        if name == step:
            raise Crash(step)
    return reached


class ADeleteCutShort(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.library = self.vl.library
        self.share = os.path.join(self.vl.pictures, "Share")
        self.binned = []
        self.crash_in_bin = False
        self.share_gone = False
        stamp_of = shares.stamp_of
        # A share that stops answering does so for every look from then on (away()).
        patcher = mock.patch.object(shares, "stamp_of",
                                    side_effect=lambda path: shares.UNANSWERED if self.share_gone else stamp_of(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, name, side in ((recycle_bin, "send_to_recycle_bin", self.recycle),
                                   (recycle_bin, "no_bin_reason", self.reason)):
            patcher = mock.patch.object(target, name, side_effect=side)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.local_id = self.vl.photo("Local", "IMG_0001.jpg", taken="2024:02:01 10:00:00", real=True, size=(16, 16))
        self.local = self.vl.path_of(self.local_id)
        self.shared_id = self.vl.photo("Share", "IMG_0002.jpg", taken="2024:02:02 10:00:00", real=True, size=(16, 16))
        self.shared = self.vl.path_of(self.shared_id)

    # ---- the test's Bin and journal ----------------------------------------------------------

    def reason(self, path):
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.share)) else None

    def recycle(self, path):
        self.binned.append(path)
        os.remove(path)
        if self.crash_in_bin:
            raise Crash("the copy is in the Bin")
        return True

    def away(self, path):
        self.share_gone = True
        raise OSError(64, "The specified network name is no longer available")

    def changes(self):
        return self.vl.rows("SELECT id, operation, owner FROM changes WHERE operation = ? ORDER BY id",
                            file_journal.DELETE)

    def files(self):
        return self.vl.rows("SELECT state FROM change_files ORDER BY id")

    def held(self, photo_id):
        return bool(self.vl.rows("SELECT 1 FROM photos WHERE id = ?", photo_id))

    def died(self):
        self.vl.conn.execute("UPDATE changes SET owner = ?", (GONE,))
        self.vl.conn.commit()

    def settle(self):
        return file_changes.settle(self.library, None)

    def delete(self, path):
        return photo_actions.delete(self.library, path, None)

    # ---- the scenarios ----------------------------------------------------------------------

    def test_a_delete_done_leaves_no_record_and_history_lists_nothing(self):
        result = self.delete(self.local)
        self.assertTrue(result.ok, result.message())
        self.assertEqual((1, 0, 1), (result.changed, result.details["file_only"], result.details["with_rows"]))
        self.assertFalse(os.path.exists(self.local))
        self.assertFalse(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))

    def test_killed_after_the_bin_and_before_the_rows_the_next_settle_forgets_the_photo(self):
        with mock.patch.object(photo_actions, "_reached", side_effect=crash_at("delete sent")):
            with self.assertRaises(Crash):
                self.delete(self.local)
        self.assertFalse(os.path.exists(self.local))
        self.assertTrue(self.held(self.local_id), "the rows are still there")
        self.assertEqual(["writing"], [state for (state,) in self.files()])
        self.died()
        self.assertEqual(1, self.settle())
        self.assertFalse(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))
        self.assertEqual([self.local], self.binned, "sent once")

    def test_a_delete_of_a_photo_a_dead_process_left_writing_finishes_that_change_first_and_leaves_nothing_behind(self):
        # #3263: the Delete settles before it plans, so a change a dead process left on the photo is not planned over.
        with mock.patch.object(photo_actions, "_reached", side_effect=crash_at("delete writing")):
            with self.assertRaises(Crash):
                self.delete(self.local)
        self.assertEqual(["writing"], [state for (state,) in self.files()])
        self.died()
        result = self.delete(self.local)
        self.assertFalse(result.ok, "the first Delete's settle sent it: the second finds it gone")
        self.assertEqual(0, result.changed)
        self.assertEqual([self.local], self.binned, "sent once")
        self.assertFalse(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()), "the dead change is finished, not left beside a new one")

    def test_killed_after_the_copy_is_in_the_bin_and_before_the_original_goes_it_is_sent_again(self):
        self.crash_in_bin = True
        with self.assertRaises(Crash):
            self.delete(self.shared)
        self.assertTrue(os.path.exists(self.shared), "the original is still on the share")
        self.assertEqual(["writing"], [state for (state,) in self.files()])
        self.crash_in_bin = False
        self.died()
        self.assertEqual(1, self.settle())
        self.assertFalse(os.path.exists(self.shared))
        self.assertFalse(self.held(self.shared_id))
        self.assertEqual(([], []), (self.changes(), self.files()))
        self.assertEqual(2, len(self.binned), "a copy each time: the Bin holds the photo twice, never not at all")

    def test_planned_and_never_begun_then_an_unclean_stop_is_withdrawn_and_nothing_is_deleted(self):
        with mock.patch.object(photo_actions, "_reached", side_effect=crash_at("delete planned")):
            with self.assertRaises(Crash):
                self.delete(self.local)
        self.assertEqual(["planned"], [state for (state,) in self.files()])
        self.died()
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("1 photo(s) were still to be deleted", said.output[0])
        self.assertNotIn(os.path.basename(self.local), said.output[0], "a count, no path")
        self.assertTrue(os.path.exists(self.local))
        self.assertTrue(self.held(self.local_id))
        self.assertEqual(([], [], []), (self.changes(), self.files(), self.binned))

    def test_planned_by_a_process_that_died_then_put_aside_by_a_stopped_settle_is_withdrawn_not_deleted(self):
        # A process planned the Delete and died. This one's settle took the change (its owner overwritten) and an update
        # stopped it before it began: put aside, released, its owner NULL as a clean stop would leave it. Never begun, it
        # is withdrawn at the next start, nothing deleted.
        with mock.patch.object(photo_actions, "_reached", side_effect=crash_at("delete planned")):
            with self.assertRaises(Crash):
                self.delete(self.local)
        self.died()
        self.addCleanup(batches.reopen)
        batches.stop_all()
        thread = file_changes._settle_on_a_thread(self.library, None)
        thread.join(30)
        batches.reopen()
        [(change_id, _operation, owner)] = self.changes()
        self.assertIsNone(owner, "put aside: released, as a clean stop would leave it")
        # The next start: what this process put off is the old process's, gone with it.
        mine = (self.library.key, change_id)
        file_changes._put_off.discard(mine)
        for key in [key for key, whose in file_changes._unsettled.items() if whose == mine]:
            del file_changes._unsettled[key]
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("1 photo(s) were still to be deleted", said.output[0])
        self.assertTrue(os.path.exists(self.local))
        self.assertTrue(self.held(self.local_id))
        self.assertEqual(([], [], []), (self.changes(), self.files(), self.binned))

    def test_killed_after_the_bin_then_another_file_put_at_its_path_is_not_deleted_and_the_row_keeps_its_faces(self):
        face_rows.add_face(self.vl.conn, self.local)
        self.vl.conn.commit()
        with mock.patch.object(photo_actions, "_reached", side_effect=crash_at("delete sent")):
            with self.assertRaises(Crash):
                self.delete(self.local)
        self.assertFalse(os.path.exists(self.local))
        # While TagPup was down, another photo was saved under that name.
        make_jpeg(self.local, shade=(200, 40, 40), size=(24, 24))
        self.died()
        with self.assertLogs(photo_actions.logger, "WARNING") as said:
            self.assertEqual(1, self.settle())
        self.assertIn("1 photo(s) it was deleting had another file put at their path", said.output[0])
        self.assertNotIn(os.path.basename(self.local), said.output[0], "a count, no path")
        self.assertTrue(os.path.exists(self.local), "the other photo is not sent to the Bin")
        self.assertTrue(self.held(self.local_id))
        self.assertEqual([(1,)], self.vl.rows("SELECT COUNT(*) FROM faces WHERE photo_id = ?", self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))
        self.assertEqual([self.local], self.binned, "the first photo only")

    def test_a_delete_waits_for_the_chunk_that_holds_the_lock(self):
        done = []
        with file_changes.exclusively():
            worker = threading.Thread(target=lambda: done.append(self.delete(self.local)))
            worker.start()
            waited = threading.Event()
            while not file_changes._waiting and worker.is_alive():
                waited.wait(0.01)
            self.assertEqual(1, file_changes._waiting, "the Delete waits for the lock")
            self.assertTrue(os.path.exists(self.local), "nothing is deleted while the chunk holds the lock")
            self.assertEqual([], self.changes())
        worker.join(30)
        self.assertTrue(done and done[0].ok)
        self.assertFalse(os.path.exists(self.local))
        self.assertFalse(self.held(self.local_id))

    def test_a_share_that_stops_answering_leaves_the_delete_writing_until_it_answers(self):
        with mock.patch.object(recycle_bin, "send_to_recycle_bin", side_effect=self.away):
            result = self.delete(self.local)
        self.assertFalse(result.ok)
        self.assertEqual(0, result.changed)
        self.assertIn("is deleted when the share answers", result.message())
        [(change_id, operation, owner)] = self.changes()
        self.assertEqual((file_journal.DELETE, None), (operation, owner), "released, for a settle")
        self.assertEqual(["writing"], [state for (state,) in self.files()])
        # History lists nothing; Undo says the way back is the Recycle Bin.
        self.assertEqual([], [entry for entry in journal.history(self.library.path)
                              if entry["operation"] == file_journal.DELETE])
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            self.assertIn("restore the photo from the Recycle Bin", journal_service.why_not(conn, change_id))
        finally:
            conn.close()
        # Still away: the settle waits, nothing decided.
        self.assertEqual(0, self.settle())
        self.assertEqual(["writing"], [state for (state,) in self.files()])
        # It answers: the photo is there, the file the Delete stamped, and is sent to the Bin.
        self.share_gone = False
        self.assertEqual(1, self.settle())
        self.assertFalse(os.path.exists(self.local))
        self.assertFalse(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))

    def test_a_delete_whose_file_could_not_be_looked_at_sends_nothing_and_promises_nothing(self):
        # No look, no stamp: a settle could not tell this file from another put at its path, so nothing is sent.
        with mock.patch.object(shares, "stamp_of", return_value=shares.UNANSWERED), \
                mock.patch.object(recycle_bin, "send_to_recycle_bin") as sent:
            result = self.delete(self.local)
        sent.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("nothing was deleted", result.message())
        self.assertNotIn("is deleted when the share answers", result.message())
        self.assertTrue(os.path.exists(self.local))
        self.assertTrue(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()), "no record is left to settle")

    def test_the_page_is_told_a_delete_the_share_did_not_answer_for_is_done_when_it_answers(self):
        # The route's reply is what the page's alert shows ("Error deleting photo: " + its error; web/tagpup/photo.js).
        with mock.patch.object(recycle_bin, "send_to_recycle_bin", side_effect=self.away):
            reply = self.app.test_client().post("/library/api/photo/delete", json={"path": self.local})
        body = reply.get_json()
        self.assertFalse(body["success"])
        self.assertIn("is deleted when the share answers", body["error"])
        self.assertTrue(os.path.exists(self.local))
        self.assertEqual(["writing"], [state for (state,) in self.files()])

    def test_a_bin_that_refuses_keeps_the_photo_and_leaves_no_record(self):
        def held_open(path):
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")

        with mock.patch.object(recycle_bin, "send_to_recycle_bin", side_effect=held_open):
            result = self.delete(self.local)
        self.assertFalse(result.ok)
        self.assertTrue(os.path.exists(self.local))
        self.assertTrue(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))

    def test_two_deletes_of_one_photo_at_once_delete_it_once(self):
        results = []
        workers = [threading.Thread(target=lambda: results.append(self.delete(self.local))) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
        self.assertEqual([0, 1], sorted(result.changed for result in results))
        self.assertEqual([self.local], self.binned)
        self.assertFalse(self.held(self.local_id))
        self.assertEqual(([], []), (self.changes(), self.files()))

    def test_a_just_look_photo_is_deleted_with_no_record(self):
        loose = os.path.join(self.home.root, "elsewhere", "IMG_0003.jpg")
        make_jpeg(loose, size=(16, 16))
        result = self.delete(loose)
        self.assertTrue(result.ok, result.message())
        self.assertEqual((1, 1, 0), (result.changed, result.details["file_only"], result.details["with_rows"]))
        self.assertFalse(os.path.exists(loose))
        self.assertEqual([], self.changes())


if __name__ == "__main__":
    unittest.main()

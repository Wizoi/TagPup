"""A long change of photo files killed part-way (a console window closed on an hours-long tag rewrite or caption sweep)
is settled by whatever opens the library next a chunk at a time, as a write is carried out: a save that waits gets the
lock between two chunks, Cancel stops it at the next chunk with the rest left planned for the next start, a line says
how far it has got, and no file is written twice (tagpup.services.file_changes.settle). A save or a rename let in between
two chunks leaves a photo the settle has still to write alone, saying why, so that the cut-off edit is not lost on it as
a conflict; a settle that cannot take its turn (a batch running, a hand-over) is left whole for the next.

Real JPEGs written by the real ExifTool in a home of the test's own; rows as the indexer records a read
(photo_rows.add_read, through FilesCase.make); the kill is a BaseException at a step of the write (crash_at), with the
change's owner a process of this machine that has ended. Chunks of two (batches.CHUNK) over twelve photos. Each test
fails on the settle before it, which took every file in one go under the lock with no stop and no line.
"""
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, keywords_of, requires_exiftool  # noqa: E402

from tagpup.files import names  # noqa: E402
from tagpup.services import batches, file_changes, tagging  # noqa: E402

GONE = "%s:4000000:1" % socket.gethostname()      # a process of this machine that has ended
PHOTOS = 12


def wait_until(check, what):
    deadline = time.monotonic() + 30
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("never happened: %s" % what)
        threading.Event().wait(0.01)


@requires_exiftool
class ALongChangeCutOff(FilesCase):
    def setUp(self):
        super().setUp()
        chunk = mock.patch.object(batches, "CHUNK", 2)
        chunk.start()
        self.addCleanup(chunk.stop)
        # Each test is a fresh process: no settle run yet, none put off.
        for kept in (file_changes._settled, file_changes._put_off, file_changes._unsettled):
            saved = kept.copy()
            kept.clear()
            self.addCleanup(kept.update, saved)
        self.photos = [self.make("photo %02d.jpg" % n) for n in range(PHOTOS)]
        self.other = self.make("not in the change.jpg")
        # Killed as the third file is written, before its row: two done, one written, nine never reached.
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 3)):
            with self.assertRaises(Crash):
                self.add(self.photos)
        self.change = self.last_change()
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, self.change))
        self.assertEqual(["done"] * 2 + ["writing"] + ["planned"] * 9, self.states(self.change))
        self.stamps = {path: os.stat(path).st_mtime_ns for path in self.photos[:3]}

    def open_library(self, step=None):
        """What the server's first request does (runtime.library_settings): settle_once, `step(name)` seeing each step
        of the files' writes. Returns (what it returned, the lines it logged, how many files it wrote)."""
        wrote, settling = [], threading.current_thread()

        def reached(name):
            if name == "file written" and threading.current_thread() is settling:
                wrote.append(name)
            if step is not None:
                step(name)
        with mock.patch.object(file_changes, "_reached", side_effect=reached), \
                self.assertLogs(file_changes.logger, "INFO") as said:
            finished = file_changes.settle_once(self.library, EXIFTOOL)
        return finished, [record.getMessage() for record in said.records], len(wrote)

    def done(self):
        return self.states(self.change).count("done")

    def assert_settled(self):
        self.assertEqual(("applied", ["done"] * PHOTOS), (self.status(self.change), self.states(self.change)))
        for path in self.photos:
            self.assertEqual(["Beach", "Harbour"], keywords_of(path), path)
            self.assertEqual(["Beach", "Harbour"], self.indexed(path), path)
        self.assertEqual(self.stamps, {path: os.stat(path).st_mtime_ns for path in self.stamps},
                         "a file written before the kill was written again")

    def test_the_next_open_settles_it_a_chunk_at_a_time_and_a_save_that_waits_gets_its_turn(self):
        save = {}

        def step(name):
            if name == "file recorded" and "thread" not in save:
                # The owner saves another photo while the settle is under way: it waits for the lock.
                def saving():
                    result = tagging.change_tags(self.library, [self.other], ["Lighthouse"], [], EXIFTOOL)
                    save["ok"], save["settled then"] = result.ok, self.done()
                save["thread"] = threading.Thread(target=saving)
                save["thread"].start()
                wait_until(lambda: file_changes._waiting == 1, "the save waits for the lock")

        finished, said, wrote = self.open_library(step)
        save["thread"].join(60)
        self.assertEqual(1, finished)
        self.assertTrue(save["ok"])
        # The settle's first chunk is the written file and the first never reached: the save ran after it, not after all.
        self.assertEqual(4, save["settled then"], "the save waited for the whole settle")
        self.assertEqual(["Beach", "Lighthouse"], keywords_of(self.other))
        self.assertEqual(9, wrote, "only the nine files never reached are written")
        for line in ("2 of 10 file(s) settled", "4 of 10 file(s) settled", "8 of 10 file(s) settled"):
            self.assertTrue(any(line in each for each in said), (line, said))
        for each in said:
            self.assertNotIn(self.folder, each, "a path was logged")
        self.assert_settled()

    def test_cancel_stops_it_at_the_chunk_the_rest_left_planned_and_the_next_start_finishes_it(self):
        listed = []

        def step(name):
            if name == "file recorded" and not listed:
                # The long settle is a batch like any: listed with its progress, and cancelled from the list.
                listed.extend(batches.listing(self.library))
                batches.cancel(self.library, listed[0]["ticket"])

        finished, said, wrote = self.open_library(step)
        self.assertEqual(0, finished)
        # Ten files to settle, of which the first chunk's two are done when it is listed.
        self.assertEqual([("add to all selected", 10, 4, 8, True, self.change)],
                         [(each["operation"], each["photos"], each["done"], each["planned"], each["here"], each["id"])
                          for each in listed])
        self.assertEqual(1, wrote)
        self.assertEqual(("planned", ["done"] * 4 + ["planned"] * 8), (self.status(self.change), self.states(self.change)))
        self.assertTrue(any("stopped as asked after 2 of 10 file(s); the 8 left" in each for each in said), said)
        for path in self.photos[4:]:
            self.assertEqual(["Beach"], keywords_of(path), "a file after the stop was written")
        self.assertEqual([], batches.listing(self.library))

        # The next save in this process does not take the settle up again in its own request.
        self.assertEqual(0, file_changes.settle(self.library, EXIFTOOL))
        self.assertEqual(["done"] * 4 + ["planned"] * 8, self.states(self.change))

        # Nor does a save of a photo it left planned write ahead of it: the next start writes the cut-off edit first.
        refused = tagging.change_tags(self.library, [self.photos[11]], ["Lighthouse"], [], EXIFTOOL)
        self.assertFalse(refused.ok)
        self.assertIn("still to be written by change %d" % self.change, str(refused.errors))
        self.assertEqual(["Beach"], keywords_of(self.photos[11]))

        # The next start does, and writes each file left once.
        file_changes._settled.clear()
        file_changes._put_off.clear()
        file_changes._unsettled.clear()
        self.stamps.update({path: os.stat(path).st_mtime_ns for path in self.photos[3:4]})
        finished, _said, wrote = self.open_library()
        self.assertEqual((1, 8), (finished, wrote))
        self.assert_settled()

    def test_a_save_let_in_between_chunks_leaves_a_photo_the_settle_has_not_reached_alone(self):
        last, next_to_last, tried = self.photos[11], self.photos[10], {}

        def step(name):
            if name == "file recorded" and "thread" not in tried:
                # The owner saves, then renames, a photo of the cut-off change in its last chunk, before the settle is there.
                def saving():
                    result = tagging.change_tags(self.library, [last], ["Lighthouse"], [], EXIFTOOL)
                    tried["saved"], tried["save said"] = result.ok, str(result.errors)
                    try:
                        file_changes.rename(self.library, "smart rename",
                                            {next_to_last: os.path.join(self.folder, "renamed.jpg")}, {}, EXIFTOOL)
                    except names.RenameFailed as refused:
                        tried["rename said"] = str(refused)
                tried["thread"] = threading.Thread(target=saving)
                tried["thread"].start()
                wait_until(lambda: file_changes._waiting == 1, "the save waits for the lock")

        finished, _said, _wrote = self.open_library(step)
        tried["thread"].join(60)
        self.assertEqual(1, finished)
        self.assertFalse(tried["saved"])
        for said in (tried["save said"], tried["rename said"]):
            self.assertIn("still to be written by change %d" % self.change, said)
        self.assertTrue(os.path.exists(next_to_last), "the rename moved a photo the settle had still to write")
        # Settled whole, no conflict: the cut-off edit is on every photo.
        self.assert_settled()
        self.assertEqual({}, file_changes._unsettled)

        # Saved again once it is finished, the photo holds both edits.
        self.assertTrue(tagging.change_tags(self.library, [last], ["Lighthouse"], [], EXIFTOOL).ok)
        self.assertEqual(["Beach", "Harbour", "Lighthouse"], keywords_of(last))

    def test_a_settle_that_cannot_take_its_turn_is_left_whole_for_the_next(self):
        # A bulk write of this process holds the batches' turn (it waits for the lock the settle holds), or the server is
        # handing over to a new version: the settle claims no turn, writes nothing, and says why in one line.
        holding, done = threading.Event(), threading.Event()

        def bulk():
            with batches.claim(self.library, "add to all selected", 30):
                holding.set()
                done.wait(30)
        ahead = threading.Thread(target=bulk)
        ahead.start()
        holding.wait(30)
        try:
            running = self.open_library()
        finally:
            done.set()
            ahead.join(30)
        batches.stop_all()
        try:
            file_changes._settled.clear()
            closing = self.open_library()
        finally:
            batches.reopen()
        for finished, said, wrote in (running, closing):
            self.assertEqual((0, 0), (finished, wrote))
            self.assertEqual(1, sum("left half done" in each for each in said), said)
            self.assertTrue(any("for the next settle" in each for each in said), said)
        self.assertEqual(["done"] * 2 + ["writing"] + ["planned"] * 9, self.states(self.change))

        file_changes._settled.clear()
        finished, _said, wrote = self.open_library()
        self.assertEqual((1, 9), (finished, wrote))
        self.assert_settled()

    def test_a_write_that_settles_first_is_listed_with_the_change_it_settles(self):
        others = [self.make("other %d.jpg" % n) for n in range(3)]
        listed = []

        def step(name):
            if name == "file recorded" and not listed:
                listed.extend(batches.listing(self.library))
        with mock.patch.object(file_changes, "_reached", side_effect=step):
            self.assertTrue(tagging.change_tags(self.library, others, ["Lighthouse"], [], EXIFTOOL).ok)
        # One row, the write's own batch, cancellable here: not a batch of another process that cannot be.
        self.assertEqual([(self.change, True)], [(each["id"], each["here"]) for each in listed])
        self.assert_settled()


if __name__ == "__main__":
    unittest.main()

"""A long change of photo files killed part-way (a console window closed on an hours-long tag rewrite or caption sweep)
is settled by whatever opens the library next a chunk at a time, as a write is carried out: a save that waits gets the
lock between two chunks, Cancel stops it at the next chunk with the rest left planned for the next start, a line says
how far it has got, and no file is written twice (tagpup.services.file_changes.settle). A save or a rename let in between
two chunks leaves a photo the settle has still to write alone, saying why, so that the cut-off edit is not lost on it as
a conflict, and an Undo of an older change of its photos let in is refused as any undo with a newer change of its files;
a settle that cannot take its turn (a batch running, a hand-over) is left whole for the next.

In the server the settle runs on a thread of its own, so the request that opens the library returns at once (#2610;
DECISIONS 2026-11-07): the change is taken and its photos refused before it returns, the thread waits its turn as a
batch, listed and cancelled as one, and the CLI opening the library meanwhile leaves the change to it.

Real JPEGs written by the real ExifTool in a home of the test's own; rows as the indexer records a read
(photo_rows.add_read, through FilesCase.make); the kill is a BaseException at a step of the write (crash_at), with the
change's owner a process of this machine that has ended. Chunks of two (batches.CHUNK) over twelve photos. Each test
fails on the settle before it, which took every file in one go under the lock with no stop and no line.
"""
import os
import socket
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waiting import wait_until  # noqa: E402

from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, keywords_of, requires_exiftool  # noqa: E402

from tagpup import runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import names  # noqa: E402
from tagpup.services import batches, file_changes, tagging  # noqa: E402
from tagpup.store import file_journal, schema  # noqa: E402
from tagpup.web import lifecycle  # noqa: E402

GONE = "%s:4000000:1" % socket.gethostname()      # a process of this machine that has ended
PHOTOS = 12



@requires_exiftool
class CutOff(FilesCase):
    """Twelve photos holding Beach, a change adding Harbour to all twelve cut off at the third, its owner gone."""

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
        self.before_the_cut()
        # Killed as the third file is written, before its row: two done, one written, nine never reached.
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 3)):
            with self.assertRaises(Crash):
                self.add(self.photos)
        self.change = self.last_change()
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, self.change))
        self.assertEqual(["done"] * 2 + ["writing"] + ["planned"] * 9, self.states(self.change))
        self.stamps = {path: os.stat(path).st_mtime_ns for path in self.photos[:3]}

    def before_the_cut(self):
        """What the owner did before the long change: nothing."""

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


@requires_exiftool
class ALongChangeCutOff(CutOff):
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
                wait_until(lambda: file_changes._waiting == 1, what="the save waits for the lock")

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
        self.assertIn("still to be written by an earlier change", str(refused.errors))
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
                wait_until(lambda: file_changes._waiting == 1, what="the save waits for the lock")

        finished, _said, _wrote = self.open_library(step)
        tried["thread"].join(60)
        self.assertEqual(1, finished)
        self.assertFalse(tried["saved"])
        for said in (tried["save said"], tried["rename said"]):
            self.assertIn("still to be written by an earlier change", said)
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


def settle_threads():
    """The settles running on a thread of their own (file_changes.SETTLE_THREAD)."""
    return [thread for thread in threading.enumerate() if thread.name.startswith("SettleThread")]


@requires_exiftool
class OnAThreadInTheServer(CutOff):
    """What the server's first request does (runtime.library_settings) with the server's settle on a thread."""

    def setUp(self):
        super().setUp()
        on = mock.patch.object(runtime, "_settle_on_a_thread", True)
        on.start()
        self.addCleanup(on.stop)
        self.addCleanup(self.join_settles)
        self.writers = []
        reached = mock.patch.object(file_changes, "_reached", side_effect=self.reached)
        reached.start()
        self.addCleanup(reached.stop)
        self.step = None

    def reached(self, name):
        if name == "file written":
            self.writers.append(threading.current_thread().name)
        if self.step is not None:
            self.step(name)

    def settled(self):
        """The files the settle's thread wrote."""
        return [name for name in self.writers if name.startswith("SettleThread")]

    def join_settles(self):
        for thread in settle_threads():
            thread.join(120)
            self.assertFalse(thread.is_alive(), "a settle never ended")

    def open_in_the_server(self):
        """runtime.library_settings, as the request that first opens the library calls it: it returns before the settle
        has written a file, and writes none in the request's thread."""
        runtime.library_settings(self.library)
        self.assertNotIn(threading.current_thread().name, self.writers, "the request that opened the library settled it")

    def bulk_ahead(self):
        """A bulk write of the owner's holding this process's batch turn until the returned event is set."""
        holding, done = threading.Event(), threading.Event()

        def bulk():
            with batches.claim(self.library, "add to all selected", 30):
                holding.set()
                done.wait(60)
        ahead = threading.Thread(target=bulk)
        ahead.start()
        holding.wait(30)
        self.addCleanup(ahead.join, 30)
        self.addCleanup(done.set)
        return done

    def next_start(self):
        """The server ends and starts again: nothing kept, the change's owner gone."""
        self.join_settles()
        for kept in (file_changes._settled, file_changes._put_off, file_changes._unsettled):
            kept.clear()
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, self.change))

    def save(self, path):
        return tagging.change_tags(self.library, [path], ["Lighthouse"], [], EXIFTOOL)

    def test_the_open_returns_at_once_its_photos_are_refused_until_the_settle_reaches_them_then_saved(self):
        go = self.bulk_ahead()
        self.open_in_the_server()
        # The page sees the settle: a batch of this process, waiting its turn, cancellable, naming the change.
        wait_until(lambda: [row["state"] for row in batches.listing(self.library) if row["id"] == self.change]
                   == [batches.QUEUED], what="the settle is listed, waiting its turn")
        listed = [row for row in batches.listing(self.library) if row["id"] == self.change]
        self.assertEqual([(True, 10)], [(row["here"], row["photos"]) for row in listed])
        self.assertIsNotNone(listed[0]["ticket"])
        # Meanwhile a photo it has to write is refused at once, saying why; another is saved.
        refused = self.save(self.photos[11])
        self.assertFalse(refused.ok)
        self.assertIn("still to be written by an earlier change", str(refused.errors))
        self.assertEqual(["Beach"], keywords_of(self.photos[11]))
        self.assertTrue(self.save(self.other).ok)
        self.assertEqual("planned", self.status(self.change))

        go.set()
        self.join_settles()
        self.assert_settled()
        self.assertEqual(9, len(self.settled()), "only the nine files never reached are written")
        self.assertEqual({}, file_changes._unsettled)
        self.assertTrue(self.save(self.photos[11]).ok)
        self.assertEqual(["Beach", "Harbour", "Lighthouse"], keywords_of(self.photos[11]))

    def test_a_second_request_opening_it_starts_no_second_settle(self):
        go = self.bulk_ahead()
        self.open_in_the_server()
        self.open_in_the_server()
        self.assertEqual(1, len(settle_threads()))
        go.set()
        self.join_settles()
        self.assert_settled()
        self.assertEqual(9, len(self.settled()))

    def test_cancel_while_it_runs_keeps_the_photos_it_did_not_reach_refused_until_the_next_start(self):
        def step(name):
            if name == "file recorded" and threading.current_thread() in settle_threads() and self.step is step:
                self.step = None
                batches.cancel(self.library, [row for row in batches.listing(self.library)
                                              if row["id"] == self.change][0]["ticket"])
        self.step = step
        self.open_in_the_server()
        self.join_settles()
        self.assertEqual(("planned", ["done"] * 4 + ["planned"] * 8), (self.status(self.change), self.states(self.change)))
        refused = self.save(self.photos[11])
        self.assertFalse(refused.ok)
        self.assertIn("still to be written by an earlier change", str(refused.errors))

        self.stamps.update({path: os.stat(path).st_mtime_ns for path in self.photos[3:4]})
        self.next_start()
        self.writers.clear()
        self.open_in_the_server()
        self.join_settles()
        self.assertEqual(8, len(self.settled()))
        self.assert_settled()

    def test_cancel_while_it_waits_its_turn_writes_nothing_and_keeps_its_photos_refused_until_the_next_start(self):
        go = self.bulk_ahead()
        self.open_in_the_server()
        wait_until(lambda: any(row["id"] == self.change for row in batches.listing(self.library)), what="the settle waits")
        ticket = [row for row in batches.listing(self.library) if row["id"] == self.change][0]["ticket"]
        self.assertTrue(batches.cancel(self.library, ticket))
        self.join_settles()
        go.set()
        self.assertEqual([], self.writers)
        self.assertEqual(["done"] * 2 + ["writing"] + ["planned"] * 9, self.states(self.change))
        self.assertFalse(self.save(self.photos[11]).ok)
        # The next save's settle does not take it up in this process either.
        self.assertEqual(0, file_changes.settle(self.library, EXIFTOOL))

        self.next_start()
        self.open_in_the_server()
        self.join_settles()
        self.assert_settled()
        self.assertEqual(9, len(self.settled()))

    def test_two_libraries_opened_one_after_the_other_are_settled_one_at_a_time(self):
        # A second library, holding photos of its own, a change of theirs cut off the same way.
        first = self.library
        self.library = Library(self.home.library("second.db"))
        schema.ensure(self.library.path)
        theirs = [self.make("second %02d.jpg" % n) for n in range(4)]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 1)):
            with self.assertRaises(Crash):
                self.add(theirs)
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, self.last_change()))
        second, self.library = self.library, first

        self.open_in_the_server()
        runtime.library_settings(second)
        self.join_settles()
        self.assert_settled()
        for path in theirs:
            self.assertEqual(["Beach", "Harbour"], keywords_of(path), path)
        # One settle at a time: every write of one library's before any of the other's.
        order = [name for index, name in enumerate(self.writers) if index == 0 or self.writers[index - 1] != name]
        self.assertEqual(2, len(order), self.writers)

    def on_the_thread(self):
        """open_in_the_server, its settle thread seen to start and waited for: the thread."""
        ran = []
        settle_in_turn = file_changes._settle_in_turn

        def seen(*args):
            ran.append(threading.current_thread())
            settle_in_turn(*args)
        with mock.patch.object(file_changes, "_settle_in_turn", side_effect=seen):
            self.open_in_the_server()
            self.join_settles()
        self.assertEqual(1, len(ran), "no settle ran on a thread of its own")
        self.assertTrue(ran[0].name.startswith("SettleThread"), ran[0].name)
        self.assertFalse(ran[0].is_alive())
        return ran[0]

    def assert_put_off_with_its_photos_refused(self, states):
        self.assertEqual([("planned", None)], self.rows("SELECT status, owner FROM changes WHERE id = ?", (self.change,)))
        self.assertEqual(states, self.states(self.change))
        # Its photos are refused until the next start, and this process's next write does not take it up.
        refused = self.save(self.photos[11])
        self.assertFalse(refused.ok)
        self.assertIn(file_changes.UNSETTLED, str(refused.errors))
        self.assertEqual(["Beach"], keywords_of(self.photos[11]))
        self.assertEqual(0, file_changes.settle(self.library, EXIFTOOL))
        # The next start finishes it.
        self.stamps.update({path: os.stat(path).st_mtime_ns for path, state in zip(self.photos, states) if state == "done"})
        self.next_start()
        self.writers.clear()
        self.open_in_the_server()
        self.join_settles()
        self.assert_settled()

    def test_an_error_on_the_thread_part_way_puts_the_change_off_with_its_photos_still_refused(self):
        # ExifTool fails reading the first chunk back: two files settled, the rest never reached.
        with mock.patch.object(file_changes, "_read_back", side_effect=RuntimeError("the disk said no")), \
                self.assertLogs(file_changes.logger, "ERROR") as said:
            self.on_the_thread()
        self.assertTrue(any("could not be settled now" in line for line in said.output), said.output)
        self.assert_put_off_with_its_photos_refused(["done"] * 4 + ["planned"] * 8)

    def test_a_file_away_on_the_thread_puts_the_change_off_with_its_photo_still_refused(self):
        """The last photo's share does not answer while the thread settles: the eleven others are settled, the change is
        left. Put off like a cancelled one, its photo still refused: freed, the next save's own settle took the change up in
        its request (the wait the thread is for), or, with a batch running, wrote the photo ahead of it, a conflict at the
        next start. Fails on the code before: the save took it up and wrote the photo."""
        real, away = file_changes.file_states, os.path.normcase(self.photos[11])

        def states(photo_paths):
            return [(path, file_changes.AWAY if os.path.normcase(path) == away else state)
                    for path, state in real(photo_paths)]
        with mock.patch.object(file_changes, "file_states", side_effect=states):
            self.on_the_thread()
        self.assert_put_off_with_its_photos_refused(["done"] * 11 + ["planned"])

    def test_a_line_that_is_full_puts_the_change_off_with_its_photos_still_refused(self):
        with mock.patch.object(batches, "MOST_WAITING", -1), self.assertLogs(file_changes.logger, "ERROR") as said:
            self.on_the_thread()
        self.assertTrue(any("could not be settled now" in line for line in said.output), said.output)
        self.assertEqual([], self.writers)
        self.assert_put_off_with_its_photos_refused(["done"] * 2 + ["writing"] + ["planned"] * 9)

    def test_a_short_change_is_a_batch_that_an_update_waits_for_and_can_stop(self):
        seen = []

        def step(name):
            if name == "file written" and threading.current_thread() in settle_threads() and not seen:
                seen.append(([work.kind for work in lifecycle.under_way({})],
                             [(row["here"], row["id"], row["photos"]) for row in batches.listing(self.library)]))
        self.step = step
        # Ten files to settle, one chunk: not more than batches.CHUNK.
        with mock.patch.object(batches, "CHUNK", 10):
            self.on_the_thread()
        self.assertEqual([([lifecycle.BATCH], [(True, self.change, 10)])], seen)
        self.assert_settled()

    def test_two_changes_taken_at_once_are_both_this_process_s_on_activity(self):
        # A second long change of other photos cut off the same way, the first still running (its owner alive) meanwhile.
        more = [self.make("more %02d.jpg" % n) for n in range(6)]
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (file_journal.owner(), self.change))
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 2)):
            with self.assertRaises(Crash):
                self.add(more)
        second = self.last_change()
        self.assertNotEqual(self.change, second)
        self.execute("UPDATE changes SET owner = ? WHERE id IN (?, ?)", (GONE, self.change, second))
        listed = []

        def step(name):
            if name == "file recorded" and threading.current_thread() in settle_threads() and not listed:
                listed.extend((row["id"], row["here"]) for row in batches.listing(self.library))
        self.step = step
        self.open_in_the_server()
        self.join_settles()
        # The one settling is listed, cancellable here; the one after it is not another process's batch.
        self.assertEqual([(self.change, True)], listed)
        self.assert_settled()
        self.assertEqual("applied", self.status(second))

    def test_killed_part_way_on_the_thread_the_next_start_finishes_it_writing_no_file_twice(self):
        def step(name):
            if name == "file written" and len(self.writers) == 5:
                raise Crash(name)
        self.step = step
        hook = mock.patch.object(threading, "excepthook", lambda args: None)
        with hook:
            self.open_in_the_server()
            self.join_settles()
        # Killed as its fifth file was written: the fifth written and not recorded, the rest never reached.
        states = self.states(self.change)
        self.assertEqual((1, 5), (states.count("writing"), len(self.settled())), states)
        planned = states.count("planned")
        self.assertTrue(planned, states)
        self.stamps.update({path: os.stat(path).st_mtime_ns
                            for path, state in zip(self.photos, states) if state != "planned"})
        self.step = None
        self.writers.clear()
        self.next_start()
        self.open_in_the_server()
        self.join_settles()
        self.assert_settled()
        self.assertEqual(planned, len(self.settled()), "only the files never reached are written")

    def test_the_cli_opening_it_while_the_server_settles_leaves_the_change_to_the_server(self):
        go = self.bulk_ahead()
        self.open_in_the_server()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cli = ("import sys\n"
               "from tagpup.core.library import Library\n"
               "from tagpup.services import file_changes\n"
               "print('FINISHED=%d' % file_changes.settle_once(Library(sys.argv[1]), sys.argv[2]))\n")
        done = processes.run([sys.executable, "-c", cli, self.library.path, EXIFTOOL], cwd=root, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("FINISHED=0", done.stdout)
        self.assertEqual(["done"] * 2 + ["writing"] + ["planned"] * 9, self.states(self.change))
        go.set()
        self.join_settles()
        self.assert_settled()
        self.assertEqual(9, len(self.settled()))


@requires_exiftool
class AnOlderChangeOfItsPhotos(CutOff):
    def before_the_cut(self):
        # The owner added Gull to the last photo before the long change.
        self.assertTrue(self.add([self.photos[11]], tags=("Gull",)).ok)
        self.older = self.last_change()

    def test_an_undo_of_it_let_in_between_chunks_is_refused_and_the_settle_finishes(self):
        tried = {}

        def step(name):
            if name == "file recorded" and "thread" not in tried:
                # The owner undoes the older change from History while the next open settles the long one.
                def undoing():
                    result = self.undo(self.older)
                    tried["ok"], tried["said"] = result.ok, result.message()
                tried["thread"] = threading.Thread(target=undoing)
                tried["thread"].start()
                wait_until(lambda: file_changes._waiting == 1, what="the undo waits for the lock")

        finished, _said, _wrote = self.open_library(step)
        tried["thread"].join(60)
        self.assertEqual(1, finished)
        self.assertFalse(tried["ok"], "the undo put back a photo the settle had still to write")
        self.assertIn("change %d (add to all selected), applied after it, wrote the same photo files" % self.change,
                      tried["said"])
        self.assertEqual("applied", self.status(self.older))
        # Settled whole, no conflict: the last photo holds both edits.
        self.assertEqual(("applied", ["done"] * PHOTOS), (self.status(self.change), self.states(self.change)))
        self.assertEqual(["Beach", "Gull", "Harbour"], keywords_of(self.photos[11]))


if __name__ == "__main__":
    unittest.main()

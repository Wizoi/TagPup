"""The server starts, and the folder watcher's catch-up and the daily `sync` job both want to walk one library (F4;
#2102; tagpup.jobs.recurring.sync_whole owns the claim).

2026-10-28 08:34-08:48: photo_index's runs 94 and 95 were the watcher's whole sync and the daily job at once, two walks of
its 69,700 files over the share, and a second library's sync waited 15 min behind them. Now a watcher's whole sync is a
run of the `sync` job, claimed as the runner's: whichever asks first walks, and the other is told "running", then "not
due" -- a run that ended done since it was wanted covers it. One walk.

A folder's sync by the watcher records no run, and walked beside the daily one (#2755): now it takes the library's walk
(recurring.sync_folder), as a run of the `sync` job does. A photo copied in while the daily walks is synced after it, RECHECK
later; the daily asked while a folder's sync walks is told "running", and walks once it is over.

The libraries are made as the indexer leaves them, in the test's own home; fictional names. Nothing sleeps: the walks are
held on events, the watcher's clock is the test's, and each claim's outcome is told as it is made.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

from watchdog.events import FileCreatedEvent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

THEN = 1_700_000_000
WAIT = 20


class FakeObserver:
    """watchdog's observer, as far as the watcher uses it; `scheduled` is set once a folder is watched."""

    scheduled = None

    class Emitter:
        def __init__(self, watch):
            self.watch = watch

        def is_alive(self):
            return True

    def __init__(self):
        self.emitters = []

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, timeout=None):
        pass

    def schedule(self, handler, path, recursive=False):
        watch = ("watch", paths.key(path), recursive)
        self.emitters.append(self.Emitter(watch))
        if FakeObserver.scheduled is not None:
            FakeObserver.scheduled.set()
        return watch

    def unschedule(self, watch):
        self.emitters = [e for e in self.emitters if e.watch != watch]


class Clock:
    """The watcher's clock (time.monotonic), moved by the test."""

    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class TheWatcherAndTheDailySyncWantOneLibrary(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="one_walk_")
        folder = os.path.join(home.root, "Photos", "2019 Regatta")
        os.makedirs(folder)
        db_path = home.library("regatta.db")
        library_actions.create(db_path)
        conn = db.connect(db_path)
        try:
            for n in range(2):
                path = os.path.join(folder, "IMG_%04d.jpg" % n)
                with open(path, "wb") as handle:
                    handle.write(b"jpeg %d" % n)
                os.utime(path, (THEN, THEN))
                photo_rows.add_indexed(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        self.library, self.folder = Library(db_path), folder
        #: Each walk of the library, by who: ("watcher" or "runner", folder).
        self.walks = []
        #: Each claim of the `sync` job's run and what became of it: (who, "ran" or why not).
        self.outcomes = []
        self.told = threading.Condition()
        real = recurring.run_claimed

        def run_claimed(*args, **kwargs):
            outcome = real(*args, **kwargs)
            who = "watcher" if threading.current_thread().name == "FolderWatcherThread" else "runner"
            with self.told:
                self.outcomes.append((who, outcome.why or "ran"))
                self.told.notify_all()
            return outcome
        patcher = mock.patch.object(recurring, "run_claimed", run_claimed)
        patcher.start()
        self.addCleanup(patcher.stop)
        #: Each sync of a folder the watcher asked for: "ran", or "running" when the walk was another's.
        self.folder_turns = []
        real_folder = recurring.sync_folder

        def sync_folder(library, call):
            result = real_folder(library, call)
            with self.told:
                self.folder_turns.append("running" if result is None else "ran")
                self.told.notify_all()
            return result
        folder_patcher = mock.patch.object(recurring, "sync_folder", sync_folder)
        folder_patcher.start()
        self.addCleanup(folder_patcher.stop)
        FakeObserver.scheduled = threading.Event()
        self.addCleanup(setattr, FakeObserver, "scheduled", None)
        self.clock = Clock()

    def walk(self, who, entered=None, release=None, only_a_folders=False):
        """A sync of the library as `who` makes one, recorded as a walk, held on `release` once `entered` is set (only a
        folder's, when `only_a_folders`)."""
        def walked(library, folder=None, apply=True):
            self.walks.append((who, folder))
            if entered is not None and (folder is not None or not only_a_folders):
                entered.set()
                release.wait(WAIT)
            return sync.sync(library, folder=folder, apply=apply)
        return walked

    def watcher(self, synced, **timings):
        options = dict(debounce=0.0, recheck=1000.0, tick=0.01, clock=self.clock, observer=FakeObserver)
        options.update(timings)
        watcher = runtimes.folder_watcher(lambda: [self.library], sync_with=synced, **options)
        self.addCleanup(watcher.stop, 10)
        return watcher

    def runner(self, synced):
        class Runtime:
            def sync(self, library, folder=None, apply=False):
                return synced(library, folder, apply)
        return recurring.Runner(lambda: [self.library], given={"runtime": Runtime()})

    def told_of(self, outcome):
        with self.told:
            self.assertTrue(self.told.wait_for(lambda: outcome in self.outcomes, WAIT),
                            "never told %r; told %r" % (outcome, self.outcomes))

    def folder_told_of(self, turn, count):
        with self.told:
            self.assertTrue(self.told.wait_for(lambda: self.folder_turns.count(turn) >= count, WAIT),
                            "never told %r; told %r" % (turn, self.folder_turns))

    def copied_in(self, watcher):
        """A photo copied into the library's folder, and its notification as the watch on the folder gives it."""
        photo = os.path.join(self.folder, "IMG_0002.jpg")
        with open(photo, "wb") as handle:
            handle.write(b"jpeg 2")
        watcher.notice(paths.key(self.folder), FileCreatedEvent(photo))

    def test_a_photo_copied_in_while_the_daily_sync_walks_is_synced_after_it_not_beside_it(self):
        """#2755. Break: the watcher syncing a folder without the library's walk (runtime.folder_watcher not handing it
        recurring.sync_folder), or dropping the folder when the walk is another's (Watcher._folder_later)."""
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        watcher = self.watcher(self.walk("watcher"), recheck=100.0)
        runner = self.runner(self.walk("runner", entered, release))
        job = recurring.JOBS.get(recurring.SYNC)
        watcher.start()
        self.told_of(("watcher", "ran"))   # the catch-up
        daily = threading.Thread(target=runner.run_job, args=(job, self.library), kwargs={"force": True},
                                 name="DailySyncThread", daemon=True)
        daily.start()
        self.addCleanup(daily.join, WAIT)
        self.assertTrue(entered.wait(WAIT), "the daily sync never walked")
        self.copied_in(watcher)
        self.folder_told_of("running", 1)
        release.set()
        daily.join(WAIT)
        self.told_of(("runner", "ran"))
        self.clock.now = 100.0   # its recheck
        self.folder_told_of("ran", 1)
        self.assertEqual([("watcher", None), ("runner", None), ("watcher", paths.stored(self.folder))], self.walks)

    def test_the_daily_sync_asked_while_a_folders_sync_walks_is_told_running_and_walks_after_it(self):
        """#2755, the other way. Break: a run of the `sync` job not taking the library's walk (recurring.run_claimed)."""
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        watcher = self.watcher(self.walk("watcher", entered, release, only_a_folders=True))
        runner = self.runner(self.walk("runner"))
        job = recurring.JOBS.get(recurring.SYNC)
        watcher.start()
        self.told_of(("watcher", "ran"))   # the catch-up
        self.copied_in(watcher)
        self.assertTrue(entered.wait(WAIT), "the folder's sync never walked")
        self.assertEqual("running", runner.run_job(job, self.library, force=True).why,
                         "the daily sync walked beside a folder's")
        release.set()
        self.folder_told_of("ran", 1)
        self.assertTrue(runner.run_job(job, self.library, force=True).ran, "the daily sync did not walk after it")
        self.assertEqual([("watcher", None), ("watcher", paths.stored(self.folder)), ("runner", None)], self.walks)
        self.assertEqual([("watcher", "ran"), ("runner", "running"), ("runner", "ran")], self.outcomes)

    def test_the_watchers_catch_up_walks_first_and_the_daily_sync_is_told_running_then_not_due(self):
        """Runs 94 and 95 of 2026-10-28. Break: the watcher syncing whole without recurring.sync_whole (the runner's claim
        is granted beside its walk), or sync_whole not ending its run done (the runner walks again after it)."""
        entered, release = threading.Event(), threading.Event()
        watcher = self.watcher(self.walk("watcher", entered, release))
        self.addCleanup(release.set)
        runner = self.runner(self.walk("runner"))
        job = recurring.JOBS.get(recurring.SYNC)
        watcher.start()
        self.assertTrue(entered.wait(WAIT), "the watcher's catch-up never walked")
        self.assertEqual("running", runner.run_job(job, self.library).why, "the daily sync walked beside the watcher")
        release.set()
        self.told_of(("watcher", "ran"))
        self.assertEqual("not due", runner.run_job(job, self.library).why, "the watcher's walk is not the daily one")
        self.assertEqual([("watcher", None)], self.walks)
        self.assertEqual([("runner", "running"), ("watcher", "ran"), ("runner", "not due")], self.outcomes)

    def test_the_daily_sync_walks_first_and_the_watchers_catch_up_is_told_running_then_not_due_after_its_recheck(self):
        """Break: the watcher syncing whole beside a run that holds the claim, dropping its catch-up when told "running"
        (never asked again), or walking again after it (sync_whole not asking recurring.covered_since)."""
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        watcher = self.watcher(self.walk("watcher"), debounce=5.0, recheck=100.0)
        runner = self.runner(self.walk("runner", entered, release))
        job = recurring.JOBS.get(recurring.SYNC)
        watcher.start()
        self.assertTrue(FakeObserver.scheduled.wait(WAIT), "the watcher never watched")   # its catch-up is wanted now
        daily = threading.Thread(target=runner.run_job, args=(job, self.library), name="DailySyncThread", daemon=True)
        daily.start()
        self.addCleanup(daily.join, WAIT)
        self.assertTrue(entered.wait(WAIT), "the daily sync never walked")
        self.clock.now = 5.0   # the catch-up's notifications have settled: it asks while the daily walks
        self.told_of(("watcher", "running"))
        release.set()
        daily.join(WAIT)
        self.told_of(("runner", "ran"))
        self.clock.now = 105.0   # its recheck
        self.told_of(("watcher", "not due"))
        self.assertEqual([("runner", None)], self.walks)
        self.assertEqual([("watcher", "running"), ("runner", "ran"), ("watcher", "not due")], self.outcomes)

    def test_a_photo_copied_in_while_the_folders_return_waits_is_walked_though_the_catch_up_covered_the_return(self):
        """A folder back from a share that did not answer is wanted since the share was found not answering, and the
        catch-up that waited for the share covers it; a photo copied in meanwhile does not wait on that walk. Break:
        Watcher.notice leaving a waiting whole sync wanted since before the photo came (it is told "not due")."""
        watcher = self.watcher(self.walk("watcher"), debounce=5.0)
        before = time.time()
        watcher.start()
        self.assertTrue(FakeObserver.scheduled.wait(WAIT), "the watcher never watched")
        self.clock.now = 5.0
        self.told_of(("watcher", "ran"))   # the catch-up
        watcher._note_whole([self.library], noted=before)   # the folder back, as Watcher._look notes it
        self.clock.now = 10.0
        self.told_of(("watcher", "not due"))
        watcher._note_whole([self.library], noted=before)
        photo = os.path.join(self.folder, "IMG_0002.jpg")
        with open(photo, "wb") as handle:
            handle.write(b"jpeg 2")
        with mock.patch.object(time, "time", return_value=time.time() + 5):   # a later second than the catch-up's
            watcher.notice(paths.key(self.folder), FileCreatedEvent(photo))
        self.clock.now = 15.0
        with self.told:
            self.assertTrue(self.told.wait_for(lambda: len(self.outcomes) == 3, WAIT), self.outcomes)
        self.assertEqual([("watcher", "ran"), ("watcher", "not due"), ("watcher", "ran")], self.outcomes)
        self.assertEqual([("watcher", None), ("watcher", None)], self.walks)


if __name__ == "__main__":
    unittest.main()

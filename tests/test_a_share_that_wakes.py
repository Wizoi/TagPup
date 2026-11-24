"""A share asleep as the server starts, and waking (F7; #2101; tagpup.files.shares owns "back").

2026-10-28 08:33: the server started with the NAS asleep. The watcher's first look at the roots did not answer within its
moment and took the share as away; the catch-up sync 3 s later was answered "away" from that memo without waiting, and
every library on the share was recorded not in step (photo_index run 92, kr-track 83, renton_parkrun 67); the share
answered 30 s later and nothing looked again until the watcher's next RECHECK. Now a sync's roots go through one patient
walk (shares.Walk), which waits for the share to be back, and the watcher is told when it is (shares.on_back) and looks
at once. A share that never answers leaves the sync honestly not in step, and the watcher's RECHECK stays its safety net.

The daily `sync` job that could not look at its roots ended done, so it was tried again 23 h later (#2103): it now ends
failed, saying why, and is tried again in an hour.

The share is a folder of the test's own home taken for a network share: its folder looks wait until the test wakes it.
The libraries are made as the indexer leaves them; fictional names.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images, shares  # noqa: E402
from tagpup.jobs import recurring, watching  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import job_runs  # noqa: E402

THEN = 1_700_000_000


class FakeObserver:
    """watchdog's observer, as far as the watcher uses it: what is scheduled, each emitter alive."""

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
        return watch

    def unschedule(self, watch):
        self.emitters = [e for e in self.emitters if e.watch != watch]


class Clock:
    """The recurring runner's clock, moved by the test."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Stand:
    """A library as the watcher sees one: a key and a name."""

    def __init__(self, name):
        self.name = self.key = name


class AShareAsleep(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="share_wakes_")
        self.share = os.path.join(self.home.root, "NAS", "Pictures")
        os.makedirs(self.share)
        shares.forget()
        self.addCleanup(shares.forget)
        self.awake = threading.Event()
        self.addCleanup(self.awake.set)
        #: Each look at a folder on the share, as asked of the disk: one for each ShareLook thread started.
        self.looks = []
        under, real = os.path.normcase(self.share), os.path.isdir

        def isdir(path):
            if os.path.normcase(str(path)).startswith(under):
                self.looks.append(path)
                self.awake.wait(20)
            return real(path)
        for patcher in (mock.patch.object(os.path, "isdir", isdir),
                        mock.patch.object(shares, "on_a_network_drive",
                                          lambda path: os.path.normcase(str(path)).startswith(under)),
                        mock.patch.object(shares, "SHARE_WAIT", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wake_in(self, seconds):
        timer = threading.Timer(seconds, self.awake.set)
        timer.daemon = True
        timer.start()
        self.addCleanup(timer.cancel)

    def library(self, name, folder, count=2):
        """A library holding `count` photos in `folder`, each a row as a whole index leaves it."""
        os.makedirs(folder, exist_ok=True)
        db_path = self.home.library(name)
        library_actions.create(db_path)
        from tagpup.store import db
        conn = db.connect(db_path)
        try:
            for n in range(count):
                path = os.path.join(folder, "IMG_%04d.jpg" % n)
                with open(path, "wb") as handle:
                    handle.write(b"jpeg %d" % n)
                os.utime(path, (THEN, THEN))
                photo_rows.add_indexed(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        return Library(db_path)

    def watcher(self, libraries, folders, synced, **timings):
        """A watcher of stand-ins: `folders(library)` watched, each sync `synced(library, folder)` at once."""
        options = dict(debounce=0.2, recheck=30.0, tick=0.05, observer=FakeObserver)
        options.update(timings)
        watcher = watching.Watcher(lambda: libraries, folders, synced, images.is_photo, **options)
        self.addCleanup(watcher.stop, 10)
        return watcher

    def servers_watcher(self, libraries, synced):
        """The watcher the server makes of real libraries (runtime.folder_watcher): a whole sync claimed as a run of the
        daily `sync` job (recurring.sync_whole)."""
        watcher = runtimes.folder_watcher(lambda: libraries, sync_with=synced, debounce=0.2, recheck=30.0, tick=0.05,
                                          observer=FakeObserver)
        self.addCleanup(watcher.stop, 10)
        return watcher

    def wait_until(self, check, seconds=10):
        deadline = time.monotonic() + seconds
        while not check():
            self.assertLess(time.monotonic(), deadline, "timed out")
            time.sleep(0.02)

    def test_the_server_starts_with_the_share_asleep_and_the_catch_up_waits_for_it_and_finds_the_library_in_step(self):
        """Runs 92/83/67 of 2026-10-28 08:33. Break: sync._scan asking shares.folder_state (the memo answers "away" at
        once), or the watcher syncing whole again when the share is back although its catch-up looked at it (the
        folder's return wanted since now, not since the share was found not answering: recurring.covered_since)."""
        held = os.path.join(self.share, "2019 Regatta")
        regatta = self.library("regatta.db", held)
        results = []

        def synced(library, folder):
            results.append(sync.sync(library, folder=folder, apply=True, roots=[self.share]))
            return results[-1]
        watcher = self.servers_watcher([regatta], synced)
        self.wake_in(0.6)   # after the watcher's moment and the catch-up's start, well within FOLDER_WAIT
        watcher.start()
        self.wait_until(lambda: results)
        counts = results[0].details["counts"]
        self.assertEqual((0, 0, 1), (counts["not_looked_at"], counts["folders_not_looked_at"], counts["folders_walked"]))
        self.assertTrue(results[0].details["in_step"], "the catch-up did not wait for the share")
        self.assertIsNotNone(sync.last(regatta)["last_in_step"])
        self.wait_until(lambda: watcher.watched() == [paths.stored(held)])
        time.sleep(0.6)   # wait: no barrier yet; a second whole sync would come a debounce (0.2) after the look
        self.assertEqual(1, len(results), "synced whole again although the catch-up looked at every folder")

    def test_a_share_that_never_answers_ends_the_sync_not_in_step_and_the_watcher_keeps_looking_on_one_thread(self):
        """Break: a sync that stops waiting calling the library in step, or a look while away starting a thread of its own
        (a page of looks each holding one). The RECHECK look stays: a share may never say it is back."""
        regatta = self.library("regatta.db", os.path.join(self.share, "2019 Regatta"))
        results, looks = [], []

        def synced(library, folder):
            results.append(sync.sync(library, folder=folder, apply=True, roots=[self.share]))
            return results[-1]
        watcher = self.watcher([regatta], lambda library: [self.share], synced, recheck=0.1)
        real_look = watcher._look

        def look(*args, **kwargs):
            looks.append(time.monotonic())
            return real_look(*args, **kwargs)
        with mock.patch.object(sync, "FOLDER_WAIT", 0.3), mock.patch.object(watcher, "_look", look):
            watcher.start()
            self.wait_until(lambda: results)
            self.wait_until(lambda: len(looks) >= 5)
        counts = results[0].details["counts"]
        self.assertEqual((2, 1), (counts["not_looked_at"], counts["folders_not_looked_at"]))
        self.assertFalse(results[0].details["in_step"])
        self.assertIsNone(sync.last(regatta)["last_in_step"])
        self.assertEqual([], watcher.watched())
        self.assertEqual(1, len(self.looks), "a look at the share while it was away started a thread of its own")

    def test_the_server_restarts_within_the_hour_after_a_catch_up_that_could_not_look_and_its_catch_up_walks(self):
        """#2109: run 92 (in_step 0) counted as a recent whole sync, so a restart within CATCH_UP_SKIP skipped the
        catch-up and the library stayed not in step until the daily job. Break: sync.synced_whole_within asking the
        newest whole sync, not the newest that was in step (job_runs.last_in_step)."""
        regatta = self.library("regatta.db", os.path.join(self.share, "2019 Regatta"))
        results = []

        def synced(library, folder):
            results.append(sync.sync(library, folder=folder, apply=True, roots=[self.share]))
            return results[-1]
        with mock.patch.object(sync, "FOLDER_WAIT", 0.2):
            first = self.servers_watcher([regatta], synced)
            first.start()
            self.wait_until(lambda: results)
            self.assertFalse(results[0].details["in_step"])
            self.assertTrue(first.stop(10))
            self.servers_watcher([regatta], synced).start()   # the server restarted, the share still asleep
            self.wait_until(lambda: len(results) == 2)
        self.assertEqual([False, False], [result.details["in_step"] for result in results])
        self.assertIsNone(sync.last(regatta)["last_in_step"])

    def test_the_daily_sync_that_could_not_look_at_the_share_says_so_and_is_tried_again_in_an_hour_not_a_day(self):
        """#2103: runs 92/83/41/42/67 could not look at their roots and ended done, so the next try was 23 h later.
        Break: recurring.Runner ending the run without asking Job.failed (sync.could_not_look), or job_runs.finish
        ending it done all the same."""
        regatta = self.library("regatta.db", os.path.join(self.share, "2019 Regatta"))
        walks, share = [], self.share

        class Runtime:
            def sync(self, library, folder=None, apply=False):
                walks.append(folder)
                return sync.sync(library, folder=folder, apply=apply, roots=[share])
        clock = Clock(THEN)
        runner = recurring.Runner(lambda: [regatta], clock=clock, given={"runtime": Runtime()})
        job = recurring.JOBS.get("sync")
        with mock.patch.object(sync, "FOLDER_WAIT", 0.2):
            self.assertTrue(runner.run_job(job, regatta).ran)
            (entry,) = [each for each in recurring.overview(regatta, now=THEN) if each["name"] == "sync"]
            run = entry["runs"][0]
            self.assertEqual(("failed", True), (run["outcome"], entry["failing"]), "the Activity page shows it failing")
            self.assertEqual("share", run["failure"])
            self.assertTrue(run["error"].startswith("1 folder could not be looked at"), run["error"])
            self.assertEqual(job_runs.stamp(THEN + recurring.RETRY_AFTER), entry["next_due"])
            clock.now = THEN + recurring.RETRY_AFTER - 60
            self.assertEqual("not due", runner.run_job(job, regatta).why)
            clock.now = THEN + recurring.RETRY_AFTER
            self.assertTrue(runner.run_job(job, regatta).ran, "not tried again an hour later")
        self.assertEqual([None, None], walks)

    def test_the_share_wakes_and_the_watcher_watches_it_and_syncs_whole_within_a_tick_not_its_recheck(self):
        """Break: the watcher not asking shares.on_back (sync.watch_states) or not looking when told: it looks again only
        at RECHECK, 30 s here as on the owner's server."""
        library, synced = Stand("regatta"), []
        watcher = self.watcher([library], lambda lib: [self.share], lambda lib, folder: synced.append(folder))
        watcher.start()
        self.wait_until(lambda: synced == [None])   # the catch-up, the share still asleep
        self.assertEqual([], watcher.watched())
        woke = time.monotonic()
        self.awake.set()
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.share)], 5)
        self.wait_until(lambda: synced == [None, None], 5)
        self.assertLess(time.monotonic() - woke, 5.0)

    def test_two_libraries_on_one_share_are_both_woken_by_its_one_answer(self):
        """Break: one share's answer telling one listener (the second library's folder asked again only at RECHECK)."""
        regatta, crew = Stand("regatta"), Stand("crew")
        folders = {"regatta": os.path.join(self.share, "Regatta"), "crew": os.path.join(self.share, "Crew")}
        for folder in folders.values():
            os.makedirs(folder)
        synced = []
        watcher = self.watcher([regatta, crew], lambda lib: [folders[lib.name]],
                               lambda lib, folder: synced.append((lib.name, folder)))
        watcher.start()
        self.wait_until(lambda: sorted(synced) == [("crew", None), ("regatta", None)])
        del synced[:]
        self.awake.set()
        self.wait_until(lambda: len(watcher.watched()) == 2, 5)
        self.wait_until(lambda: sorted(synced) == [("crew", None), ("regatta", None)], 5)


class TwoLibrariesSyncedOnAShareAsleep(unittest.TestCase):
    """The second library's sync, on the share the first found away, waits for it once (FOLDER_WAIT), not "away" at once."""

    def test_the_second_library_waits_for_the_share_once_and_is_then_not_looked_at(self):
        """Break: sync._scan asking shares.folder_state (answered from the memo; the second library never waits)."""
        home = own_home.for_test(self, prefix="share_second_")
        share = os.path.join(home.root, "NAS", "Pictures")
        os.makedirs(share)
        shares.forget()
        self.addCleanup(shares.forget)
        asleep, under, real = threading.Event(), os.path.normcase(share), os.path.isdir
        self.addCleanup(asleep.set)

        def isdir(path):
            if os.path.normcase(str(path)).startswith(under):
                asleep.wait(20)
                return False
            return real(path)
        waits, real_wait = [], shares.wait_back

        def wait_back(share_key, seconds, stop=None):
            waits.append((share_key, seconds))
            return real_wait(share_key, seconds, stop)
        db_paths = []
        for name in ("regatta.db", "crew.db"):
            db_paths.append(home.library(name))
            library_actions.create(db_paths[-1])
        with mock.patch.object(os.path, "isdir", isdir), mock.patch.object(shares, "SHARE_WAIT", 0.05), \
                mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(under)), \
                mock.patch.object(shares, "wait_back", wait_back), mock.patch.object(sync, "FOLDER_WAIT", 0.1):
            first = sync.sync(Library(db_paths[0]), apply=True, roots=[share])
            del waits[:]
            second = sync.sync(Library(db_paths[1]), apply=True, roots=[share])
        self.assertEqual([(shares.share_of(share), 0.1)], waits, "the second library did not wait for the share, once")
        for result in (first, second):
            self.assertEqual(1, result.details["counts"]["folders_not_looked_at"])
            self.assertFalse(result.details["in_step"])


if __name__ == "__main__":
    unittest.main()

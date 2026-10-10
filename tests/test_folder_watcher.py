"""The folder watcher (tagpup.jobs.watching; docs/ARCHITECTURE.md, phase 8, "Watching,
with the schedule as the safety net"): real watchdog on temporary folders.

A photo copied in is one sync of its folder once the notifications settle; fifty are one
sync too; a file that is no photo is none. The whole library is synced when the watcher
starts, when notifications were lost, and when a folder that was gone is back. A write
TagPup makes is synced too, and the sync reads no file. Stopping waits for a sync under
way. No library of the owner's is touched: the libraries here are stand-ins, or made in
the test's own home.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.jobs import watching  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import refresh_rows, relink_photos, tagging  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
JPEG = None


def a_jpeg(path):
    """A small real JPEG at `path`."""
    global JPEG
    if JPEG is None:
        from PIL import Image
        import io
        buffer = io.BytesIO()
        Image.new("RGB", (16, 12), (90, 110, 130)).save(buffer, "JPEG")
        JPEG = buffer.getvalue()
    with open(path, "wb") as handle:
        handle.write(JPEG)
    return path


class Stand:
    """A library as the watcher sees one: a key and a name."""

    def __init__(self, name):
        self.name = name
        self.key = name


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="watcher_")
        self.root = os.path.join(self.home.root, "Photos")
        os.makedirs(os.path.join(self.root, "Regatta"))
        self.library = Stand("harbour")
        self.synced = []
        self.gate = None

    def sync(self, library, folder):
        if self.gate is not None:
            self.gate.wait(30)
        self.synced.append((library.name, folder))

    def make(self, **timings):
        options = dict(debounce=0.5, recheck=0.2, tick=0.05)
        options.update(timings)
        watcher = watching.Watcher(lambda: [self.library], lambda library: [self.root], self.sync,
                                   images.is_photo, **options)
        self.addCleanup(watcher.stop, 10)
        return watcher

    def wait_until(self, check, seconds=20):
        deadline = time.time() + seconds
        while True:
            found = check()
            if found:
                return found
            self.assertLess(time.time(), deadline, "timed out")
            time.sleep(0.02)

    def started(self, watcher):
        watcher.start()
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        # The catch-up: the whole library, once, as it starts.
        self.wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced)
        del self.synced[:]
        return watcher

    def folder_syncs(self):
        return [folder for _name, folder in self.synced]


class WhatIsSynced(Base):
    def test_a_photo_copied_in_is_one_sync_of_its_folder_after_the_notifications_settle(self):
        watcher = self.started(self.make(debounce=1.0))
        regatta = os.path.join(self.root, "Regatta")
        noticed = time.monotonic()
        a_jpeg(os.path.join(regatta, "Regatta - 1.jpg"))
        self.wait_until(lambda: self.synced)
        self.assertGreaterEqual(time.monotonic() - noticed, 1.0, "synced before the notifications settled")
        time.sleep(1.5)
        self.assertEqual([paths.stored(regatta)], self.folder_syncs())
        self.assertFalse(watcher.busy())

    def test_fifty_photos_are_one_sync(self):
        self.started(self.make(debounce=1.0))
        regatta = os.path.join(self.root, "Regatta")
        for n in range(50):
            a_jpeg(os.path.join(regatta, "Regatta - %d.jpg" % n))
        self.wait_until(lambda: self.synced)
        time.sleep(2)
        self.assertEqual([paths.stored(regatta)], self.folder_syncs())

    def test_a_file_that_is_no_photo_is_no_sync(self):
        self.started(self.make())
        with open(os.path.join(self.root, "Regatta", "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("race day")
        os.makedirs(os.path.join(self.root, "Empty"))
        time.sleep(1.5)
        self.assertEqual([], self.synced)

    def test_a_folder_and_one_under_it_are_one_sync_of_the_folder(self):
        self.started(self.make(debounce=1.0))
        a_jpeg(os.path.join(self.root, "Start.jpg"))
        a_jpeg(os.path.join(self.root, "Regatta", "Finish.jpg"))
        self.wait_until(lambda: self.synced)
        time.sleep(1.5)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_a_photo_moved_between_folders_syncs_both(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        os.makedirs(os.path.join(self.root, "Harbour"))
        self.started(self.make())
        os.replace(os.path.join(self.root, "Regatta", "Buoy.jpg"), os.path.join(self.root, "Harbour", "Buoy.jpg"))
        self.wait_until(lambda: len(self.synced) >= 2)
        time.sleep(1)
        self.assertEqual(sorted([paths.stored(os.path.join(self.root, "Regatta")),
                                 paths.stored(os.path.join(self.root, "Harbour"))], key=paths.key),
                         sorted(self.folder_syncs(), key=paths.key))


class AFolderMovedOrDeleted(Base):
    """Windows says a folder deleted or moved out of the watched one is a file deleted
    (watchdog 6 cannot ask what it was once it is gone): a name with no photo extension,
    which the photo filter dropped, so the folder's rows were never found missing."""

    def test_moved_out_of_the_root_syncs_the_folder_it_was_in(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        self.started(self.make())
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        os.replace(os.path.join(self.root, "Regatta"), os.path.join(elsewhere, "Regatta"))
        self.wait_until(lambda: self.synced)
        time.sleep(1)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_deleted_syncs_the_folder_it_was_in(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        self.started(self.make())
        shutil.rmtree(os.path.join(self.root, "Regatta"))
        self.wait_until(lambda: self.synced)
        time.sleep(1)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_a_file_that_is_no_photo_deleted_is_still_no_sync_of_its_own_folder(self):
        notes = os.path.join(self.root, "Regatta", "notes.txt")
        with open(notes, "w", encoding="utf-8") as handle:
            handle.write("race day")
        self.started(self.make())
        os.remove(notes)
        self.wait_until(lambda: self.synced)
        # Its parent is synced -- it might have been a folder -- never the file's name.
        self.assertEqual([paths.stored(os.path.join(self.root, "Regatta"))], self.folder_syncs())


class WhenNotificationsMayHaveBeenMissed(Base):
    def test_an_overflow_syncs_the_library_whole(self):
        watcher = self.started(self.make())
        with self.assertLogs("tagpup.jobs.watching", level="WARNING"):
            watcher.notice(paths.key(self.root), watching.Overflow(self.root))
        self.wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced)

    @unittest.skipUnless(os.name == "nt", "Windows' notifications")
    def test_windows_says_so_by_a_read_of_nothing(self):
        from watchdog.observers.api import EventQueue, ObservedWatch
        observer = watching.make_observer()
        # Made, not started: the handle is opened only as its thread starts.
        emitter = observer._emitter_class(EventQueue(), ObservedWatch(self.root, recursive=True))
        emitter._whandle = object()
        queued = []
        emitter.queue_event = queued.append
        emitter.should_keep_running = lambda: True
        from watchdog.observers import winapi
        with mock.patch.object(winapi, "read_directory_changes", return_value=(b"", 0)):
            self.assertEqual([], emitter._read_events())
        self.assertEqual(1, len(queued))
        self.assertIsInstance(queued[0], watching.Overflow)
        # And a watch being stopped reads nothing too, and that is no overflow.
        emitter.should_keep_running = lambda: False
        with mock.patch.object(winapi, "read_directory_changes", return_value=(b"", 0)):
            emitter._read_events()
        self.assertEqual(1, len(queued))

    def test_a_folder_not_there_is_watched_when_it_is_back_and_its_library_synced_whole(self):
        away = self.root + " (unplugged)"
        os.replace(self.root, away)
        watcher = self.make()
        watcher.start()
        time.sleep(0.8)
        self.assertEqual([], watcher.watched())
        self.assertEqual([("harbour", None)], self.synced, "the catch-up at start")
        del self.synced[:]
        os.replace(away, self.root)
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        self.wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced, "not synced whole when its folder came back")
        del self.synced[:]
        a_jpeg(os.path.join(self.root, "Regatta", "Back.jpg"))
        self.wait_until(lambda: self.synced)
        self.assertEqual([paths.stored(os.path.join(self.root, "Regatta"))], self.folder_syncs())

    def test_a_watched_folder_removed_is_let_go_and_watched_again_when_it_is_back(self):
        watcher = self.started(self.make())
        shutil.rmtree(self.root)
        self.wait_until(lambda: not os.path.exists(self.root) and watcher.watched() == [])
        os.makedirs(os.path.join(self.root, "Regatta"))
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        self.wait_until(lambda: ("harbour", None) in self.synced)

    def test_a_watched_folder_renamed_is_noticed_from_its_parent_and_its_library_synced_whole(self):
        # The 30 s look at the folders is not what finds it: its watch sees nothing of its own rename.
        watcher = self.started(self.make(recheck=30.0))
        self.wait_until(lambda: watcher.watched_parents() == [paths.stored(self.home.root)])
        os.rename(self.root, self.root + " 2026")
        self.wait_until(lambda: ("harbour", None) in self.synced, 10)

    def test_a_folder_beside_a_watched_one_renamed_is_no_sync(self):
        watcher = self.started(self.make(recheck=30.0))
        self.wait_until(lambda: watcher.watched_parents() == [paths.stored(self.home.root)])
        os.makedirs(os.path.join(self.home.root, "Other"))
        os.rename(os.path.join(self.home.root, "Other"), os.path.join(self.home.root, "Other 2"))
        time.sleep(1.5)
        self.assertEqual([], self.synced)

    def test_a_library_in_too_many_separate_folders_is_not_watched_and_is_told_why(self):
        folders = [tempfile.mkdtemp(dir=self.home.root) for _ in range(3)]
        watcher = watching.Watcher(lambda: [self.library], lambda library: folders, self.sync,
                                   images.is_photo, debounce=0.2, recheck=0.2, tick=0.05, max_watches=2)
        self.addCleanup(watcher.stop, 10)
        with self.assertLogs("tagpup.jobs.watching", level="WARNING") as logged:
            watcher.start()
            time.sleep(0.6)
        self.assertEqual([], watcher.watched())
        self.assertEqual(1, sum("root folders" in line for line in logged.output), "said at every look")


class Stopping(Base):
    def test_waits_for_a_sync_under_way_and_then_watches_nothing(self):
        watcher = self.started(self.make())
        self.gate = threading.Event()
        a_jpeg(os.path.join(self.root, "Regatta", "Slow.jpg"))
        self.wait_until(watcher.busy)
        stopped = []
        stopping = threading.Thread(target=lambda: stopped.append(watcher.stop(timeout=20)), daemon=True)
        stopping.start()
        time.sleep(0.3)
        self.assertTrue(stopping.is_alive(), "stopped under a sync")
        self.gate.set()
        stopping.join(20)
        self.assertEqual([True], stopped)
        self.assertFalse(watcher.busy())
        del self.synced[:]
        a_jpeg(os.path.join(self.root, "Regatta", "After.jpg"))
        time.sleep(1)
        self.assertEqual([], self.synced)

    def test_started_again_it_watches_again_without_the_catch_up(self):
        watcher = self.started(self.make())
        watcher.stop()
        watcher.start()
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        time.sleep(0.5)
        self.assertEqual([], self.synced)
        a_jpeg(os.path.join(self.root, "Regatta", "Again.jpg"))
        self.wait_until(lambda: self.synced)
        self.assertEqual([paths.stored(os.path.join(self.root, "Regatta"))], self.folder_syncs())


class TheCatchUpAtStart(Base):
    """Every start of the server synced each library whole: after a crash restart, or an
    update, that is photo_index walked again for nothing. It is skipped for a library
    synced whole in the last CATCH_UP_SKIP."""

    def make_with(self, recent):
        watcher = watching.Watcher(lambda: [self.library], lambda library: [self.root], self.sync, images.is_photo,
                                   debounce=0.2, recheck=0.2, tick=0.05, recent=recent)
        self.addCleanup(watcher.stop, 10)
        return watcher

    def test_is_skipped_for_a_library_synced_whole_lately(self):
        watcher = self.make_with(lambda library: True)
        watcher.start()
        self.wait_until(lambda: watcher.watched())
        time.sleep(0.8)
        self.assertEqual([], self.synced)

    def test_is_made_for_one_that_was_not(self):
        watcher = self.make_with(lambda library: False)
        watcher.start()
        self.wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced)

    def test_a_whole_sync_is_recent_by_the_librarys_own_record(self):
        home = own_home.for_test(self, prefix="catch_up_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        self.assertFalse(sync_service.synced_whole_within(library, 3600))
        from tagpup.store import sync_runs
        sync_runs.record(library.path, sync_runs.now(), whole=False, in_step=True, found={}, changed={})
        self.assertFalse(sync_service.synced_whole_within(library, 3600), "a folder's sync is not the whole")
        sync_runs.record(library.path, sync_runs.now(), whole=True, in_step=False, found={}, changed={})
        self.assertTrue(sync_service.synced_whole_within(library, 3600))
        with mock.patch.object(sync_service.time, "time", return_value=time.time() + 3601):
            self.assertFalse(sync_service.synced_whole_within(library, 3600))


class WhichProcessWatches(unittest.TestCase):
    def test_the_one_that_runs_the_jobs_and_never_a_tests_unless_it_asks(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(runtimes.RUN_JOBS, None)
            os.environ.pop(runtimes.NO_JOBS, None)
            self.assertNotIn("folder watcher", runtimes.background(Runtime()).names())
            os.environ[runtimes.RUN_JOBS] = "1"
            self.assertIn("folder watcher", runtimes.background(Runtime()).names())
            os.environ[runtimes.NO_JOBS] = "1"
            self.assertNotIn("folder watcher", runtimes.background(Runtime()).names())


def seed(library_path, photo):
    """The photo's row as the indexer leaves it: its stamp and what it holds."""
    stat = os.stat(photo)
    conn = db.connect(library_path)
    try:
        conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                     (paths.stored(photo), stat.st_mtime, stat.st_size, json.dumps([]), json.dumps([]), json.dumps({})))
        conn.commit()
    finally:
        conn.close()


class WhichFoldersALibraryHas(unittest.TestCase):
    def test_its_roots_and_the_folders_it_holds_photos_in_outside_them(self):
        home = own_home.for_test(self, prefix="watch_folders_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        root = os.path.join(home.root, "Photos")
        outside = os.path.join(home.root, "Elsewhere", "Trip")
        for folder in (os.path.join(root, "Regatta"), outside):
            os.makedirs(folder)
            seed(library.path, a_jpeg(os.path.join(folder, "one.jpg")))
        found = sync_service.watch_folders(library, [root])
        self.assertEqual(sorted([paths.stored(root), paths.stored(outside)], key=paths.key),
                         sorted(found, key=paths.key))

    def test_each_photo_path_is_read_again_only_when_the_photos_change(self):
        """The watcher asks every 30 s; on photo_index each ask read 68,387 paths."""
        home = own_home.for_test(self, prefix="watch_folders_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        root = os.path.join(home.root, "Photos")
        os.makedirs(os.path.join(root, "Regatta"))
        seed(library.path, a_jpeg(os.path.join(root, "Regatta", "one.jpg")))
        with mock.patch.object(sync_service.store_folders, "with_rows",
                               wraps=sync_service.store_folders.with_rows) as read:
            first = sync_service.watch_folders(library, [])
            self.assertEqual(first, sync_service.watch_folders(library, []))
            self.assertEqual(1, read.call_count, "every path read again with nothing changed")
            self.assertEqual([paths.stored(root)], sync_service.watch_folders(library, [root]),
                             "a change of roots is seen at once")
            self.assertEqual(1, read.call_count)
            elsewhere = os.path.join(home.root, "Elsewhere")
            os.makedirs(elsewhere)
            seed(library.path, a_jpeg(os.path.join(elsewhere, "two.jpg")))
            self.assertIn(paths.stored(elsewhere), sync_service.watch_folders(library, [root]))
            self.assertEqual(2, read.call_count)

    def test_none_for_a_library_behind_this_version(self):
        home = own_home.for_test(self, prefix="watch_folders_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        with mock.patch.object(sync_service.schema, "pending", return_value=[14]):
            self.assertEqual([], sync_service.watch_folders(library, [home.root]))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TagPupsOwnWrites(unittest.TestCase):
    """The app's writes are notified like any other; the sync they cause finds each row
    describing its file, and reads nothing."""

    def test_a_tag_written_by_tagpup_is_a_sync_that_reads_no_file(self):
        home = own_home.for_test(self, prefix="watcher_writes_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        folder = os.path.join(home.root, "Photos")
        os.makedirs(folder)
        photo = a_jpeg(os.path.join(folder, "Regatta - 1.jpg"))
        seed(library.path, photo)

        results, reads = [], []

        def counted(real, what):
            def call(*args, **kwargs):
                files = args[1] if what == "read_files" else args[0]
                if files:
                    reads.append((what, len(files)))
                return real(*args, **kwargs)
            return call

        def sync(lib, folder_synced):
            with mock.patch.object(refresh_rows, "read_files", counted(refresh_rows.read_files, "read_files")), \
                    mock.patch.object(relink_photos, "claims_of", counted(relink_photos.claims_of, "claims_of")):
                result = runtimes.sync(lib, folder=folder_synced, apply=True)
            results.append((folder_synced, result))

        watcher = watching.Watcher(lambda: [library], lambda lib: sync_service.watch_folders(lib, []), sync,
                                   images.is_photo, debounce=1.0, recheck=0.5, tick=0.05)
        self.addCleanup(watcher.stop, 20)
        watcher.start()
        deadline = time.time() + 30
        while not (results and watcher.watched()) and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual([None], [f for f, _r in results], "no catch-up at start")
        self.assertTrue(results[0][1].details["in_step"])
        del results[:]

        written = tagging.change_tags(library, [photo], ["Harbour"], [], EXIFTOOL)
        self.assertEqual(1, written.changed, written.refused)
        while not results and time.time() < deadline + 30:
            time.sleep(0.05)
        time.sleep(1.5)
        self.assertEqual([paths.stored(folder)], [f for f, _r in results], "one sync of the written folder")
        result = results[0][1]
        counts = result.details["counts"]
        self.assertEqual((0, 0, 0, 0), (counts["changed"], counts["new"], counts["moved"], counts["missing"]))
        self.assertEqual(0, result.changed)
        self.assertEqual([], reads, "the sync of TagPup's own write read a file")


class FakeObserver:
    """An observer that records what is scheduled, and lets a test run something when a watch is unscheduled."""

    class Emitter:
        def __init__(self, watch):
            self.watch, self.alive = watch, True

        def is_alive(self):
            return self.alive

    def __init__(self):
        self.scheduled, self.emitters, self.on_unschedule = [], [], None

    def schedule(self, handler, path, recursive=False):
        watch = ("watch", path, recursive, len(self.scheduled))
        self.scheduled.append((path, recursive))
        self.emitters.append(self.Emitter(watch))
        return watch

    def unschedule(self, watch):
        self.emitters = [e for e in self.emitters if e.watch != watch]
        if self.on_unschedule:
            self.on_unschedule(watch)


class TheWatchesThemselves(Base):
    def watcher_over(self, folders, **options):
        self.folders = folders
        return watching.Watcher(lambda: [self.library], lambda library: list(self.folders), self.sync,
                                images.is_photo, **options)

    def test_a_watch_is_unscheduled_outside_the_lock_watchdog_dispatches_into(self):
        # watchdog calls notice() with the observer's lock held; unscheduling holds that lock too. If the watcher
        # unschedules while holding its own, each waits for the other.
        watcher, observer = self.watcher_over([self.root]), FakeObserver()
        watcher._look(observer)
        stuck = []

        def a_notification_arrives(_watch):
            from watchdog.events import FileCreatedEvent
            thread = threading.Thread(target=watcher.notice, args=(
                paths.key(self.root), FileCreatedEvent(os.path.join(self.root, "Regatta", "A.jpg"))), daemon=True)
            thread.start()
            thread.join(3)
            stuck.append(thread.is_alive())

        observer.on_unschedule = a_notification_arrives
        self.folders = []
        watcher._look(observer)
        self.assertTrue(stuck, "nothing was unscheduled")
        self.assertEqual([False] * len(stuck), stuck, "a notification waited on the lock unschedule was holding")

    def test_the_parents_count_toward_the_limit_of_watches(self):
        observer = FakeObserver()
        self.watcher_over([self.root], max_watches=1)._look(observer)
        self.assertEqual([(paths.stored(self.root), True)], observer.scheduled)
        observer = FakeObserver()
        self.watcher_over([self.root], max_watches=2)._look(observer)
        self.assertEqual([(paths.stored(self.root), True), (paths.stored(self.home.root), False)],
                         observer.scheduled)

    def test_a_parent_whose_watch_died_is_watched_again_and_its_libraries_synced_whole(self):
        watcher, observer = self.watcher_over([self.root]), FakeObserver()
        watcher._look(observer)
        parent = [e for e in observer.emitters if e.watch[1] == paths.stored(self.home.root)][0]
        parent.alive = False
        before = len(observer.scheduled)
        watcher._look(observer)
        self.assertEqual((paths.stored(self.home.root), False), observer.scheduled[before])
        self.assertIsNotNone(watcher._pending[self.library.key]["whole"])


if __name__ == "__main__":
    unittest.main()

"""The folder watcher (tagpup.jobs.watching; docs/ARCHITECTURE.md, phase 8, "Watching,
with the schedule as the safety net"): real watchdog on temporary folders.

A photo copied in is one sync of its folder once the notifications settle; fifty are one
sync too; a file that is no photo is none. The whole library is synced when the watcher
starts, when notifications were lost, and when a folder that was gone is back. A write
TagPup makes is not synced: the rows already describe the files. Stopping waits for a sync under
way. No library of the owner's is touched: the libraries here are stand-ins, or made in
the test's own home.
"""
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
import photo_rows  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images, shares  # noqa: E402
from tagpup.jobs import recurring, watching  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import job_runs as recorded_runs  # noqa: E402
from tagpup.services import refresh_rows, tagging  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import db, job_runs, schema  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

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

    def test_a_watched_folder_whose_share_stops_answering_keeps_its_watch_and_is_not_synced_whole(self):
        """#1392: the watcher asked a bare os.path.isdir, so a root on a share that stopped answering was let go as not
        there, and its library synced whole when it answered again. Neither there nor gone, it keeps its watch. (A local
        drive that is not connected is gone, #1387: test_a_watched_folder_removed_is_let_go_...)"""
        watcher = self.started(self.make())
        root = paths.key(self.root)
        real_isdir = os.path.isdir
        with mock.patch.object(os.path, "isdir", lambda path: False if paths.key(path) == root else real_isdir(path)), \
                mock.patch("tagpup.files.shares.on_a_network_drive", lambda path: paths.key(path) == root), \
                mock.patch("tagpup.files.shares._share_answers", lambda path: paths.key(path) != root):
            time.sleep(0.8)   # four looks at the roots (recheck 0.2)
            self.assertEqual([paths.stored(self.root)], watcher.watched(), "a share that does not answer was let go")
        time.sleep(0.5)
        self.assertEqual([], self.synced, "synced whole as if it had come back")

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


class WhatTheLogSays(Base):
    def said_of(self, changed):
        """What the watcher logs of a whole sync whose Result changed `changed` (details["changed"])."""
        from tagpup.core.result import Result
        result = Result(attempted=1, changed=sum(changed.get(what, 0) for what in ("from_files", "relinked", "removed")))
        result.details.update(counts={"missing": changed.get("removed", 0)}, queued=0, changed=changed)
        watcher = watching.Watcher(lambda: [self.library], lambda library: [self.root], lambda library, folder: result,
                                   images.is_photo, debounce=0.5, recheck=0.2, tick=0.05)
        with self.assertLogs("tagpup.jobs.watching", level="INFO") as logged:
            watcher._run(self.library, None)
        return "\n".join(logged.output)

    def test_an_unasked_removal_is_said_with_its_named_faces(self):
        said = self.said_of({"from_files": 0, "relinked": 0, "removed": 2, "removed_faces": 5, "removed_named": 1})
        self.assertIn("2 row(s) removed, with 5 face(s), 1 of them named", said)

    def test_a_sync_that_removed_nothing_says_nothing_of_it(self):
        said = self.said_of({"from_files": 3, "relinked": 0, "removed": 0, "removed_faces": 0, "removed_named": 0})
        self.assertNotIn("removed", said)


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
        from tagpup.store import job_runs
        job_runs.record_sync(library.path, job_runs.stamp(time.time()), whole=False, in_step=True, found={}, changed={})
        self.assertFalse(sync_service.synced_whole_within(library, 3600), "a folder's sync is not the whole")
        job_runs.record_sync(library.path, job_runs.stamp(time.time()), whole=True, in_step=False, found={}, changed={})
        self.assertFalse(sync_service.synced_whole_within(library, 3600), "a whole sync that left it not in step")
        job_runs.record_sync(library.path, job_runs.stamp(time.time()), whole=True, in_step=True, found={}, changed={})
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
    """The photo as a whole index leaves it (photo_rows.add_indexed): its row's stamp, what it holds, its folder, its
    vector and its look for faces -- the index owes it nothing, so a sync that finds it unchanged leaves the library in
    step. It was an INSERT of the photo alone, with no folder row: a shape nothing real makes."""
    db.write_with_connection(library_path, lambda conn: photo_rows.add_indexed(conn, photo, {}))


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
class TagPupsOwnWrites(Base):
    """TagPup's own writes are notified like any other, from whichever process made them. A bake of photo_index synced
    its folder once for each file it wrote, 7 or 8 times a minute, and recorded each (2026-10-28). The watcher the
    server runs (runtime.folder_watcher) asks sync.followed whether the library's rows already describe each photo the
    notifications named: then the folder is not synced. Anything else -- a photo renamed by the owner, a second library
    whose rows are stale, a folder changed, a look that fails, too many photos to look at -- is synced as before."""

    PHOTOS = 201

    def setUp(self):
        super().setUp()
        self.regatta = os.path.join(self.root, "Regatta")
        self.photos = [a_jpeg(os.path.join(self.regatta, "Regatta - %03d.jpg" % n)) for n in range(self.PHOTOS)]
        self.harbour = self.made("harbour.db", self.photos)
        self.results, self.reads = [], []

    def made(self, name, photos):
        """A library holding `photos` as a whole index leaves them, synced whole just now: no catch-up at start."""
        library = Library(self.home.library(name))
        schema.ensure(library.path)
        db.write_with_connection(library.path, lambda conn: [photo_rows.add_indexed(conn, photo, {}) for photo in photos])
        job_runs.record_sync(library.path, job_runs.stamp(time.time()), whole=True, in_step=True, found={}, changed={})
        return library

    def record(self, library, folder):
        """The watcher's sync, applied as the server's is, and what it read of the files."""
        def counted(real):
            def call(*args, **kwargs):
                if args[1]:
                    self.reads.append((library.name, len(args[1])))
                return real(*args, **kwargs)
            return call

        with mock.patch.object(refresh_rows, "read_files", counted(refresh_rows.read_files)):
            result = runtimes.sync(library, folder=folder, apply=True, index_new=False)
        self.synced.append((library.name, folder))
        self.results.append(result)

    def watching(self, *libraries):
        # Half the server's DEBOUNCE: a pause longer than it between a file written and its row recorded (a loaded
        # machine) is a sync, rightly -- the row did not yet describe the file -- but not what these tests ask.
        watcher = runtimes.folder_watcher(libraries=lambda: list(libraries), sync_with=self.record,
                                          debounce=1.5, recheck=0.5, tick=0.05)
        self.addCleanup(watcher.stop, 20)
        watcher.start()
        self.wait_until(lambda: watcher.watched() == [paths.stored(self.regatta)])
        return watcher

    def settled(self, watcher, *libraries):
        """Wait until each library's notifications were heard and none waits, then a tick more."""
        def done():
            told = watcher.status()["libraries"]
            return all(told.get(library.name, {}).get("last_event") and not told[library.name]["pending_folders"]
                       and not told[library.name]["whole_pending"] for library in libraries)
        self.wait_until(done, 60)
        self.wait_until(lambda: not watcher.busy(), 60)
        time.sleep(0.3)

    def write(self, photos, library=None):
        written = tagging.change_tags(library or self.harbour, photos, ["Harbour"], [], EXIFTOOL)
        self.assertEqual(len(photos), written.changed, written.refused)

    def folder_records(self, library):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_FOLDER,)).fetchone()[0]
        finally:
            conn.close()

    def test_two_hundred_photos_written_by_tagpup_are_no_sync_and_no_record(self):
        watcher = self.watching(self.harbour)
        self.write(self.photos[:200])
        self.settled(watcher, self.harbour)
        self.assertEqual([], self.synced, "neither the folder nor the whole library is synced")
        self.assertEqual(0, self.folder_records(self.harbour))

    def test_a_photo_the_owner_renames_during_the_writes_is_synced_and_its_row_follows(self):
        watcher = self.watching(self.harbour)
        renamed = os.path.join(self.regatta, "Regatta - start line.jpg")
        writing = threading.Thread(target=self.write, args=(self.photos[:200],), daemon=True)
        writing.start()
        time.sleep(0.2)
        os.rename(self.photos[200], renamed)
        writing.join(120)
        self.settled(watcher, self.harbour)
        self.assertEqual([("harbour", paths.stored(self.regatta))], self.synced)
        counts = self.results[0].details["counts"]
        self.assertEqual((1, 0, 0, 0), (counts["moved"], counts["new"], counts["missing"], counts["changed"]))
        self.assertEqual([], self.reads, "the sync read a file TagPup wrote")
        conn = db.connect(db.readonly_uri(self.harbour.path), uri=True)
        try:
            held = sorted(paths.key(path) for (path,) in conn.execute("SELECT path FROM photos"))
        finally:
            conn.close()
        self.assertIn(paths.key(renamed), held)
        self.assertNotIn(paths.key(self.photos[200]), held)
        self.assertEqual(1, self.folder_records(self.harbour), "a sync that found something is recorded")

    def test_a_second_library_holding_the_folder_has_stale_rows_and_syncs_it(self):
        marina = self.made("marina.db", self.photos[:3])
        watcher = self.watching(self.harbour, marina)
        self.write(self.photos[:3])
        self.settled(watcher, self.harbour, marina)
        self.assertEqual([("marina", paths.stored(self.regatta))], self.synced)
        self.assertEqual(3, self.results[0].details["counts"]["changed"])
        conn = db.connect(db.readonly_uri(marina.path), uri=True)
        try:
            tags = [tags for (tags,) in conn.execute("SELECT tags FROM photos")]
        finally:
            conn.close()
        self.assertTrue(all("Harbour" in each for each in tags), tags)

    def test_a_look_that_fails_still_syncs(self):
        with mock.patch.object(sync_service, "followed", side_effect=OSError("the library is locked")):
            watcher = self.watching(self.harbour)
        self.write(self.photos[:3])
        self.settled(watcher, self.harbour)
        self.assertEqual([("harbour", paths.stored(self.regatta))], self.synced)

    def test_more_photos_named_than_it_looks_at_walk_the_folder(self):
        watcher = self.watching(self.harbour)
        with mock.patch.object(watching, "NAMED_MOST", 3):
            self.write(self.photos[:4])
            self.settled(watcher, self.harbour)
        self.assertEqual([("harbour", paths.stored(self.regatta))], self.synced)
        self.assertEqual([], self.reads, "its rows describe every file: nothing is read")

    def test_a_folder_named_among_the_writes_walks_the_folder(self):
        spare = os.path.join(self.regatta, "Spare")
        os.makedirs(spare)
        watcher = self.watching(self.harbour)
        self.write(self.photos[:3])
        os.rmdir(spare)   # Windows says: a name gone, which may have been a folder
        self.settled(watcher, self.harbour)
        self.assertEqual([("harbour", paths.stored(self.regatta))], self.synced)


class AWholeSyncOverASlowShare(unittest.TestCase):
    """A whole sync walks the share folder by folder, minutes on photo_index's 2,564: while it walks, the Activity
    page's Now says how far it got -- its phase and counts from its run's row (a run of the `sync` job,
    recurring.sync_whole), the folder it is at from the watcher's memory -- and the log says so every LOG_FOLDERS
    folders and LOG_SECONDS seconds, then once more when the walk is over."""

    def test_says_how_far_it_got(self):
        home = own_home.for_test(self, prefix="watcher_slow_")
        library = Library(home.library("harbour.db"))
        schema.ensure(library.path)
        # A second library, idle: Now reads both.
        schema.ensure(home.library("regatta.db"))
        root = os.path.join(home.root, "Photos")
        os.makedirs(root)
        seed(library.path, a_jpeg(os.path.join(root, "Regatta - 1.jpg")))
        for name in ("Dunes", "Ferry", "Lighthouse", "Quay", "Tidepool"):
            os.makedirs(os.path.join(root, name))
            a_jpeg(os.path.join(root, name, "one.jpg"))
        walked_before_the_share_stalls = 3
        stalled, go_on, listed = threading.Event(), threading.Event(), []
        real_listing = shares.listing

        def slow_listing(folder):
            if len(listed) == walked_before_the_share_stalls:
                stalled.set()
                go_on.wait(30)
            listed.append(folder)
            return real_listing(folder)

        watcher = watching.Watcher(lambda: [library], lambda lib: [root],
                                   lambda lib, folder: runtimes.sync(lib, folder=folder, apply=False),
                                   images.is_photo, whole=recurring.sync_whole, debounce=0.5, recheck=0.2, tick=0.05)
        app = web.create_app("tagpup", lifecycle=Lifecycle(background=runtimes.Background(
            [(runtimes.FOLDER_WATCHER, watcher)])))
        app.testing = True
        page = app.test_client()
        # Every count told is written: the 10 s between two writes of a run's progress is not what this is about.
        with mock.patch.object(shares, "listing", slow_listing), mock.patch.object(recorded_runs, "PROGRESS_EVERY", 0), \
                mock.patch.object(sync_service, "LOG_FOLDERS", 2), mock.patch.object(sync_service, "LOG_SECONDS", 0), \
                self.assertLogs("tagpup.services.sync", level="INFO") as logged:
            syncing = threading.Thread(target=watcher._run, args=(library, None))
            syncing.start()
            try:
                self.assertTrue(stalled.wait(30), "the walk never reached the stalling share")
                now = page.get("/api/activity/now").get_json()
                self.assertEqual("harbour", now["syncing"]["library"])
                self.assertIsNone(now["syncing"]["folder"])
                progress = now["syncing"]["progress"]
                self.assertEqual((sync_service.WALKING, 3, 3), (progress["phase"], progress["folders"], progress["files"]))
                self.assertEqual(paths.key(listed[-1]), paths.key(progress["folder"]))
                [run] = job_runs.under_way(library.path)
                self.assertEqual(recurring.SYNC, run.job)
                self.assertEqual(run.heartbeat, progress["progressed"])
                self.assertNotIn(paths.key(root), str(run.progress).lower(), "the row holds counts, never a path")
                self.assertEqual({"harbour", "regatta"}, {each["name"] for each in now["libraries"]})
            finally:
                go_on.set()
                syncing.join(30)
        said = "\n".join(logged.output)
        self.assertIn("so far", said)
        self.assertIn("6 folder(s) and 6 photo file(s) in", said, "the walk's end is said once a progress line was")
        self.assertIsNone(page.get("/api/activity/now").get_json()["syncing"])
        self.assertEqual([], job_runs.under_way(library.path), "the walk's run ended with it")

    def test_a_short_walk_says_nothing_of_its_progress(self):
        """Fewer than LOG_FOLDERS folders: no line; and once the walk is over, the folder it was at is no longer said."""
        at = {}
        with sync_service.walking_at_into(at), self.assertNoLogs("tagpup.services.sync", level="INFO"):
            walked = sync_service._Walked(sync_service.WALKING)
            walked.listed("D:/Photos/Regatta", 4)
            self.assertEqual("D:/Photos/Regatta", at["folder"])
            walked.ended()
        self.assertIsNone(at["folder"])


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

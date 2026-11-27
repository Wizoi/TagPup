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
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from waiting import wait_until  # noqa: E402

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
from watchdog.events import FileMovedEvent  # noqa: E402

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
        self.heard, self.looks, self.barriers = [], 0, 0

    def sync(self, library, folder):
        if self.gate is not None:
            self.gate.wait(30)
        self.synced.append((library.name, folder))

    def make(self, **timings):
        options = dict(debounce=0.5, recheck=0.2, tick=0.05)
        options.update(timings)
        watcher = watching.Watcher(lambda: [self.library], self.folders, self.sync, images.is_photo, **options)
        self.addCleanup(watcher.stop, 10)
        return self.hearing(watcher)

    def folders(self, library):
        """The library's one root; each call is the start of one of the watcher's looks at its folders."""
        self.looks += 1
        return [self.root]

    def looked(self, times=1):
        """Wait until the watcher has looked at its folders `times` times whole from now (the next look starting is the
        last one ended)."""
        now = self.looks
        wait_until(lambda: self.looks > now + times, what="%d looks at the roots" % times)

    def hearing(self, watcher):
        """Note each notification the watcher has finished noting, by the names in it (`heard`): what quiet() waits for."""
        for name in ("notice", "notice_parent"):
            real = getattr(watcher, name)

            def noted(key, event, real=real):
                try:
                    return real(key, event)
                finally:
                    self.heard.extend(paths.key(path) for path in (event.src_path, getattr(event, "dest_path", ""))
                                      if path)
            setattr(watcher, name, noted)
        return watcher

    def quiet(self, watcher, where=None):
        """Wait until the watcher has heard all this test did and synced what it will. A file written after it, no
        photo and so noted as nothing, is heard (a watch's notifications come in order, on one thread), and then
        nothing waits for its debounce and nothing syncs: a sync the test must show did NOT happen would be in
        `synced` by then, with no fixed wait."""
        self.barriers += 1
        barrier = os.path.join(where or self.root, "barrier %d.txt" % self.barriers)
        with open(barrier, "w", encoding="utf-8") as handle:
            handle.write("heard")
        wait_until(lambda: paths.key(barrier) in self.heard, what="the notification of " + barrier)
        wait_until(lambda: self.idle(watcher), what="the watcher to have nothing waiting and nothing syncing")

    @staticmethod
    def idle(watcher):
        told = watcher.status()["libraries"].values()
        return not watcher.busy() and not any(each["pending_folders"] or each["whole_pending"] for each in told)

    def started(self, watcher):
        watcher.start()
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        # The catch-up: the whole library, once, as it starts.
        wait_until(lambda: self.synced)
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
        wait_until(lambda: self.synced)
        self.assertGreaterEqual(time.monotonic() - noticed, 1.0, "synced before the notifications settled")
        self.quiet(watcher)
        self.assertEqual([paths.stored(regatta)], self.folder_syncs())
        self.assertFalse(watcher.busy())

    def test_fifty_photos_are_one_sync(self):
        watcher = self.started(self.make(debounce=1.0))
        regatta = os.path.join(self.root, "Regatta")
        for n in range(50):
            a_jpeg(os.path.join(regatta, "Regatta - %d.jpg" % n))
        self.quiet(watcher)
        self.assertEqual([paths.stored(regatta)], self.folder_syncs())

    def test_a_file_that_is_no_photo_is_no_sync(self):
        watcher = self.started(self.make())
        with open(os.path.join(self.root, "Regatta", "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("race day")
        os.makedirs(os.path.join(self.root, "Empty"))
        self.quiet(watcher)
        self.assertEqual([], self.synced)

    def test_a_folder_and_one_under_it_are_one_sync_of_the_folder(self):
        watcher = self.started(self.make(debounce=1.0))
        a_jpeg(os.path.join(self.root, "Start.jpg"))
        a_jpeg(os.path.join(self.root, "Regatta", "Finish.jpg"))
        self.quiet(watcher)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_a_photo_moved_between_folders_syncs_both(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        os.makedirs(os.path.join(self.root, "Harbour"))
        watcher = self.started(self.make())
        os.replace(os.path.join(self.root, "Regatta", "Buoy.jpg"), os.path.join(self.root, "Harbour", "Buoy.jpg"))
        self.quiet(watcher)
        self.assertEqual(sorted([paths.stored(os.path.join(self.root, "Regatta")),
                                 paths.stored(os.path.join(self.root, "Harbour"))], key=paths.key),
                         sorted(self.folder_syncs(), key=paths.key))


class AFolderMovedOrDeleted(Base):
    """Windows says a folder deleted or moved out of the watched one is a file deleted
    (watchdog 6 cannot ask what it was once it is gone): a name with no photo extension,
    which the photo filter dropped, so the folder's rows were never found missing."""

    def test_moved_out_of_the_root_syncs_the_folder_it_was_in(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        watcher = self.started(self.make())
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        os.replace(os.path.join(self.root, "Regatta"), os.path.join(elsewhere, "Regatta"))
        self.quiet(watcher)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_deleted_syncs_the_folder_it_was_in(self):
        a_jpeg(os.path.join(self.root, "Regatta", "Buoy.jpg"))
        watcher = self.started(self.make())
        shutil.rmtree(os.path.join(self.root, "Regatta"))
        self.quiet(watcher)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_a_folder_renamed_that_watchdog_called_a_file_syncs_its_parent_with_no_look_at_a_path(self):
        """Noting a notification runs on watchdog's one dispatch thread, every library's: a look at a share that sleeps
        there held them all (#2754). A folder renamed while watchdog's own look at it raced comes as a file renamed in
        its folder, and is synced from that folder. Break: Watcher.notice asking whether the new name is a folder."""
        watcher = self.started(self.make())
        with mock.patch.object(shares, "folder_state", side_effect=AssertionError("a look on the dispatch thread")), \
                mock.patch.object(shares, "stamp_of", side_effect=AssertionError("a look on the dispatch thread")):
            watcher.notice(paths.key(self.root), FileMovedEvent(os.path.join(self.root, "Regatta"),
                                                                os.path.join(self.root, "Races")))
        wait_until(lambda: self.synced)
        self.assertEqual([paths.stored(self.root)], self.folder_syncs())

    def test_a_file_that_is_no_photo_deleted_is_still_no_sync_of_its_own_folder(self):
        notes = os.path.join(self.root, "Regatta", "notes.txt")
        with open(notes, "w", encoding="utf-8") as handle:
            handle.write("race day")
        self.started(self.make())
        os.remove(notes)
        wait_until(lambda: self.synced)
        # Its parent is synced -- it might have been a folder -- never the file's name.
        self.assertEqual([paths.stored(os.path.join(self.root, "Regatta"))], self.folder_syncs())


class WhenNotificationsMayHaveBeenMissed(Base):
    def test_an_overflow_syncs_the_library_whole(self):
        watcher = self.started(self.make())
        with self.assertLogs("tagpup.jobs.watching", level="WARNING"):
            watcher.notice(paths.key(self.root), watching.Overflow(self.root, watching._Lately().said()))
        wait_until(lambda: self.synced)
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
        with mock.patch.object(watching, "_read_directory_changes", return_value=(b"", 0)):
            self.assertEqual([], emitter._read_events())
        self.assertEqual(1, len(queued))
        self.assertIsInstance(queued[0], watching.Overflow)
        # A read aborted -- the watch's stop cancels it -- is no overflow, running or not.
        with mock.patch.object(watching, "_read_directory_changes", return_value=(None, 0)):
            self.assertEqual([], emitter._read_events())
        self.assertEqual(1, len(queued))
        # And a watch being stopped reads nothing too, and that is no overflow.
        emitter.should_keep_running = lambda: False
        with mock.patch.object(watching, "_read_directory_changes", return_value=(b"", 0)):
            emitter._read_events()
        self.assertEqual(1, len(queued))

    def test_a_folder_not_there_is_watched_when_it_is_back_and_its_library_synced_whole(self):
        away = self.root + " (unplugged)"
        os.replace(self.root, away)
        watcher = self.make()
        watcher.start()
        wait_until(lambda: self.synced and [root["absent"] for root in watcher.status()["roots"]] == [True],
                   what="the first look to find the root not there, and the catch-up")
        self.assertEqual([], watcher.watched())
        self.assertEqual([("harbour", None)], self.synced, "the catch-up at start")
        del self.synced[:]
        os.replace(away, self.root)
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced, "not synced whole when its folder came back")
        del self.synced[:]
        a_jpeg(os.path.join(self.root, "Regatta", "Back.jpg"))
        wait_until(lambda: self.synced)
        self.assertEqual([paths.stored(os.path.join(self.root, "Regatta"))], self.folder_syncs())

    def test_a_watched_folder_removed_is_let_go_and_watched_again_when_it_is_back(self):
        watcher = self.started(self.make())
        shutil.rmtree(self.root)
        wait_until(lambda: not os.path.exists(self.root) and watcher.watched() == [])
        os.makedirs(os.path.join(self.root, "Regatta"))
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        wait_until(lambda: ("harbour", None) in self.synced)

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
            self.looked(2)
            self.assertEqual([paths.stored(self.root)], watcher.watched(), "a share that does not answer was let go")
        self.looked(2)
        self.assertTrue(self.idle(watcher), "synced whole as if it had come back")
        self.assertEqual([], self.synced, "synced whole as if it had come back")

    def test_a_watched_folder_renamed_is_noticed_from_its_parent_and_its_library_synced_whole(self):
        # The 30 s look at the folders is not what finds it: its watch sees nothing of its own rename.
        watcher = self.started(self.make(recheck=30.0))
        wait_until(lambda: watcher.watched_parents() == [paths.stored(self.home.root)])
        os.rename(self.root, self.root + " 2026")
        wait_until(lambda: ("harbour", None) in self.synced, 10)

    def test_a_folder_beside_a_watched_one_renamed_is_no_sync(self):
        watcher = self.started(self.make(recheck=30.0))
        wait_until(lambda: watcher.watched_parents() == [paths.stored(self.home.root)])
        os.makedirs(os.path.join(self.home.root, "Other"))
        os.rename(os.path.join(self.home.root, "Other"), os.path.join(self.home.root, "Other 2"))
        self.quiet(watcher, where=self.home.root)
        self.assertEqual([], self.synced)

    def test_a_library_in_too_many_separate_folders_is_not_watched_and_is_told_why(self):
        folders = [tempfile.mkdtemp(dir=self.home.root) for _ in range(3)]
        looks = []
        watcher = watching.Watcher(lambda: [self.library], lambda library: looks.append(library) or folders, self.sync,
                                   images.is_photo, debounce=0.2, recheck=0.2, tick=0.05, max_watches=2)
        self.addCleanup(watcher.stop, 10)
        with self.assertLogs("tagpup.jobs.watching", level="WARNING") as logged:
            watcher.start()
            wait_until(lambda: len(looks) > 2, what="two whole looks at the roots")
        self.assertEqual([], watcher.watched())
        self.assertEqual(1, sum("root folders" in line for line in logged.output), "said at every look")


@unittest.skipUnless(os.name == "nt", "Windows' notifications")
class WhatAWatchAsksWindowsFor(Base):
    """A watch asks Windows for names made, removed and renamed and for last writes only (watching.NOTIFY_FILTER):
    over the share one ExifTool write of a photo was 158 notifications under watchdog's whole filter, 6 under this
    (2026-10-30). Whatever changes a photo's row -- a photo written, renamed, deleted -- still syncs its folder."""

    def setUp(self):
        super().setUp()
        self.regatta = os.path.join(self.root, "Regatta")
        self.real_read = watching._read_directory_changes
        self.hold, self.held, self.release = threading.Event(), threading.Event(), threading.Event()
        self.failing = threading.Event()
        self.addCleanup(self.release.set)
        patcher = mock.patch.object(watching, "_read_directory_changes", self.read)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, handle, path, recursive):
        """The watch's read: as it is, held once its next read returned while `hold` is set, failing once while `fail`
        is."""
        if self.failing.is_set():
            self.failing.clear()
            raise OSError(64, "The specified network name is no longer available")
        found = self.real_read(handle, path, recursive)
        if self.hold.is_set() and found[1]:
            self.hold.clear()
            self.held.set()
            self.release.wait(30)
        return found

    def test_it_asks_for_names_and_last_writes_and_nothing_else(self):
        from watchdog.observers import winapi
        from watchdog.observers.api import EventQueue, ObservedWatch
        emitter = watching.make_observer()._emitter_class(EventQueue(), ObservedWatch(self.root, recursive=True))
        emitter._whandle = object()
        emitter.queue_event = lambda event: None
        asked = []
        with mock.patch.object(watching, "_read_directory_changes", self.real_read), \
                mock.patch.object(winapi, "ReadDirectoryChangesW",
                                  side_effect=lambda *args: asked.append(args[4]) or True):
            emitter._read_events()
        self.assertEqual([winapi.FILE_NOTIFY_CHANGE_FILE_NAME | winapi.FILE_NOTIFY_CHANGE_DIR_NAME
                          | winapi.FILE_NOTIFY_CHANGE_LAST_WRITE], asked)

    def test_a_photo_written_in_pieces_is_synced_after_its_last_piece(self):
        started = []
        self.sync = lambda library, folder: (started.append(time.monotonic()), self.synced.append((library.name, folder)))
        watcher = self.started(self.make(debounce=0.2))
        del started[:]
        photo = os.path.join(self.regatta, "Regatta - slow.jpg")
        whole = a_jpeg(os.path.join(self.home.root, "whole.jpg"))
        with open(whole, "rb") as handle:
            data = handle.read()
        with open(photo, "wb") as handle:
            for n in range(4):
                if n:
                    time.sleep(watcher.debounce + 2 * watcher.tick)    # wait: longer than the debounce, the folder settles early
                handle.write(data[n * len(data) // 4:(n + 1) * len(data) // 4])
                handle.flush()
                os.fsync(handle.fileno())
        last = time.monotonic()
        wait_until(lambda: started and started[-1] >= last)
        self.assertEqual({paths.stored(self.regatta)}, set(self.folder_syncs()))

    def test_a_burst_of_photos_made_renamed_and_deleted_is_one_sync_of_their_folder(self):
        watcher = self.started(self.make(debounce=1.0))
        with self.assertNoLogs("tagpup.jobs.watching", level="WARNING"):
            for n in range(40):
                made = a_jpeg(os.path.join(self.regatta, "Regatta - %d.jpg" % n))
                renamed = os.path.join(self.regatta, "Regatta - %d renamed.jpg" % n)
                os.replace(made, renamed)
                if n % 2:
                    os.remove(renamed)
            self.quiet(watcher)
        self.assertEqual([paths.stored(self.regatta)], self.folder_syncs())

    def test_a_photo_renamed_in_its_folder_syncs_it(self):
        a_jpeg(os.path.join(self.regatta, "Buoy.jpg"))
        watcher = self.started(self.make())
        os.replace(os.path.join(self.regatta, "Buoy.jpg"), os.path.join(self.regatta, "Buoy at dawn.jpg"))
        self.quiet(watcher)
        self.assertEqual([paths.stored(self.regatta)], self.folder_syncs())

    def test_an_overflow_names_its_folder_and_what_its_watch_read_before(self):
        self.started(self.make())
        self.hold.set()
        with self.assertLogs("tagpup.jobs.watching", level="WARNING") as said:
            a_jpeg(os.path.join(self.regatta, "Regatta - first.jpg"))
            wait_until(self.held.is_set)
            # While the watch's thread is held, Windows keeps what happens in its buffer, which these overfill.
            for n in range(400):
                with open(os.path.join(self.regatta, "%s %03d.txt" % ("a long name for a race note" * 4, n)), "w",
                          encoding="utf-8") as handle:
                    handle.write("lap")
            self.release.set()
            wait_until(lambda: self.synced)
        self.assertEqual([("harbour", None)], self.synced)
        line = [record.getMessage() for record in said.records if "overflowed" in record.getMessage()]
        self.assertEqual(1, len(line), said.output)
        self.assertIn(paths.stored(self.root), line[0])
        self.assertRegex(line[0], r"read \d+ notifications in the 60 s before \(1 added")

    def test_the_overflow_counts_only_the_last_minute(self):
        now = [1000.0]
        lately = watching._Lately(clock=lambda: now[0])
        lately.add([1, 3, 3, 4, 5])
        now[0] += 30
        lately.add([2])
        self.assertEqual("5 notifications in the 60 s before (1 added, 2 modified, 1 removed, 1 renamed)", lately.said())
        now[0] += 31
        self.assertEqual("1 notifications in the 60 s before (1 removed)", lately.said())
        now[0] += 60
        self.assertEqual("no notification in the 60 s before", lately.said())

    def test_a_watch_whose_read_fails_is_watched_again_and_its_library_synced_whole(self):
        watcher = self.started(self.make())
        self.failing.set()
        ended = []
        with self.assertLogs("tagpup.jobs.watching", level="WARNING") as said, \
                mock.patch.object(threading, "excepthook", lambda hooked: ended.append(hooked.exc_value)):
            # The read under way returns with this photo's notifications; the next one fails, as a share's that dropped.
            a_jpeg(os.path.join(self.regatta, "Regatta - 1.jpg"))
            wait_until(lambda: ("harbour", None) in self.synced)
        self.assertTrue(any("failed; watching it again" in line for line in said.output), said.output)
        self.assertEqual([64], [error.errno for error in ended])
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        self.quiet(watcher)
        del self.synced[:]
        a_jpeg(os.path.join(self.regatta, "Regatta - 2.jpg"))
        wait_until(lambda: self.synced)
        self.assertEqual([paths.stored(self.regatta)], self.folder_syncs())


class Stopping(Base):
    def test_waits_for_a_sync_under_way_and_then_watches_nothing(self):
        watcher = self.started(self.make(debounce=0.2))
        self.gate = threading.Event()
        a_jpeg(os.path.join(self.root, "Regatta", "Slow.jpg"))
        wait_until(watcher.busy)
        stopped = []
        stopping = threading.Thread(target=lambda: stopped.append(watcher.stop(timeout=20)), daemon=True)
        stopping.start()
        stopping.join(0.3)   # wait: the stop must still be waiting for the sync; nothing else says it is
        self.assertTrue(stopping.is_alive(), "stopped under a sync")
        self.gate.set()
        stopping.join(20)
        self.assertEqual([True], stopped)
        self.assertFalse(watcher.busy())
        del self.synced[:]
        after = a_jpeg(os.path.join(self.root, "Regatta", "After.jpg"))
        # Stopped, it hears nothing to wait for: a sync would come a debounce after the photo's notification.
        time.sleep(watcher.debounce + 4 * watcher.tick)   # wait: nothing may happen, and nothing is there to hear
        self.assertNotIn(paths.key(after), self.heard, "heard after it stopped")
        self.assertEqual([], self.synced)

    def test_watches_that_will_not_stop_are_left_behind_at_the_deadline(self):
        """An update while a share is away (2026-11-19): on Windows a watch on a share that stopped answering can block
        in watchdog's observer.stop() itself. The stop ends at its deadline and leaves them; started again, it watches
        anew. On the code before, stop() waited in observer.stop() for good."""
        let_go, stops = threading.Event(), []

        def stuck_observer():
            observer = watching.make_observer()
            stop = observer.stop

            def blocking_stop():
                stops.append(observer)
                let_go.wait()
                stop()
            observer.stop = blocking_stop
            return observer
        watcher = self.make(observer=stuck_observer)
        self.addCleanup(let_go.set)
        self.started(watcher)
        started = time.monotonic()
        self.assertTrue(watcher.stop(timeout=1))
        self.assertLess(time.monotonic() - started, 3, "the stop waited for watches that would not stop")
        self.assertEqual(1, len(stops))
        watcher.start()
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        let_go.set()

    def test_started_again_it_watches_again_without_the_catch_up(self):
        watcher = self.started(self.make())
        watcher.stop()
        watcher.start()
        wait_until(lambda: watcher.watched() == [paths.stored(self.root)])
        # Its first look notes a catch-up before it watches: once it watches, one would be waiting or synced.
        self.assertTrue(self.idle(watcher), "a catch-up noted when started again")
        self.assertEqual([], self.synced)
        a_jpeg(os.path.join(self.root, "Regatta", "Again.jpg"))
        wait_until(lambda: self.synced)
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
        wait_until(lambda: watcher.watched())
        # Its first look notes a catch-up before it watches: once it watches, one would be waiting or synced.
        self.assertTrue(self.idle(watcher), "a catch-up noted for a library synced whole lately")
        self.assertEqual([], self.synced)

    def test_is_made_for_one_that_was_not(self):
        watcher = self.make_with(lambda library: False)
        watcher.start()
        wait_until(lambda: self.synced)
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
        self.runtime = Runtime()      # the server's: its sync is the watcher's

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
            result = self.runtime.sync(library, folder=folder, apply=True, index_new=False)
        self.synced.append((library.name, folder))
        self.results.append(result)

    def watching(self, *libraries):
        # Half the server's DEBOUNCE: a pause longer than it between a file written and its row recorded (a loaded
        # machine) is a sync, rightly -- the row did not yet describe the file -- but not what these tests ask.
        watcher = runtimes.folder_watcher(libraries=lambda: list(libraries), sync_with=self.record,
                                          debounce=1.5, recheck=0.5, tick=0.05)
        self.addCleanup(watcher.stop, 20)
        self.hearing(watcher)
        watcher.start()
        wait_until(lambda: watcher.watched() == [paths.stored(self.regatta)])
        return watcher

    def settled(self, watcher, *libraries):
        """Wait until all this test did was heard (a file written after it is heard) and each library's notifications
        were noted and none waits or syncs."""
        barrier = os.path.join(self.regatta, "barrier.txt")
        with open(barrier, "w", encoding="utf-8") as handle:
            handle.write("heard")
        wait_until(lambda: paths.key(barrier) in self.heard, 60, what="the notification of " + barrier)

        def done():
            told = watcher.status()["libraries"]
            return all(told.get(library.name, {}).get("last_event") and not told[library.name]["pending_folders"]
                       and not told[library.name]["whole_pending"] for library in libraries)
        wait_until(done, 60)
        wait_until(lambda: not watcher.busy(), 60)

    def write(self, photos, library=None):
        written = tagging.change_tags(library or self.harbour, photos, ["Harbour"], [], exiftool_fields.opened(self, EXIFTOOL))
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
        time.sleep(0.2)   # wait: the scenario, a rename while the writes go on
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

    def test_it_is_busy_while_it_looks_whether_a_change_was_its_own(self):
        # Taken off the list, a folder was neither pending nor syncing while followed looked at its photos: a test
        # waiting for the watcher to settle read it as done before the sync ran (#2297), and so could an update.
        looking, answer = threading.Event(), threading.Event()

        def followed(library, photos):
            looking.set()
            answer.wait(30)
            return False

        watcher = watching.Watcher(lambda: [self.harbour], lambda library: [self.regatta], self.record, images.is_photo,
                                   debounce=0.3, recheck=0.5, tick=0.05, followed=followed,
                                   recent=lambda library: True)
        self.addCleanup(watcher.stop, 20)
        self.addCleanup(answer.set)
        watcher.start()
        wait_until(lambda: watcher.watched() == [paths.stored(self.regatta)])
        self.write(self.photos[:1])
        wait_until(looking.is_set)
        self.assertTrue(watcher.busy(), "not busy while it decides whether to sync")
        answer.set()
        wait_until(lambda: self.synced)
        wait_until(lambda: not watcher.busy())
        self.assertEqual([("harbour", paths.stored(self.regatta))], self.synced)

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

        runtime = Runtime()
        watcher = watching.Watcher(lambda: [library], lambda lib: [root],
                                   lambda lib, folder: runtime.sync(lib, folder=folder, apply=False),
                                   images.is_photo, whole=recurring.sync_whole, debounce=0.5, recheck=0.2, tick=0.05)
        app = web.create_app("tagpup", runtime=runtime, lifecycle=Lifecycle(background=runtimes.Background(
            [(runtimes.FOLDER_WATCHER, watcher)]), runtime=runtime))
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
            # A short walk, fewer than LOG_FOLDERS folders (one with no subfolder): no progress line, and once it is
            # over the folder it was at is no longer said.
            # (the library holds a photo of Dunes, or the folder's sync would be refused before any walk; a sync that found
            # nothing says its closing line at DEBUG)
            seed(library.path, os.path.join(root, "Dunes", "one.jpg"))
            with self.assertLogs("tagpup.services.sync", level="DEBUG") as short:
                watcher._run(library, os.path.join(root, "Dunes"))
            lines = short.output
            self.assertTrue(any("looked at 1 folder(s) and 1 photo file(s)" in line for line in lines),
                            "the folder was really walked: %s" % lines)
            self.assertEqual([], [line for line in lines if "so far" in line or re.search(r"Sync, \w+: \d+ folder", line)],
                             "a short walk said how far it got")
            self.assertIsNone(page.get("/api/activity/now").get_json()["syncing"])


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

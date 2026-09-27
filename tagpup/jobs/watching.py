"""Watching each library's folders, so what changes on disk outside the apps is synced when
it happens (docs/ARCHITECTURE.md, phase 8, "Event-driven first" and "Watching, with the
schedule as the safety net").

The always-on process runs one Watcher (a background task, tagpup.runtime.BACKGROUND). It
watches each library's folders -- its root folders and the folders it holds photos in
outside them, each the topmost of those under it -- with one recursive watch per folder
(Windows' directory-change notifications, through watchdog), a folder two libraries
share watched once for both. What a notification tells it is only noted: which folder of
which library changed, and when. Its own thread syncs a folder once no notification has
come for it for DEBOUNCE seconds, so a copy of 500 photos is one sync, not 500; the
watchdog threads never sync. A folder whose parent also waits is left to the parent's
sync, which walks it too.

Only photos matter (`concerns`, the photo extensions): a notification for any other file
made or changed is dropped. A folder deleted or moved is synced from its parent -- and so
is any other name deleted or moved away, since Windows reports a folder gone as a file
gone (watchdog 6 cannot ask what a name that no longer exists was). The app's own writes
are notified too, and cost a sync that finds the rows already describe their files: one
walk of the folder, no file read.

Notifications can be missed, so the whole library is synced
- when the watcher first starts (a catch-up for what happened while the process was
  not running);
- when Windows says its buffer overflowed, or a watch fails;
- when a folder that was not there -- a drive unplugged -- is back. A folder that is not
  there is looked for again every RECHECK, and the libraries and their folders are read
  again then too (a root folder added in the settings is watched within RECHECK).
The daily `sync` job stays as the safety net (tagpup.jobs.recurring).

`busy()` is true while a sync it started runs: an update waits for it
(tagpup.web.lifecycle). stop() stops the watches and waits for that sync to end; start()
after a stop watches again, without the catch-up.
"""
import logging
import os
import threading
import time

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from tagpup.core import paths

logger = logging.getLogger(__name__)

#: Seconds after the last notification for a folder before it is synced.
DEBOUNCE = 3.0

#: How often the libraries, their folders and the folders not there are looked at again.
RECHECK = 30.0

#: How often the watcher's thread looks for a folder whose notifications have settled.
TICK = 0.25

#: A library watched by more folders than this is not watched: without root folders set,
#: a library holding photos in thousands of folders would take a thread and a handle for
#: each. It is told to set its root folders, and the daily sync keeps it in step.
MAX_WATCHES = 64


class Overflow(FileSystemEvent):
    """Windows dropped notifications for a watched folder: its buffer overflowed."""
    event_type = "overflow"
    is_synthetic = True


def _windows_observer():
    """watchdog's Windows observer, told when ReadDirectoryChangesW overflows: Windows then
    returns no notification at all, which watchdog reads as none happening."""
    from watchdog.observers import winapi
    from watchdog.observers.api import BaseObserver
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    class OverflowingEmitter(WindowsApiEmitter):
        def _read_events(self):
            if not self._whandle:
                return []
            buffer, size = winapi.read_directory_changes(self._whandle, self.watch.path,
                                                         recursive=self.watch.is_recursive)
            if not size:
                # Also what a watch being stopped reads; only a running one overflowed.
                if self.should_keep_running():
                    self.queue_event(Overflow(self.watch.path))
                return []
            return [winapi.WinAPINativeEvent(action, path)
                    for action, path in winapi._parse_event_buffer(buffer, size)]

    return BaseObserver(emitter_class=OverflowingEmitter)


def make_observer():
    if os.name == "nt":
        return _windows_observer()
    from watchdog.observers import Observer
    return Observer()


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher, root_key):
        self.watcher, self.root_key = watcher, root_key

    def dispatch(self, event):
        try:
            self.watcher.notice(self.root_key, event)
        except Exception:
            logger.exception("A notification could not be noted")


class Watcher:
    """Watches the folders of `libraries()` -- `folders(library)` for each, the topmost
    folders to watch -- and calls `sync(library, folder)` for a folder whose notifications
    have settled, or `sync(library, None)` for the whole library. `concerns(path)` says
    whether a file matters (a photo). The rest are the timings above, a test's shorter."""

    def __init__(self, libraries, folders, sync, concerns, debounce=DEBOUNCE, recheck=RECHECK, tick=TICK,
                 clock=time.monotonic, observer=make_observer, max_watches=MAX_WATCHES):
        self._libraries, self._folders, self._sync, self._concerns = libraries, folders, sync, concerns
        self.debounce, self.recheck, self.tick, self._clock = debounce, recheck, tick, clock
        self._make_observer, self.max_watches = observer, max_watches
        self._lock = threading.Lock()
        #: root key -> {"path", "libraries": {library key: Library}, "watch", "absent"}.
        self._roots = {}
        #: library key -> {"library", "folders": {key: [folder, last noticed]}, "whole": time or None}.
        self._pending = {}
        self._syncing = 0
        self._stop = threading.Event()
        self._thread = None
        self._observer = None
        self._started_before = False
        self._too_many = set()

    # ---- The background task -----------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return self._thread
        stop = self._stop = threading.Event()
        observer = self._observer = self._make_observer()
        observer.start()
        with self._lock:
            for root in self._roots.values():
                root["watch"] = None
        catch_up, self._started_before = not self._started_before, True
        self._thread = threading.Thread(target=self._loop, args=(stop, observer, catch_up),
                                        name="FolderWatcherThread", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout=30):
        """Stop watching, and wait up to `timeout` for a sync under way to end. True when
        it has."""
        self._stop.set()
        thread, self._thread = self._thread, None
        observer, self._observer = self._observer, None
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout)
            except Exception as e:
                logger.error("Could not stop the folder watches: %s", e)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def busy(self):
        """Is a sync it started running?"""
        with self._lock:
            return self._syncing > 0

    def watched(self):
        """The folders watched now, as stored: for a test, and the log."""
        with self._lock:
            return sorted((root["path"] for root in self._roots.values() if root["watch"] is not None),
                          key=paths.key)

    # ---- Notifications, noted -----------------------------------------------------------

    def notice(self, root_key, event):
        """Note what `event`, from the watch on the folder `root_key`, says changed."""
        with self._lock:
            root = self._roots.get(root_key)
            libraries = list(root["libraries"].values()) if root else []
        if not libraries:
            return
        if isinstance(event, Overflow):
            logger.warning("Notifications for a watched folder were lost (its buffer overflowed); "
                           "syncing its libraries whole.")
            self._note_whole(libraries)
            return
        folders = []
        if event.is_directory:
            if event.event_type not in ("deleted", "moved"):
                # A folder made: its files are notified one by one. A folder modified:
                # every change inside it says so, and is notified for itself.
                return
            if paths.key(event.src_path) == root_key:
                # The watched folder itself is gone: its rows are missing now.
                self._note_whole(libraries)
                return
            folders.append(os.path.dirname(event.src_path))
            if event.event_type == "moved" and event.dest_path:
                folders.append(os.path.dirname(event.dest_path))
        else:
            for path in (event.src_path, getattr(event, "dest_path", "")):
                if path and self._concerns(path):
                    folders.append(os.path.dirname(path))
            if event.event_type in ("deleted", "moved") and not self._concerns(event.src_path):
                # Windows cannot say what a name that is gone was: a folder deleted or
                # moved out comes as a file deleted. Its photos' rows are its parent's
                # sync's to find missing (or moved).
                if paths.key(event.src_path) == root_key:
                    self._note_whole(libraries)
                    return
                folders.append(os.path.dirname(event.src_path))
            dest = getattr(event, "dest_path", "")
            if event.event_type == "moved" and dest and os.path.isdir(dest):
                folders.append(os.path.dirname(dest))
        if not folders:
            return
        now = self._clock()
        with self._lock:
            for library in libraries:
                pending = self._pending_for(library)
                for folder in folders:
                    pending["folders"][paths.key(folder)] = [paths.stored(folder), now]

    def _pending_for(self, library):
        """Under the lock."""
        return self._pending.setdefault(library.key, {"library": library, "folders": {}, "whole": None})

    def _note_whole(self, libraries):
        now = self._clock()
        with self._lock:
            for library in libraries:
                self._pending_for(library)["whole"] = now

    def _due(self):
        """[(library, folder or None)] whose notifications have settled, taken off the
        list: the whole library, or each folder no pending folder of it is under."""
        now = self._clock()
        due = []
        with self._lock:
            for pending in self._pending.values():
                library = pending["library"]
                if pending["whole"] is not None:
                    if now - pending["whole"] >= self.debounce:
                        due.append((library, None))
                        pending["whole"] = None
                        pending["folders"].clear()
                    continue
                folders = pending["folders"]
                taken = []
                for key, (folder, last) in sorted(folders.items()):
                    if now - last < self.debounce:
                        continue
                    if any(paths.is_under(folder, other) for other_key, (other, _l) in folders.items()
                           if other_key != key):
                        continue   # a folder it is under waits too, and its sync walks this one
                    taken.append(folder)
                # Taken, and whatever waits under them: their syncs walk it.
                for key in list(folders):
                    folder = folders[key][0]
                    if folder in taken or any(paths.is_under(folder, parent) for parent in taken):
                        del folders[key]
                due += [(library, folder) for folder in taken]
        return due

    # ---- The watcher's thread ------------------------------------------------------------

    def _loop(self, stop, observer, catch_up):
        try:
            self._look(observer, catch_up)
        except Exception:
            logger.exception("Looking at the folders to watch failed")
        next_look = self._clock() + self.recheck
        while not stop.wait(self.tick):
            if self._clock() >= next_look:
                try:
                    self._look(observer)
                except Exception:
                    logger.exception("Looking at the folders to watch failed")
                next_look = self._clock() + self.recheck
            for library, folder in self._due():
                if stop.is_set():
                    # Not synced now: the catch-up at the next start finds it.
                    break
                self._run(library, folder)

    def _run(self, library, folder):
        with self._lock:
            self._syncing += 1
        try:
            result = self._sync(library, folder)
            what = folder if folder else "every folder"
            if result is None:
                return
            if getattr(result, "refused", None):
                logger.debug("Sync of %s in %s refused: %s", what, library.name, result.refused)
                return
            details = getattr(result, "details", {}) or {}
            counts = details.get("counts", {})
            if result.changed or details.get("queued") or counts.get("missing"):
                logger.info("Synced %s in %s: %d row(s) changed, %d folder(s) queued to index, %d missing.",
                            what, library.name, result.changed, details.get("queued", 0), counts.get("missing", 0))
        except Exception:
            logger.exception("Syncing %s failed", library.name)
        finally:
            with self._lock:
                self._syncing -= 1

    def _look(self, observer, catch_up=False):
        """Read the libraries and their folders again; watch what is wanted and there, stop
        watching what is not; note the libraries to sync whole."""
        wanted = {}
        for library in self._libraries():
            try:
                folders = list(self._folders(library))
            except Exception as e:
                logger.error("Could not read the folders of %s to watch: %s", library.name, e)
                continue
            if len(folders) > self.max_watches:
                if library.key not in self._too_many:
                    logger.warning("%s holds photos in %d separate folders and has no root folders to cover "
                                   "them: it is not watched (the daily sync keeps it in step). Set its root "
                                   "folders to have it watched.", library.name, len(folders))
                    self._too_many.add(library.key)
                continue
            self._too_many.discard(library.key)
            for folder in folders:
                entry = wanted.setdefault(paths.key(folder), [paths.stored(folder), {}])
                entry[1][library.key] = library
            if catch_up and folders:
                self._note_whole([library])

        with self._lock:
            for key in [key for key in self._roots if key not in wanted]:
                self._unwatch(observer, self._roots.pop(key))
            for key, (folder, libraries) in wanted.items():
                root = self._roots.setdefault(key, {"path": folder, "libraries": {}, "watch": None, "absent": False})
                root["libraries"] = libraries
            roots = list(self._roots.items())

        for key, root in roots:
            there = os.path.isdir(root["path"])
            watch = root["watch"]
            if watch is not None and (not there or not _emitter_alive(observer, watch)):
                with self._lock:
                    self._unwatch(observer, root)
                if there:
                    logger.warning("The watch on %s failed; watching it again and syncing its libraries whole.",
                                   root["path"])
                    self._note_whole(list(root["libraries"].values()))
            if root["watch"] is None and there:
                try:
                    watch = observer.schedule(_Handler(self, key), root["path"], recursive=True)
                except Exception as e:
                    logger.error("Could not watch %s: %s", root["path"], e)
                    continue
                with self._lock:
                    root["watch"] = watch
                    came_back, root["absent"] = root["absent"], False
                if came_back:
                    logger.info("%s is back; syncing its libraries whole.", root["path"])
                    self._note_whole(list(root["libraries"].values()))
            elif not there and not root["absent"]:
                logger.info("%s is not there (a drive unplugged?); watching it when it is back.", root["path"])
                with self._lock:
                    root["absent"] = True

    @staticmethod
    def _unwatch(observer, root):
        """Under the lock."""
        watch, root["watch"] = root["watch"], None
        if watch is not None:
            try:
                observer.unschedule(watch)
            except Exception:
                pass


def _emitter_alive(observer, watch):
    """Is the thread reading `watch`'s notifications still running? It ends when its read
    fails: the folder gone, a network drive lost."""
    for emitter in getattr(observer, "emitters", ()):
        if emitter.watch == watch:
            return emitter.is_alive()
    return False

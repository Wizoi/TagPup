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
  not running), unless the library was synced whole in the last CATCH_UP_SKIP (a crash
  restart, an update);
- when Windows says its buffer overflowed, or a watch fails;
- when a folder that was not there -- a drive unplugged -- is back. A folder that is not
  there is looked for again every RECHECK, and the libraries and their folders are read
  again then too (a root folder added in the settings is watched within RECHECK).
The daily `sync` job stays as the safety net (tagpup.jobs.recurring).

The photos a notification names as made, written or moved in are noted too, and handed
to `recheck(library, photos)` before their folder's sync: a photo recorded damaged is read
again then, whatever its stamp (tagpup.services.damaged_photos.check_again). A good copy
laid over a damaged file can keep its modified time and its size, and the stamp alone
would never tell.

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

#: A library synced whole this lately gets no catch-up when the watcher starts (runtime).
CATCH_UP_SKIP = 60 * 60

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


class _ParentHandler(FileSystemEventHandler):
    """The watch on a watched folder's parent, which is told of the folder's own rename."""

    def __init__(self, watcher, parent_key):
        self.watcher, self.parent_key = watcher, parent_key

    def dispatch(self, event):
        try:
            self.watcher.notice_parent(self.parent_key, event)
        except Exception:
            logger.exception("A notification could not be noted")


class Watcher:
    """Watches the folders of `libraries()` -- `folders(library)` for each, the topmost
    folders to watch -- and calls `sync(library, folder)` for a folder whose notifications
    have settled, or `sync(library, None)` for the whole library. `concerns(path)` says
    whether a file matters (a photo). The rest are the timings above, a test's shorter."""

    def __init__(self, libraries, folders, sync, concerns, debounce=DEBOUNCE, recheck=RECHECK, tick=TICK,
                 clock=time.monotonic, observer=make_observer, max_watches=MAX_WATCHES, recent=None,
                 written=None):
        self._libraries, self._folders, self._sync, self._concerns = libraries, folders, sync, concerns
        #: written(library, photos): the photos a notification said were made, written or
        #: moved in, before their folder's sync (tagpup.runtime.check_damaged).
        self._written = written
        #: recent(library): was it synced whole lately? The catch-up at start is skipped
        #: for one that was -- a crash restart or an update need not walk it again.
        self._recent = recent or (lambda library: False)
        self.debounce, self.recheck, self.tick, self._clock = debounce, recheck, tick, clock
        self._make_observer, self.max_watches = observer, max_watches
        self._lock = threading.Lock()
        #: root key -> {"path", "libraries": {library key: Library}, "watch", "absent"}.
        self._roots = {}
        #: parent key -> {"path", "watch"}: the parent of each folder watched, watched without its
        #: subfolders, since a watch on a folder sees nothing of the folder's own rename.
        self._parents = {}
        self._parents_limited = False
        #: library key -> {"library", "folders": {key: [folder, last noticed]}, "whole": time or None,
        #: "files": {key: photo written}}.
        self._pending = {}
        self._syncing = 0
        self._stop = threading.Event()
        self._thread = None
        self._observer = None
        self._started_before = False
        self._too_many = set()
        #: For the Activity page (status): each library's name by key, when its folders
        #: last changed (a notification that concerned it, time.time()), the sync under
        #: way, and each library's last sync.
        self._names = {}
        self._last_event = {}
        self._current = None
        self._last_sync = {}

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
            for parent in self._parents.values():
                parent["watch"] = None
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

    def status(self):
        """What the Activity page shows: {"running", "roots": [{"path", "watched", "absent",
        "libraries"}], "libraries": {name: {"last_event", "pending_folders", "whole_pending",
        "last_sync", "not_watched"}}, "syncing": {"library", "folder", "started"} or None}."""
        with self._lock:
            roots = [{"path": root["path"], "watched": root["watch"] is not None, "absent": root["absent"],
                      "libraries": sorted(library.name for library in root["libraries"].values())}
                     for root in self._roots.values()]
            found = {}
            for key, name in self._names.items():
                pending = self._pending.get(key) or {"folders": {}, "whole": None}
                found[name] = {"last_event": self._last_event.get(key),
                               "pending_folders": len(pending["folders"]),
                               "whole_pending": pending["whole"] is not None,
                               "last_sync": dict(self._last_sync[key]) if key in self._last_sync else None,
                               "not_watched": key in self._too_many}
            syncing = dict(self._current) if self._current else None
        return {"running": self._thread is not None, "roots": sorted(roots, key=lambda root: paths.key(root["path"])),
                "libraries": found, "syncing": syncing}

    def watched(self):
        """The folders watched now, as stored: for a test, and the log."""
        with self._lock:
            return sorted((root["path"] for root in self._roots.values() if root["watch"] is not None),
                          key=paths.key)

    def watched_parents(self):
        """The parents of the watched folders, watched for a rename of one: for a test."""
        with self._lock:
            return sorted((parent["path"] for parent in self._parents.values() if parent["watch"] is not None),
                          key=paths.key)

    # ---- Notifications, noted -----------------------------------------------------------

    def notice_parent(self, parent_key, event):
        """Note what `event`, from the watch on the parent `parent_key` of watched folders, says:
        only a watched folder renamed, moved or deleted matters, and its libraries are synced
        whole -- its rows name a folder that is not there, which the sync reports as missing and the
        owner renames back by hand. Windows reports a folder gone as a file gone, so
        is_directory is not asked."""
        if event.event_type not in ("moved", "deleted"):
            return
        gone = paths.key(event.src_path)
        with self._lock:
            root = self._roots.get(gone)
            libraries = list(root["libraries"].values()) if root else []
        if libraries:
            logger.info("%s was renamed or removed; syncing its libraries whole.", event.src_path)
            self._note_whole(libraries)

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
        folders, written = [], []
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
            if event.event_type in ("created", "modified", "closed") and self._concerns(event.src_path):
                written.append(event.src_path)
            dest_path = getattr(event, "dest_path", "")
            if event.event_type == "moved" and dest_path and self._concerns(dest_path):
                written.append(dest_path)
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
        heard = _now()
        with self._lock:
            for library in libraries:
                pending = self._pending_for(library)
                self._last_event[library.key] = heard
                for folder in folders:
                    pending["folders"][paths.key(folder)] = [paths.stored(folder), now]
                for path in written:
                    pending["files"][paths.key(path)] = paths.stored(path)

    def _pending_for(self, library):
        """Under the lock."""
        return self._pending.setdefault(library.key, {"library": library, "folders": {}, "whole": None,
                                                      "files": {}})

    def _note_whole(self, libraries):
        now = self._clock()
        with self._lock:
            for library in libraries:
                self._pending_for(library)["whole"] = now

    def _due(self):
        """[(library, folder or None, photos written)] whose notifications have settled,
        taken off the list: the whole library, or each folder no pending folder of it is
        under, each with the photos noted as written in it."""
        now = self._clock()
        due = []
        with self._lock:
            for pending in self._pending.values():
                library = pending["library"]
                if pending["whole"] is not None:
                    if now - pending["whole"] >= self.debounce:
                        due.append((library, None, sorted(pending["files"].values(), key=paths.key)))
                        pending["whole"] = None
                        pending["folders"].clear()
                        pending["files"].clear()
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
                for folder in taken:
                    files = [path for key, path in pending["files"].items()
                             if paths.same(os.path.dirname(path), folder) or paths.is_under(path, folder)]
                    for path in files:
                        pending["files"].pop(paths.key(path), None)
                    due.append((library, folder, sorted(files, key=paths.key)))
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
            for library, folder, written in self._due():
                if stop.is_set():
                    # Not synced now: the catch-up at the next start finds it.
                    break
                self._run(library, folder, written)

    def _run(self, library, folder, written=()):
        with self._lock:
            self._syncing += 1
            self._current = {"library": library.name, "folder": folder, "started": _now()}
        result = None
        if written and self._written is not None:
            try:
                self._written(library, list(written))
            except Exception:
                logger.exception("Reading the photos written in %s again failed", library.name)
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
                started = (self._current or {}).get("started")
                self._current = None
                details = getattr(result, "details", None) or {}
                self._last_sync[library.key] = {
                    "folder": folder, "started": started, "finished": _now(),
                    "changed": getattr(result, "changed", 0) or 0, "queued": details.get("queued", 0),
                    "refused": bool(getattr(result, "refused", None)), "failed": result is None}

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
            with self._lock:
                self._names[library.key] = library.name
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
                try:
                    lately = self._recent(library)
                except Exception as e:
                    logger.error("Could not read when %s was last synced: %s", library.name, e)
                    lately = False
                if lately:
                    logger.info("%s was synced whole lately; no catch-up.", library.name)
                else:
                    self._note_whole([library])

        with self._lock:
            let_go = [self._detach(self._roots.pop(key)) for key in [key for key in self._roots if key not in wanted]]
            for key, (folder, libraries) in wanted.items():
                root = self._roots.setdefault(key, {"path": folder, "libraries": {}, "watch": None, "absent": False})
                root["libraries"] = libraries
            roots = list(self._roots.items())
        # Unscheduled outside the lock: watchdog dispatches a notification with its own lock held and
        # that calls notice(), which takes this one; unscheduling takes its lock while holding this
        # one is the other order, and each waits for the other.
        for watch in let_go:
            self._unschedule(observer, watch)

        for key, root in roots:
            there = os.path.isdir(root["path"])
            watch = root["watch"]
            if watch is not None and (not there or not _emitter_alive(observer, watch)):
                with self._lock:
                    dead = self._detach(root)
                self._unschedule(observer, dead)
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
        self._watch_parents(observer)

    def _watch_parents(self, observer):
        """Watch the parent of each watched folder, without its subfolders, and let go of
        those no folder needs. They count toward max_watches with the folders: none is made
        beyond it. A parent whose watch died is watched again, and the libraries of the folders
        in it synced whole, as for a folder."""
        with self._lock:
            wanted, children = {}, {}
            for root in self._roots.values():
                parent = os.path.dirname(root["path"])
                if root["watch"] is not None and parent and not paths.same(parent, root["path"]):
                    wanted.setdefault(paths.key(parent), paths.stored(parent))
                    children.setdefault(paths.key(parent), []).extend(root["libraries"].values())
            let_go = [self._detach(self._parents.pop(key)) for key in [key for key in self._parents if key not in wanted]]
            held = [(key, parent["watch"]) for key, parent in self._parents.items() if parent["watch"] is not None]
        for watch in let_go:
            self._unschedule(observer, watch)
        died = [key for key, watch in held if not _emitter_alive(observer, watch)]
        with self._lock:
            dead_watches = [self._detach(self._parents[key]) for key in died if key in self._parents]
            missing = [(key, folder) for key, folder in wanted.items()
                       if key not in self._parents or self._parents[key]["watch"] is None]
            budget = self.max_watches - sum(1 for root in self._roots.values() if root["watch"] is not None) \
                - sum(1 for parent in self._parents.values() if parent["watch"] is not None)
        for watch in dead_watches:
            self._unschedule(observer, watch)
        for key in died:
            if os.path.isdir(wanted.get(key, "")):
                logger.warning("The watch on %s failed; watching it again and syncing its libraries whole.", wanted[key])
                self._note_whole(children.get(key, []))
        for key, folder in missing:
            if budget <= 0:
                if not self._parents_limited:
                    self._parents_limited = True
                    logger.info("Not watching %s, nor the parents of other watched folders, for a rename of a folder in "
                                "them: the watches are at their limit (%d).", folder, self.max_watches)
                break
            try:
                watch = observer.schedule(_ParentHandler(self, key), folder, recursive=False)
            except Exception as e:
                logger.error("Could not watch %s for a rename of a folder in it: %s", folder, e)
                continue
            budget -= 1
            with self._lock:
                self._parents[key] = {"path": folder, "watch": watch}

    @staticmethod
    def _detach(root):
        """Take the watch off `root` (a watched folder, or a parent), and give it back to be
        unscheduled. Under the lock; the unscheduling is not (_unschedule)."""
        watch, root["watch"] = root["watch"], None
        return watch

    @staticmethod
    def _unschedule(observer, watch):
        """Stop a watch _detach gave back. Never under the lock."""
        if watch is not None:
            try:
                observer.unschedule(watch)
            except Exception:
                pass


def _now():
    """The time as the Activity page shows it."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _emitter_alive(observer, watch):
    """Is the thread reading `watch`'s notifications still running? It ends when its read
    fails: the folder gone, a network drive lost."""
    for emitter in getattr(observer, "emitters", ()):
        if emitter.watch == watch:
            return emitter.is_alive()
    return False

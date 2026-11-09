"""Watching each library's folders, so what changes on disk outside the apps is synced when
it happens (docs/ARCHITECTURE.md, phase 8, "Event-driven first" and "Watching, with the
schedule as the safety net").

The server runs one Watcher (a background task, tagpup.runtime.BACKGROUND). It
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
gone (watchdog 6 cannot ask what a name that no longer exists was).

Noting a notification looks at no path of our own: it runs on watchdog's one dispatch thread, every library's, and a look
at a share that sleeps would hold them all (watchdog itself asks `os.path.isdir` of a renamed name there, unbounded). Windows reports a rename only within one folder (a move between folders is a name
removed and one added), and watchdog has already asked whether the new name is a folder; a rename it called a file's is
synced from the folder of its old name, which is the new name's folder too.

TagPup's own writes are notified too -- the index's bake, an identity minted, a save, a
rename -- from whichever process made them: ExifTool writes beside a photo and renames its
file over it. A folder whose notifications named only photos (at most NAMED_MOST), and none
of them a folder, is first asked `followed(library, photos)` (tagpup.services.sync.followed):
when the library's rows already describe each as it is now, which every TagPup write leaves
so, it is not synced. A bake of photo_index synced its folder once for each file it wrote, 7
or 8 times a minute (2026-10-28). Anything else -- a photo added, edited, renamed or deleted
elsewhere, in the same folder at the same moment included -- is not described, and the folder
is synced as before. A name renamed onto a photo's was a file, never a folder.

Notifications can be missed, so the whole library is synced
- when the watcher first starts (a catch-up for what happened while the process was
  not running), unless the library was synced whole in the last CATCH_UP_SKIP (a crash
  restart, an update);
- when Windows says its buffer overflowed, or a watch fails; the log line of an overflow names the folder and how many
  notifications of each kind its watch read in the COUNTED seconds before, what it knows of the cause;
- when a folder that was not there -- a drive unplugged -- is back. A folder that is not
  there is looked for again every RECHECK, and the libraries and their folders are read
  again then too (a root folder added in the settings is watched within RECHECK). One on a
  share that did not answer is looked for again as soon as the share answers (shares.on_back,
  through sync.watch_states: a tick, not RECHECK, #2101); RECHECK stays the safety net for a
  share that never says so.
The daily `sync` job stays as the safety net (tagpup.jobs.recurring).

One walk of a library at a time (#2102): a whole sync is handed to `whole(library, call, noted)`, which the server
wires to recurring.sync_whole -- a run of the daily `sync` job, claimed as the runner's are. It is not run when a run
that ended done started since `noted`, when it was first wanted (time.time()); refused "running" while the runner walks
the library, it is asked again RECHECK later, still wanted since then. A folder back from a share that did not answer
is wanted since the share was first found not answering, so the catch-up that waited for it (sync.FOLDER_WAIT) and
looked at every folder covers it; one back from gone is wanted from now, since a sync that found it gone ended done.
A folder's sync is handed to `one_folder(library, call)`, which the server wires to recurring.sync_folder: it is not run
while a run of the `sync` job walks the library, and is asked again RECHECK later, with what its notifications named
(#2755); a run of the job asked while a folder's sync walks is told "running" in its turn.

A watch asks Windows only for what can change a photo's row (NOTIFY_FILTER): a name made, removed or renamed, and a
file's last write -- the row's stamp is (mtime, size), and a file's size never changes without its last write. Watchdog's
own filter asks for every kind of change besides; over the share one ExifTool write of a 7.1 MB photo was 158
notifications under it, 6 under this one, and the writes took 4.7 s a file against 3.0 s (2026-10-30).

`busy()` is true while a sync it started runs, and from when a folder's notifications settled until it is synced or
found TagPup's own: an update waits for it (tagpup.web.lifecycle). stop() stops the watches and waits for that sync to end; start()
after a stop watches again, without the catch-up.
"""
import collections
import logging
import os
import threading
import time

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from tagpup.core import paths
from tagpup.services import job_runs
from tagpup.services import sync as sync_service

logger = logging.getLogger(__name__)

#: Seconds after the last notification for a folder before it is synced.
DEBOUNCE = 3.0

#: A library synced whole this lately gets no catch-up when the watcher starts (runtime).
CATCH_UP_SKIP = 60 * 60

#: How often the libraries, their folders and the folders not there are looked at again.
RECHECK = 30.0

#: How often the watcher's thread looks for a folder whose notifications have settled.
TICK = 0.25

#: The most photos a folder's notifications may name for the watcher to ask whether they were TagPup's own writes
#: (followed): each is a look at its file, over a share a round trip. The look ends at the first photo not described, so a
#: long list costs only when every one was TagPup's own; the bake's largest write of one folder of photo_index wrote 312
#: files (2026-10-28), and a page's batch is at most batches.MAX_BATCH.
NAMED_MOST = 500

#: A library watched by more folders than this is not watched: without root folders set,
#: a library holding photos in thousands of folders would take a thread and a handle for
#: each. It is told to set its root folders, and the daily sync keeps it in step.
MAX_WATCHES = 64

#: What a watch asks Windows to notify: FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME |
#: FILE_NOTIFY_CHANGE_LAST_WRITE. Not sizes (a size changes with a last write), attributes, security, creation or
#: last-access times (none is in a row; the server keeps no last-access): watchdog's WATCHDOG_FILE_NOTIFY_FLAGS, 0x17f.
NOTIFY_FILTER = 0x01 | 0x02 | 0x10

#: The seconds of notifications an overflow's log line counts.
COUNTED = 60

#: The kinds of notification counted, by Windows' FILE_ACTION_*: a rename is counted once, by its old name.
_KINDS = {1: "added", 2: "removed", 3: "modified", 4: "renamed"}


class Overflow(FileSystemEvent):
    """Windows dropped notifications for a watched folder: its buffer overflowed. `read_lately` says how many its
    watch read in the COUNTED seconds before (_Lately.said)."""
    event_type = "overflow"
    is_synthetic = True

    def __init__(self, src_path, read_lately):
        super().__init__(src_path)
        self.read_lately = read_lately


class _Lately:
    """How many notifications of each kind a watch read in the last COUNTED seconds: a count for each second, at most
    COUNTED of them kept. Only its emitter's thread touches it."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._seconds = collections.deque()     # [second, Counter of kinds]

    def add(self, actions):
        now = int(self._clock())
        if not self._seconds or self._seconds[-1][0] != now:
            self._seconds.append([now, collections.Counter()])
        self._seconds[-1][1].update(_KINDS[action] for action in actions if action in _KINDS)
        self._forget(now)

    def said(self):
        self._forget(int(self._clock()))
        kinds = collections.Counter()
        for _second, counted in self._seconds:
            kinds.update(counted)
        if not kinds:
            return "no notification in the %d s before" % COUNTED
        return "%d notifications in the %d s before (%s)" % (
            sum(kinds.values()), COUNTED, ", ".join("%d %s" % (n, kind) for kind, n in sorted(kinds.items())))

    def _forget(self, now):
        while self._seconds and self._seconds[0][0] <= now - COUNTED:
            self._seconds.popleft()


def _read_directory_changes(handle, path, recursive):
    """One ReadDirectoryChangesW of `handle` with NOTIFY_FILTER: watchdog's winapi.read_directory_changes, which asks
    for its own filter. (buffer, bytes read); 0 bytes when Windows' buffer overflowed. (None, 0) for a read aborted,
    which only the watch's stop makes (winapi.close_directory_handle cancels it). The watched folder deleted reads as
    watchdog's deleted-self; any other failure -- a share that stops answering -- is raised, and ends the watch's
    thread, which the watcher finds and watches again (_emitter_alive)."""
    import ctypes
    from ctypes.wintypes import DWORD

    from watchdog.observers import winapi
    buffer = ctypes.create_string_buffer(winapi.BUFFER_SIZE)
    read = DWORD()
    try:
        winapi.ReadDirectoryChangesW(handle, ctypes.byref(buffer), len(buffer), recursive, NOTIFY_FILTER,
                                     ctypes.byref(read), None, None)
    except OSError as e:
        if e.winerror == winapi.ERROR_OPERATION_ABORTED:
            return None, 0
        if winapi._is_observed_path_deleted(handle, path):
            return winapi._generate_observed_path_deleted_event()
        raise
    return buffer.raw, int(read.value)


def _windows_observer():
    """watchdog's Windows observer, asking for NOTIFY_FILTER and told when ReadDirectoryChangesW overflows: Windows
    then returns no notification at all, which watchdog reads as none happening."""
    from watchdog.observers import winapi
    from watchdog.observers.api import BaseObserver
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    class OverflowingEmitter(WindowsApiEmitter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.read_lately = _Lately()

        def _read_events(self):
            if not self._whandle:
                return []
            buffer, size = _read_directory_changes(self._whandle, self.watch.path, self.watch.is_recursive)
            if buffer is None:
                return []
            if not size:
                # A watch being stopped may read nothing too; only a running one overflowed.
                if self.should_keep_running():
                    self.queue_event(Overflow(self.watch.path, self.read_lately.said()))
                return []
            events = [winapi.WinAPINativeEvent(action, path)
                      for action, path in winapi._parse_event_buffer(buffer, size)]
            self.read_lately.add(event.action for event in events)
            return events

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
    have settled, or `sync(library, None)` for the whole library, through `whole(library, call, noted)` when given
    (recurring.sync_whole: its Outcome; see above). `concerns(path)` says whether a file matters (a photo);
    `followed(library, photos)` whether the library's rows already describe the photos a folder's notifications named
    (sync.followed: then the folder is not synced). The rest are the timings above, a test's shorter."""

    def __init__(self, libraries, folders, sync, concerns, debounce=DEBOUNCE, recheck=RECHECK, tick=TICK,
                 clock=time.monotonic, observer=make_observer, max_watches=MAX_WATCHES, recent=None, whole=None,
                 followed=None, one_folder=None):
        self._libraries, self._folders, self._sync, self._concerns = libraries, folders, sync, concerns
        self._followed = followed or (lambda library, photos: False)
        #: whole(library, call, noted) -> an Outcome: the whole library's sync, claimed (None: call() at once).
        self._whole = whole
        #: one_folder(library, call) -> call()'s Result, or None when it was not called: a folder's sync, taking the
        #: library's walk (recurring.sync_folder; None: call() at once).
        self._one_folder = one_folder
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
        #: library key -> {"library", "folders": {key: [folder, last noticed, named]}, "whole": time or None,
        #: "noted": since when (time.time()) the whole sync is wanted, or None}; `named` is {key: path} of the photos
        #: its notifications named, or None when one named a folder, or more than NAMED_MOST photos (_named).
        self._pending = {}
        #: Syncs running, and settled folders taken off the list (_due) not yet synced or found TagPup's own: busy().
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
        #: Set when a share that did not answer answers again (_heard_back): the thread looks at the folders at its next tick.
        self._look_soon = threading.Event()

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
        """Is a sync it started running, or a folder whose notifications settled waiting for one (_due)?"""
        with self._lock:
            return self._syncing > 0

    def status(self):
        """What the Activity page shows: {"running", "roots": [{"path", "watched", "absent",
        "libraries"}], "libraries": {name: {"last_event", "pending_folders", "whole_pending",
        "last_sync", "not_watched"}}, "syncing": {"library", "folder", "started", "walking_at"} or None}: `walking_at`,
        the folder its walk is at (tagpup.services.sync.walking_at_into), the one part of how far it got held here; its
        phase and counts are its run's row, the `sync` job's (recurring.sync_whole; tagpup.web.activity_routes)."""
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
            if syncing is not None:
                syncing["walking_at"] = syncing["walking_at"]["folder"]
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
            logger.warning("Notifications for the watched folder %s were lost (its buffer overflowed) after its watch "
                           "read %s; syncing its libraries whole.", event.src_path, event.read_lately)
            self._note_whole(libraries)
            return
        # (folder, the photo in it the notification named, or None: what changed in it is the folder's walk to find)
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
            folders.append((os.path.dirname(event.src_path), None))
            if event.event_type == "moved" and event.dest_path:
                folders.append((os.path.dirname(event.dest_path), None))
        else:
            dest = getattr(event, "dest_path", "")
            for path in (event.src_path, dest):
                if path and self._concerns(path):
                    folders.append((os.path.dirname(path), path))
            # Renamed onto a photo's name, it was a file: ExifTool's own beside the photo it wrote, never a folder.
            onto_a_photo = event.event_type == "moved" and dest and self._concerns(dest)
            if event.event_type in ("deleted", "moved") and not self._concerns(event.src_path) and not onto_a_photo:
                # Windows cannot say what a name that is gone was: a folder deleted or
                # moved out comes as a file deleted. Its photos' rows are its parent's
                # sync's to find missing (or moved).
                if paths.key(event.src_path) == root_key:
                    self._note_whole(libraries)
                    return
                folders.append((os.path.dirname(event.src_path), None))
        if not folders:
            return
        now = self._clock()
        wall = time.time()
        heard = job_runs.stamp(wall)
        with self._lock:
            for library in libraries:
                pending = self._pending_for(library)
                self._last_event[library.key] = heard
                for folder, photo in folders:
                    key = paths.key(folder)
                    held = pending["folders"].get(key)
                    pending["folders"][key] = [paths.stored(folder), now,
                                               _named({} if held is None else held[2], {} if photo is None else
                                                      {paths.key(photo): paths.stored(photo)}, photo is None)]
                if pending["noted"] is not None:
                    # Left to the whole sync that waits (_due): one that started before this change did not see it.
                    pending["noted"] = max(pending["noted"], wall)

    def _pending_for(self, library):
        """Under the lock."""
        return self._pending.setdefault(library.key, {"library": library, "folders": {}, "whole": None, "noted": None})

    def _note_whole(self, libraries, noted=None, at=None):
        """Sync `libraries` whole once DEBOUNCE has passed since `at` (the clock; now when None), wanted since `noted`
        (time.time(); now when None). Wanted twice before it runs, it is wanted since the later: a sync that started
        between the two did not see what the second says."""
        when = self._clock() if at is None else at
        since = time.time() if noted is None else noted
        with self._lock:
            for library in libraries:
                pending = self._pending_for(library)
                if at is None or pending["whole"] is None:
                    pending["whole"] = when
                pending["noted"] = since if pending["noted"] is None else max(pending["noted"], since)

    def _due(self):
        """[(library, folder or None, noted, named)] whose notifications have settled, taken off the
        list: the whole library (since when it is wanted; named None), or each folder no pending folder of
        it is under (noted None; named, the photos the notifications of it and of the folders under it named,
        or None: _named). Each is counted in busy() until _loop has decided it (_decided): taken off the list,
        it is not pending either, and a look at whether it was TagPup's own takes a round trip a photo."""
        now = self._clock()
        due = []
        with self._lock:
            for pending in self._pending.values():
                library = pending["library"]
                if pending["whole"] is not None:
                    if now - pending["whole"] >= self.debounce:
                        due.append((library, None, pending["noted"], None))
                        pending["whole"] = pending["noted"] = None
                        pending["folders"].clear()
                    continue
                folders = pending["folders"]
                taken = []
                for key, (folder, last, _its) in sorted(folders.items()):
                    if now - last < self.debounce:
                        continue
                    if any(paths.is_under(folder, other) for other_key, (other, _l, _n) in folders.items()
                           if other_key != key):
                        continue   # a folder it is under waits too, and its sync walks this one
                    taken.append(folder)
                # Taken, and whatever waits under them: their syncs walk it, and what they named is the taken one's.
                named = {paths.key(folder): {} for folder in taken}
                for key in list(folders):
                    folder, _last, its = folders[key]
                    into = [parent for parent in taken if folder == parent or paths.is_under(folder, parent)]
                    if into:
                        held = named[paths.key(into[0])]
                        named[paths.key(into[0])] = _named(held, its or {}, its is None)
                        del folders[key]
                for folder in taken:
                    due.append((library, folder, None, named[paths.key(folder)]))
            self._syncing += len(due)
        return due

    def _decided(self):
        with self._lock:
            self._syncing -= 1

    # ---- The watcher's thread ------------------------------------------------------------

    def _loop(self, stop, observer, catch_up):
        try:
            self._look(observer, catch_up)
        except Exception:
            logger.exception("Looking at the folders to watch failed")
        next_look = self._clock() + self.recheck
        while not stop.wait(self.tick):
            if self._clock() >= next_look or self._look_soon.is_set():
                self._look_soon.clear()
                try:
                    self._look(observer)
                except Exception:
                    logger.exception("Looking at the folders to watch failed")
                next_look = self._clock() + self.recheck
            for library, folder, noted, named in self._due():
                try:
                    if stop.is_set():
                        # Not synced now: the catch-up at the next start finds it.
                        continue
                    if folder is not None and named and self._own(library, folder, named):
                        continue
                    self._run(library, folder, noted, named)
                finally:
                    self._decided()

    def _own(self, library, folder, named):
        """Were the notifications of `folder` TagPup's own writes, the photos they `named` described by `library`'s rows
        as they are now (followed)? A look that fails is no: the folder is synced."""
        try:
            own = self._followed(library, list(named.values()))
        except Exception as e:
            logger.warning("Could not tell whether the change of %s in %s was TagPup's own (%s); syncing it.",
                           folder, library.name, e)
            return False
        if own:
            logger.debug("The change of %d photo(s) in %s in %s was TagPup's own write; not synced.",
                         len(named), folder, library.name)
        return own

    def _heard_back(self, share):
        """A share a folder is on answers again (shares.on_back, on the thread of the look that heard it): look at the
        folders at the next tick."""
        logger.info("%s answers again; looking at the folders on it now.", share)
        self._look_soon.set()

    def _run(self, library, folder, noted=None, named=None):
        with self._lock:
            self._syncing += 1
            at = {"folder": None}
            self._current = {"library": library.name, "folder": folder, "started": job_runs.stamp(time.time()),
                             "walking_at": at}
        result, ran = None, True
        try:
            if folder is None and self._whole is not None:
                outcome = self._whole(library, lambda: self._synced(library, None, at), noted)
                if not outcome.ran:
                    ran = False
                    self._not_run(library, outcome.why, noted)
                    return
                result = outcome.result
            elif self._one_folder is not None:
                result = self._one_folder(library, lambda: self._synced(library, folder, at))
                if result is None:
                    ran = False
                    self._folder_later(library, folder, named)
                    return
            else:
                # What it found, changed and removed, and how it ended, is the sync's own closing line
                # (tagpup.services.sync._said), under its run tag: the watcher writes no second one.
                result = self._synced(library, folder, at)
        except Exception:
            logger.exception("Syncing %s failed", library.name)
        finally:
            with self._lock:
                self._syncing -= 1
                started = (self._current or {}).get("started")
                self._current = None
                details = getattr(result, "details", None) or {}
                if ran:
                    self._last_sync[library.key] = {
                        "folder": folder, "started": started, "finished": job_runs.stamp(time.time()),
                        "changed": getattr(result, "changed", 0) or 0, "queued": details.get("queued", 0),
                        "refused": bool(getattr(result, "refused", None)), "failed": result is None}

    def _synced(self, library, folder, at):
        """The sync of `folder` (None: the whole library), the folder its walk is at kept in `at` meanwhile (status)."""
        with sync_service.walking_at_into(at):
            return self._sync(library, folder)

    def _folder_later(self, library, folder, named):
        """The sync of `folder` was not run: a run of the `sync` job walks `library` (one_folder). It is wanted again
        RECHECK later, what its notifications `named` kept, merged with what was noted of it meanwhile; a whole sync that
        waits is wanted since now, as a notification makes it (notice)."""
        logger.info("%s is being synced whole by another run; syncing a folder of it in %d s.", library.name, self.recheck)
        at = self._clock() + self.recheck - self.debounce
        key = paths.key(folder)
        with self._lock:
            pending = self._pending_for(library)
            held = pending["folders"].get(key)
            if held is None:
                pending["folders"][key] = [paths.stored(folder), at, named]
            else:
                held[1], held[2] = max(held[1], at), _named(held[2], named, False)
            if pending["noted"] is not None:
                pending["noted"] = max(pending["noted"], time.time())

    def _not_run(self, library, why, noted):
        """The whole sync of `library` was not run, `why` (recurring.Outcome.why): "not due", a run since it was wanted
        looked at every folder; "running", another (the daily job) walks it now, and it is asked again RECHECK later,
        wanted since the same time; anything else (the claim could not be written) is logged and not asked again: the
        daily job is the safety net."""
        if why == "not due":
            logger.info("%s was synced whole since it was wanted; not walked again.", library.name)
        elif why == "running":
            logger.info("%s is being synced whole by another run; asking again in %d s.", library.name, self.recheck)
            self._note_whole([library], noted=noted, at=self._clock() + self.recheck - self.debounce)
        else:
            logger.warning("The whole sync of %s was not run (%s); the daily sync job will.", library.name, why)

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

        # A share that does not answer is neither there nor gone (sync.watch_states): a watch on it is
        # kept while its emitter lives, and none is made until it answers.
        states = sync_service.watch_states([root["path"] for _key, root in roots], on_back=self._heard_back)
        for key, root in roots:
            state = states[paths.key(root["path"])]
            there = state == "there"
            with self._lock:
                # Since when its share has not answered (time.time()): what a whole sync on its return must have started
                # after. A root found gone forgets it: a sync that found it gone ended done, and must not cover its return.
                if state == "unanswered":
                    root.setdefault("away_at", time.time())
                elif state == "gone":
                    root.pop("away_at", None)
            watch = root["watch"]
            if watch is not None and (state == "gone" or not _emitter_alive(observer, watch)):
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
                    away_at = root.pop("away_at", None)
                    libraries = list(root["libraries"].values())
                if came_back:
                    logger.info("%s is back; syncing its libraries whole.", root["path"])
                    self._note_whole(libraries, noted=away_at)
            elif root["watch"] is None and not there and not root["absent"]:
                logger.info("%s is not there or does not answer (a drive unplugged?); watching it when it is back.",
                            root["path"])
                with self._lock:
                    root["absent"] = True
            if there:
                with self._lock:
                    root.pop("away_at", None)
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
        states = sync_service.watch_states([wanted[key] for key in died if key in wanted], on_back=self._heard_back)
        for key in died:
            if key in wanted and states[paths.key(wanted[key])] == "there":
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


def _named(held, more, a_folder):
    """What a pending folder's notifications named, `held`, with `more` ({key: photo}) added: None when either is None
    or `a_folder` (a notification of a folder, or of a name that may have been one), or once past NAMED_MOST photos:
    the folder is walked."""
    if held is None or more is None or a_folder:
        return None
    merged = dict(held)
    merged.update(more)
    return merged if len(merged) <= NAMED_MOST else None


def _emitter_alive(observer, watch):
    """Is the thread reading `watch`'s notifications still running? It ends when its read
    fails: the folder gone, a network drive lost."""
    for emitter in getattr(observer, "emitters", ()):
        if emitter.watch == watch:
            return emitter.is_alive()
    return False

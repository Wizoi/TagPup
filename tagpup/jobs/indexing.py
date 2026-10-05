"""Folders waiting to be added to a library, and the one worker adding them.

Indexing is GPU-bound, so a library's folders are indexed one at a time: two at once do
not go twice as fast so much as make each other crawl. Asking for ten should still be
one action, not a wait beside the machine between each.

TagTuner had this queue. TagPup started a thread per folder, so two folders asked for
together were indexed at once. A process has one queue per library, which lasts as long
as the process: nothing about it is saved yet (docs/ARCHITECTURE.md, the `jobs` table).

Each run of the indexer has a tag (tagpup.core.runs.index_tag), held while it runs, so
its lines in this process's log and in the indexer's own (the CLI it starts writes
indexer-<library>-<run>.log) can be shown together; and the queue keeps what became of its last
HISTORY runs, for the Activity page -- in this process, as the queue itself is.
"""
import logging
import os
import threading
import time

from tagpup.core import gpu_turns, paths, runs
from tagpup.core.result import Result

logger = logging.getLogger(__name__)

#: What a folder nobody has asked about reports.
READY = {"status": "completed", "percent": 100, "message": "Ready"}

#: How many ended runs of the indexer each library's queue remembers.
HISTORY = 50

_queues = {}
_queues_lock = threading.Lock()


def _folders_of(job):
    """The folders a job indexes: one, or a batch's (IndexQueue.start's `together`)."""
    return job.get("folders") or [job["folder"]]


def queue_for(library):
    """This process's queue for a library, made the first time it is asked for."""
    with _queues_lock:
        queue = _queues.get(library.key)
        if queue is None:
            queue = _queues[library.key] = IndexQueue(library.name)
        return queue


def every_queue():
    """[(library name, queue)] of every library this process has a queue for."""
    with _queues_lock:
        return [(queue.library, queue) for queue in _queues.values()]


def running():
    """How many libraries have folders being indexed or waiting in this process: what an
    update of the always-on process waits for (tagpup.web.lifecycle)."""
    with _queues_lock:
        queues = list(_queues.values())
    return sum(1 for queue in queues if queue.busy())


def forget(library):
    """Drop a library's queue and what became of its folders. A worker still indexing
    one finishes it, into a queue nothing reads."""
    with _queues_lock:
        _queues.pop(library.key, None)


class _Progress:
    """What a run of the indexer reports through: `report(message, percent)` into its
    status, and `stop_file`, set by tagpup.services.indexing while its indexer runs: the
    file that stops it while it waits for the graphics card."""

    def __init__(self, status):
        self.status = status
        self.stop_file = None

    def __call__(self, message=None, percent=None):
        if message is not None:
            self.status["message"] = message
        if percent is not None:
            self.status["percent"] = percent


class IndexQueue:
    """One library's folders: those waiting, the one being indexed, and what became of
    each.

    The waiting list and the worker are only changed under the lock. Start, cancel and
    the worker each rewrote the list without one, so a job the worker had just taken
    could be written back by a start and indexed twice; and a worker that had found the
    list empty still looked alive to a start in the moment before it cleared itself,
    leaving that start's folder with nothing to index it.
    """

    def __init__(self, library=None):
        #: The library's name: what its runs' tags and the Activity page name it by.
        self.library = library
        self._lock = threading.RLock()
        #: {"folder", "cluster", "index"}, in the order they were asked for.
        self._pending = []
        self._runner = None
        #: Folder key -> where that folder has got.
        self._statuses = {}
        #: The runs of the indexer that ended, newest last: {"run", "folders", "name",
        #: "started", "finished", "outcome", "message"}.
        self._history = []
        #: The run under way: {"run", "started", "folders", "name", "status"}, or None.
        self._current = None
        #: The folders of the run under way, and what it reports through (_Progress).
        self._current_folders = []
        self._progress = None

    def start(self, folders, index, cluster=False, together=False):
        """Queue folders to be indexed by `index(folder, cluster, report)`, which returns
        a Result and calls `report(message, percent)` as it goes, and start the worker
        unless it is running. `together`: the folders queued are one job, and `index` is
        handed their list -- one run of the indexer for them all (sync's new files).

        A folder asked for twice, already waiting, or being indexed now is queued once;
        one that is not a folder is reported, not queued. Refused when none is a folder.

        details: `queued`, `already_queued` and `invalid`, the folders; `pending`, how
        many wait now.
        """
        result = Result(attempted=len(folders))
        valid, invalid, seen = [], [], set()
        for folder in folders:
            if not folder or not isinstance(folder, str) or not os.path.isdir(folder):
                invalid.append(str(folder))
                continue
            key = paths.key(folder)
            if key not in seen:
                seen.add(key)
                # The stored spelling: what the indexer is handed, and what the page is
                # shown for a waiting and a running folder alike.
                valid.append((paths.stored(folder), key))

        queued, already = [], []
        # The runs this start is part of -- a sync queueing its new files' folders: the run
        # of the indexer that indexes them carries their tags before its own.
        parents = runs.held()
        with self._lock:
            if valid:
                waiting = {paths.key(each) for job in self._pending for each in _folders_of(job)}
                for folder, key in valid:
                    if key in waiting or self._statuses.get(key, {}).get("status") == "running":
                        already.append(folder)
                        result.skip(folder, "already waiting or being indexed")
                        continue
                    if not together:
                        self._pending.append({"folder": folder, "cluster": cluster, "index": index,
                                              "parents": parents})
                    waiting.add(key)
                    self._statuses[key] = {"status": "queued", "percent": 0,
                                           "message": "Waiting to be indexed...", "folder": folder}
                    queued.append(folder)
                if together and queued:
                    self._pending.append({"folder": queued[0], "folders": list(queued), "cluster": cluster,
                                          "index": index, "parents": parents})
                self._ensure_runner()
            pending = len(self._pending)

        if not valid:
            result.refuse("No valid folder path" + (": %s" % ", ".join(invalid[:3]) if invalid else ""))
        result.changed = len(queued)
        result.details.update(queued=queued, already_queued=already, invalid=invalid, pending=pending)
        return result

    def cancel(self, folders=(), everything=False):
        """Drop folders that have not started: those named, or `everything` waiting.

        The folder being indexed is left alone. It owns a subprocess partway through
        writing rows, and stopping that is a different and riskier thing than
        forgetting a folder that has not begun. A dropped folder reports "cancelled":
        with its status gone, asking about it answered "completed".

        A run still waiting for its turn on the graphics card has not begun either: named,
        or with `everything`, it is told to stop (tagpup.services.indexing.STOP_FILE),
        which it does within half a second, writing nothing, and its folders report
        "cancelled" then. Its indexer decides, so a run that took the card that same moment
        carries on (docs/findings.md, #750).

        details: `cancelled`, the folders dropped; `stopping`, the folders of the run told
        to stop; `pending`, how many still wait.
        """
        targets = {paths.key(folder) for folder in folders if folder}
        cancelled = []
        stopping = self._stop_waiting_run(targets, everything)
        with self._lock:
            kept = []
            for job in self._pending:
                keys = [paths.key(each) for each in _folders_of(job)]
                if everything or targets.intersection(keys):
                    # A batch is one run of the indexer: naming one of its folders drops it.
                    for each, key in zip(_folders_of(job), keys):
                        cancelled.append(each)
                        self._statuses[key] = {"status": "cancelled", "percent": 0,
                                               "message": "Cancelled.", "folder": each}
                else:
                    kept.append(job)
            self._pending = kept
            pending = len(kept)
        result = Result(attempted=len(cancelled) if everything else len(targets), changed=len(cancelled))
        result.details.update(cancelled=cancelled, stopping=stopping, pending=pending)
        return result

    def _stop_waiting_run(self, targets, everything):
        """Tell the run under way to stop if it is named (or `everything`) and still waits
        for the graphics card: the folders told, or []."""
        with self._lock:
            progress, folders = self._progress, list(self._current_folders)
        if progress is None or not folders:
            return []
        if not everything and not targets.intersection(paths.key(each) for each in folders):
            return []
        if not gpu_turns.is_waiting(progress.status.get("message")) or not progress.stop_file:
            return []
        try:
            with open(progress.stop_file, "w", encoding="utf-8") as handle:
                handle.write("stop")
        except OSError as e:
            logger.warning("Could not tell the indexer to stop waiting: %s", e)
            return []
        progress.status["message"] = "Cancelling: the run was waiting for the graphics card..."
        return folders

    def indexing(self, folder):
        """The job that indexes `folder` -- it, or a folder above it -- running or waiting:
        {"status": "running" or "queued", "message", "folder"}; None when none does. What a
        Suggest of the folder waits for (tagpup.jobs.suggestions): the index writes the
        vectors and faces it would otherwise make itself."""
        key = paths.key(folder)

        def covers(each):
            return paths.key(each) == key or paths.is_under(key, each)
        with self._lock:
            if any(covers(each) for each in self._current_folders) and self._progress is not None:
                status = self._progress.status
                return {"status": "running", "message": status.get("message", ""), "folder": status.get("folder")}
            for job in self._pending:
                if any(covers(each) for each in _folders_of(job)):
                    return {"status": "queued", "message": "Waiting to be indexed...", "folder": job["folder"]}
        return None

    def status(self, folder):
        """Where a folder has got -- queued, running, completed, failed or cancelled --
        or READY, for one nobody has asked about."""
        return dict(self._statuses.get(paths.key(folder), READY))

    def busy(self):
        """Is a folder being indexed, or waiting to be?"""
        with self._lock:
            return bool(self._pending) or (self._runner is not None and self._runner.is_alive())

    def pending(self):
        """The folders waiting, in order, as {"folder", "cluster"}."""
        with self._lock:
            return [{"folder": job["folder"], "cluster": job["cluster"]} for job in self._pending]

    def active(self):
        """What is being indexed now, and what waits behind it.

        A page that has just loaded knows of no folder to ask about, so without this it
        could not tell that a folder is being indexed, nor how many are left.
        """
        running = []
        # Both under the lock the worker takes a job under: read apart, a job taken between
        # the two reads was neither running nor waiting, and the queue said it was idle.
        with self._lock:
            statuses = list(self._statuses.items())
            pending = self.pending()
        for key, status in statuses:
            if status.get("status") == "running":
                folder = status.get("folder") or key
                running.append({"folder": folder, "name": os.path.basename(folder),
                                "percent": status.get("percent", 0),
                                "message": status.get("message", "")})
        waiting = [{"folder": job["folder"], "name": os.path.basename(job["folder"])}
                   for job in pending]
        return {"active": running, "queued": waiting, "busy": bool(running or waiting),
                "remaining": len(running) + len(waiting)}

    def now(self):
        """What the Activity page shows of this queue: the run of the indexer under way --
        {"run", "parents", "started", "folders", "name", "percent", "message"}, or None -- and the jobs
        waiting, each {"name", "folders"} (a batch of sync's is one job, one run)."""
        with self._lock:
            current = dict(self._current) if self._current else None
            waiting = [{"name": os.path.basename(job["folder"]), "folders": len(_folders_of(job))}
                       for job in self._pending]
        if current is not None:
            status = current.pop("status")
            current.update(percent=status.get("percent", 0), message=status.get("message", ""))
        return {"running": current, "queued": waiting}

    def history(self):
        """The runs of the indexer that ended, newest first (at most HISTORY)."""
        with self._lock:
            return [dict(entry) for entry in reversed(self._history)]

    def wait(self):
        """Return once no worker is indexing: the waiting folders are done. For a program
        that queued folders and must not end before they are indexed (the CLI's `sync`)."""
        while True:
            with self._lock:
                runner = self._runner
            if runner is None or runner is threading.current_thread():
                return
            runner.join()
            with self._lock:
                if self._runner is runner:
                    # Joined, but not yet cleared by it: it has finished all the same.
                    return

    def run_pending(self):
        """Index the waiting folders one at a time until none is left: the worker.

        A folder that fails is recorded against itself and the rest still run. One
        unreadable folder should not cost the other nine.
        """
        try:
            while True:
                with self._lock:
                    if not self._pending:
                        # Stop and say so in one step, so a start arriving now finds no
                        # worker and starts one.
                        if self._runner is threading.current_thread():
                            self._runner = None
                        return
                    job = self._pending.pop(0)
                    status = {"status": "running", "percent": 0,
                              "message": "Starting indexing...", "folder": job["folder"]}
                    self._current = {"run": runs.index_tag(self.library),
                                     "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "folders": len(_folders_of(job)), "name": os.path.basename(job["folder"]),
                                     "parents": list(job.get("parents") or ()), "status": status}
                    run = self._current["run"]
                    progress = self._progress = _Progress(status)
                    self._current_folders = list(_folders_of(job))
                    # A batch's folders share one status: they are one run.
                    for each in _folders_of(job):
                        self._statuses[paths.key(each)] = status
                # Every line the run logs here carries its tag, and the indexer it starts is
                # told it (tagpup.services.indexing).
                try:
                    with runs.running(*(job.get("parents") or ()), run):
                        self._index(job, progress)
                finally:
                    with self._lock:
                        self._progress, self._current_folders = None, []
                self._remember(job, status)
        finally:
            # Only this worker's own entry: a newer one may have started already.
            with self._lock:
                if self._runner is threading.current_thread():
                    self._runner = None

    def _remember(self, job, status):
        """Keep what became of a run of the indexer, for the Activity page."""
        with self._lock:
            current, self._current = self._current or {}, None
            entry = {"run": current.get("run"), "parents": current.get("parents", []),
                     "folders": len(_folders_of(job)),
                     "name": os.path.basename(job["folder"]), "started": current.get("started"),
                     "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "outcome": status.get("status"),
                     "message": status.get("message", "")}
            self._history.append(entry)
            del self._history[:-HISTORY]

    def _ensure_runner(self):
        """Start the worker, unless one is already working through the queue."""
        with self._lock:
            if self._runner is not None and self._runner.is_alive():
                return
            self._runner = threading.Thread(target=self.run_pending, name="IndexQueueRunner",
                                            daemon=True)
            self._runner.start()

    @staticmethod
    def _index(job, report):
        status = report.status
        try:
            result = job["index"](job["folders"] if "folders" in job else job["folder"], job["cluster"], report)
        except Exception as e:
            logger.exception("Indexing %s failed", job["folder"])
            status.update(status="failed", percent=0, message="Error: %s" % e)
            return
        if result.details.get("cancelled"):
            status.update(status="cancelled", percent=0, message=result.details.get("message", "Cancelled."))
        elif result.ok:
            status.update(status="completed", percent=result.details.get("percent", 100),
                          message=result.details.get("message", "Folder indexed."))
        else:
            status.update(status="failed", percent=result.details.get("percent", 0),
                          message=result.message())

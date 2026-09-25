"""Folders waiting to be added to a library, and the one worker adding them.

Indexing is GPU-bound, so a library's folders are indexed one at a time: two at once do
not go twice as fast so much as make each other crawl. Asking for ten should still be
one action, not a wait beside the machine between each.

TagTuner had this queue. TagPup started a thread per folder, so two folders asked for
together were indexed at once. A process has one queue per library, which lasts as long
as the process: nothing about it is saved yet (docs/ARCHITECTURE.md, the `jobs` table).
"""
import logging
import os
import threading

from tagpup.core import paths
from tagpup.core.result import Result

logger = logging.getLogger(__name__)

#: What a folder nobody has asked about reports.
READY = {"status": "completed", "percent": 100, "message": "Ready"}

_queues = {}
_queues_lock = threading.Lock()


def queue_for(library):
    """This process's queue for a library, made the first time it is asked for."""
    with _queues_lock:
        return _queues.setdefault(library.key, IndexQueue())


def forget(library):
    """Drop a library's queue and what became of its folders. A worker still indexing
    one finishes it, into a queue nothing reads."""
    with _queues_lock:
        _queues.pop(library.key, None)


class IndexQueue:
    """One library's folders: those waiting, the one being indexed, and what became of
    each.

    The waiting list and the worker are only changed under the lock. Start, cancel and
    the worker each rewrote the list without one, so a job the worker had just taken
    could be written back by a start and indexed twice; and a worker that had found the
    list empty still looked alive to a start in the moment before it cleared itself,
    leaving that start's folder with nothing to index it.
    """

    def __init__(self):
        self._lock = threading.RLock()
        #: {"folder", "cluster", "index"}, in the order they were asked for.
        self._pending = []
        self._runner = None
        #: Folder key -> where that folder has got.
        self._statuses = {}

    def start(self, folders, index, cluster=False):
        """Queue folders to be indexed by `index(folder, cluster, report)`, which returns
        a Result and calls `report(message, percent)` as it goes, and start the worker
        unless it is running.

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
        with self._lock:
            if valid:
                waiting = {paths.key(job["folder"]) for job in self._pending}
                for folder, key in valid:
                    if key in waiting or self._statuses.get(key, {}).get("status") == "running":
                        already.append(folder)
                        result.skip(folder, "already waiting or being indexed")
                        continue
                    self._pending.append({"folder": folder, "cluster": cluster, "index": index})
                    waiting.add(key)
                    self._statuses[key] = {"status": "queued", "percent": 0,
                                           "message": "Waiting to be indexed...", "folder": folder}
                    queued.append(folder)
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

        details: `cancelled`, the folders dropped; `pending`, how many still wait.
        """
        targets = {paths.key(folder) for folder in folders if folder}
        cancelled = []
        with self._lock:
            kept = []
            for job in self._pending:
                key = paths.key(job["folder"])
                if everything or key in targets:
                    cancelled.append(job["folder"])
                    self._statuses[key] = {"status": "cancelled", "percent": 0,
                                           "message": "Cancelled.", "folder": job["folder"]}
                else:
                    kept.append(job)
            self._pending = kept
            pending = len(kept)
        result = Result(attempted=len(cancelled) if everything else len(targets), changed=len(cancelled))
        result.details.update(cancelled=cancelled, pending=pending)
        return result

    def status(self, folder):
        """Where a folder has got -- queued, running, completed, failed or cancelled --
        or READY, for one nobody has asked about."""
        return dict(self._statuses.get(paths.key(folder), READY))

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
        for key, status in list(self._statuses.items()):
            if status.get("status") == "running":
                folder = status.get("folder") or key
                running.append({"folder": folder, "name": os.path.basename(folder),
                                "percent": status.get("percent", 0),
                                "message": status.get("message", "")})
        waiting = [{"folder": job["folder"], "name": os.path.basename(job["folder"])}
                   for job in self.pending()]
        return {"active": running, "queued": waiting, "busy": bool(running or waiting),
                "remaining": len(running) + len(waiting)}

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
                    self._statuses[paths.key(job["folder"])] = status
                self._index(job, status)
        finally:
            # Only this worker's own entry: a newer one may have started already.
            with self._lock:
                if self._runner is threading.current_thread():
                    self._runner = None

    def _ensure_runner(self):
        """Start the worker, unless one is already working through the queue."""
        with self._lock:
            if self._runner is not None and self._runner.is_alive():
                return
            self._runner = threading.Thread(target=self.run_pending, name="IndexQueueRunner",
                                            daemon=True)
            self._runner.start()

    @staticmethod
    def _index(job, status):
        def report(message=None, percent=None):
            if message is not None:
                status["message"] = message
            if percent is not None:
                status["percent"] = percent

        try:
            result = job["index"](job["folder"], job["cluster"], report)
        except Exception as e:
            logger.exception("Indexing %s failed", job["folder"])
            status.update(status="failed", percent=0, message="Error: %s" % e)
            return
        if result.ok:
            status.update(status="completed", percent=result.details.get("percent", 100),
                          message=result.details.get("message", "Folder indexed."))
        else:
            status.update(status="failed", percent=result.details.get("percent", 0),
                          message=result.message())

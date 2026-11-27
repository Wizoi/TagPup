"""Folders waiting to be added to a library, and the one worker adding them.

Indexing is GPU-bound, so a library's folders are indexed one at a time: two at once do
not go twice as fast so much as make each other crawl. Asking for ten should still be
one action, not a wait beside the machine between each.

TagTuner had this queue. TagPup started a thread per folder, so two folders asked for
together were indexed at once. A runtime -- one a process -- has one queue per library file
(tagpup.runtime.Runtime.index_queues), which lasts as long as it: what waits is not saved
(docs/ARCHITECTURE.md, the `jobs` table).

Each run of the indexer has a tag (tagpup.core.runs.index_tag), held while it runs, so
its lines in this process's log and in the indexer's own (the CLI it starts writes
indexer-<library>-<run>.log) can be shown together. What became of a run is its row (below), never kept here: the
queue holds what waits and the run under way, as long as the process lives.

Each run of the indexer is a run of the job `index` (job_runs.INDEX) in the library's `job_runs` too (DECISIONS 2026-10-28, ACTIVITY REWORK),
a recorded run (tagpup.services.job_runs.run, whose lifecycle it is) claimed by this process as it takes the job -- under the run that queued it (a sync's), from the tags it was queued under
-- told how far it got from the indexer's lines (tagpup.services.job_runs.tell, through index_folder), and ended with its
counts and its message -- tried for job_runs.RECORD_WITHIN, as its claim is, so the next index never waits a writer out,
and one the library was being written for then written once it is free, on a thread of its own; every end that could
not be written is tried again as the queue's next index of the library is claimed (job_runs.run), and stays to be tried
at each after (so a run of this live server stays `running` only while the library cannot be
written); its row holds, from the claim on, the run's tag (`changed["run"]`), which the indexer's own log is named by, how
many folders it indexes (`folders`) and the tags of the syncs it is part of (`syncs`, tagpup.core.runs.syncs: a sync's
own record is written as it ends, so no parent names it), which the Activity page finds the run's lines by. Only
the claiming process writes a run's row: the indexer the queue starts claims none. A row that
cannot be written -- the library behind this version's schema, being written, gone -- is logged, and the index runs as
it would have.
"""
import logging
import os
import threading
import time

from tagpup.core import gpu_turns, paths, runs
from tagpup.core.result import Result
from tagpup.services import bake_after_index, job_runs
from tagpup.services import indexing as indexing_service
from tagpup.services import photos as photo_actions

logger = logging.getLogger(__name__)

#: What a folder nobody has asked about reports.
READY = {"status": "completed", "percent": 100, "message": "Ready"}

#: What a folder queued once more after a run that began before its files arrived says.
AGAIN = "Indexing again: files arrived in it while it was being indexed..."

#: What the run of an index whose thread stopped before it said how it ended (a BaseException) says.
STOPPED = "The index stopped before it ended; the server's log says why."

def _folders_of(job):
    """The folders a job indexes: one, or a batch's (IndexQueue.start's `together`)."""
    return job.get("folders") or [job["folder"]]


class _Progress:
    """What a run of the indexer reports through: `report(message, percent)` into its
    status, with when it last reported (`progressed`: a run that stopped moving is told from a long one), and
    `stop_file`, set by tagpup.services.indexing while its indexer runs: the
    file that stops it while it waits for the graphics card."""

    def __init__(self, status):
        self.status = status
        self.stop_file = None

    def __call__(self, message=None, percent=None):
        if message is not None:
            self.status["message"] = message
        if percent is not None:
            self.status["percent"] = percent
        if message is not None or percent is not None:
            self.status["progressed"] = job_runs.stamp(time.time())


class IndexQueue:
    """One library's folders: those waiting, the one being indexed, and what became of
    each.

    The waiting list and the worker are only changed under the lock. Start, cancel and
    the worker each rewrote the list without one, so a job the worker had just taken
    could be written back by a start and indexed twice; and a worker that had found the
    list empty still looked alive to a start in the moment before it cleared itself,
    leaving that start's folder with nothing to index it.
    """

    def __init__(self, library=None, recorded_in=None):
        #: The library's name: what its runs' tags and the Activity page name it by.
        self.library = library
        #: The Library its runs of the indexer are recorded in (job_runs.INDEX); None for a queue made without one -- the tests of
        #: its scheduling alone -- which records none.
        self._recorded_in = recorded_in
        self._lock = threading.RLock()
        #: {"folder", "index"}, in the order they were asked for.
        self._pending = []
        self._runner = None
        #: Folder key -> where that folder has got.
        self._statuses = {}
        #: The run under way: {"run", "started", "folders", "name", "status"}, or None.
        self._current = None
        #: The folders of the run under way, and what it reports through (_Progress).
        self._current_folders = []
        self._progress = None
        #: Folder key -> the start that was skipped because that folder was being indexed:
        #: {"folder", "index", "together", "parents"}. Files that arrived after the run
        #: looked at the folder are not in it; the run's end queues the folder once more,
        #: unless a cancel dropped it meanwhile.
        self._missed = {}

    def start(self, folders, index, together=False):
        """Queue folders to be indexed by `index(folder, report)`, which returns
        a Result and calls `report(message, percent)` as it goes, and start the worker
        unless it is running. `together`: the folders queued are one job, and `index` is
        handed their list -- one run of the indexer for them all (sync's new files).

        A folder asked for twice, or already waiting, is queued once. One being indexed now
        is not queued, but remembered: files that arrived in it after the run began are
        not in that run, so when it ends completed the folder is queued once more (a
        watcher's sync queues the folder of what arrived during an index, #1565). One that
        is not a folder is reported, not queued. Refused when none is a folder.

        details: `queued`, `already_queued` and `invalid`, the folders; `pending`, how
        many wait now.
        """
        # The runs this start is part of -- a sync queueing its new files' folders: the run
        # of the indexer that indexes them carries their tags before its own.
        return self._enqueue(folders, index, together, runs.held())

    def _enqueue(self, folders, index, together, parents, message=None, looked=False):
        """start's work, for `parents` (the runs the job is part of); `message`, what the
        job's status says while it waits and as it starts, when it is not the usual. `looked`: the
        folders were read by the run that just ended, so they are not asked of the disk again
        (the caller holds the lock, and a share that is waking would hold it with the call)."""
        result = Result(attempted=len(folders))
        valid, invalid, away, seen = [], [], [], set()
        for folder in folders:
            state = None if not folder or not isinstance(folder, str) else (
                "there" if looked else photo_actions.folder_state(folder))
            if state != "there":
                invalid.append(str(folder))
                if state == "unanswered":
                    away.append(folder)
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
                waiting = {paths.key(each) for job in self._pending for each in _folders_of(job)}
                for folder, key in valid:
                    if key in waiting or self._statuses.get(key, {}).get("status") == "running":
                        if key not in waiting:
                            self._missed[key] = {"folder": folder, "index": index, "together": together,
                                                 "parents": parents}
                        already.append(folder)
                        result.skip(folder, "already waiting or being indexed")
                        continue
                    if not together:
                        self._pending.append({"folder": folder, "index": index, "parents": parents,
                                              "message": message})
                    waiting.add(key)
                    self._statuses[key] = {"status": "queued", "percent": 0,
                                           "message": message or "Waiting to be indexed...", "folder": folder}
                    queued.append(folder)
                if together and queued:
                    self._pending.append({"folder": queued[0], "folders": list(queued), "index": index,
                                          "parents": parents, "message": message})
                self._ensure_runner()
            pending = len(self._pending)

        if not valid and away:
            # None to queue, and a share did not answer: the share's sentence, a 503 (#2129), not "no valid folder".
            refusal = photo_actions.folder_refusal(away[0], "unanswered")
            result.refuse(refusal.refused, refusal.refused_kind)
        elif not valid:
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

        A folder being indexed that was asked for again (start) is to run once more when
        its run ends; that run has not started either, and is dropped.

        details: `cancelled`, the folders dropped; `rerun_dropped`, the folders being
        indexed whose run once more was dropped; `stopping`, the folders of the run told
        to stop; `pending`, how many still wait.
        """
        targets = {paths.key(folder) for folder in folders if folder}
        cancelled, rerun_dropped = [], []
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
            for key in list(self._missed) if everything else targets.intersection(self._missed):
                rerun_dropped.append(self._missed.pop(key)["folder"])
        dropped = len(cancelled) + len(rerun_dropped)
        result = Result(attempted=dropped if everything else len(targets), changed=dropped)
        result.details.update(cancelled=cancelled, rerun_dropped=rerun_dropped, stopping=stopping, pending=pending)
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
        vectors and faces it would otherwise make itself. A run that has begun to bake has recorded
        its folders already (the bake follows the last batch), so it is not waited for (#2355)."""
        return self._job_of(folder, overlapping=False)

    def overlaps(self, folder):
        """The job whose folders overlap `folder` -- it, a folder above it, or a folder below it, any folder of a batch
        (`together`) -- running, its bake included, or waiting: {"status": "running" or "queued", "message", "folder"},
        `folder` the job's folder that overlaps; None when none does. What a removal of the folder is refused for
        (tagpup.web.folder_routes): the index would be refused at its next batch (store.photos.record_indexed raises
        NotHeld; the batch makes no rows) and stop, failed, part-way, and its bake would write the files of photos whose
        rows are gone. A folder asked for again while it is indexed waits too (`_missed`). It sees only this queue: a
        run of another process's is its row's (tagpup.services.libraries.removal_refusal). active() lists
        folders, not who covers them, so it does not answer this."""
        return self._job_of(folder, overlapping=True)

    def _job_of(self, folder, overlapping):
        """indexing's answer, or with `overlapping` overlaps'."""
        key = paths.key(folder)

        def covers(each):
            return (paths.key(each) == key or paths.is_under(key, each)
                    or (overlapping and paths.is_under(each, key)))
        with self._lock:
            hit = next((each for each in self._current_folders if covers(each)), None)
            if hit is not None and self._progress is not None:
                status = self._progress.status
                if not overlapping and status.get("message", "").startswith(
                        (bake_after_index.FOLDER_SAID, bake_after_index.SAID)):
                    return None
                return {"status": "running", "message": status.get("message", ""),
                        "folder": hit if overlapping else status.get("folder")}
            for job in self._pending:
                hit = next((each for each in _folders_of(job) if covers(each)), None)
                if hit is not None:
                    return {"status": "queued", "message": "Waiting to be indexed...",
                            "folder": hit if overlapping else job["folder"]}
            # Asked for again while it was indexed: queued as that run ends (_queue_what_was_missed, in one hold of
            # this lock), so it waits from now on -- a removal between the run's end and that queueing was let by (#3228).
            hit = next((ask["folder"] for ask in self._missed.values() if covers(ask["folder"])),
                       None) if overlapping else None
            if hit is not None:
                return {"status": "queued", "message": "Waiting to be indexed...", "folder": hit}
        return None

    def status(self, folder):
        """Where a folder has got -- queued, running, completed, failed or cancelled --
        or READY, for one nobody has asked about."""
        return dict(self._statuses.get(paths.key(folder), READY))

    def busy(self):
        """Is a folder being indexed, or waiting to be?"""
        with self._lock:
            return bool(self._pending) or (self._runner is not None and self._runner.is_alive())

    def active(self):
        """What is being indexed now, and what waits behind it, each folder by its own path and name: a batch
        (`together`, one run of the indexer) names every folder it indexes, each with the run's percent and message, and
        every folder it will -- so a page marks each (the Folders tab: services/library_view.py _folders) and counts each
        ("Indexing N of M folders": web/tagpup/suggestions.js); `remaining` counts folders, not runs (now() counts runs).

        A page that has just loaded knows of no folder to ask about, so without this it
        could not tell that a folder is being indexed, nor how many are left.
        """
        # Both under the lock the worker takes a job under: read apart, a job taken between
        # the two reads was neither running nor waiting, and the queue said it was idle.
        with self._lock:
            current = [(each, self._statuses[paths.key(each)]) for each in self._current_folders]
            waiting = [each for job in self._pending for each in _folders_of(job)]
            # Asked for again while it was indexed: queued from that moment, not only once the run's row is written
            # (_queue_what_was_missed), or a page following the queue saw it idle while the rerun was about to start.
            waiting += [ask["folder"] for ask in self._missed.values()]
        # A run whose index has said how it ended is not running, though its folders are the run's until its row is.
        running = [{"folder": each, "name": os.path.basename(each), "percent": status.get("percent", 0),
                    "message": status.get("message", "")}
                   for each, status in current if status.get("status") == "running"]
        queued = [{"folder": each, "name": os.path.basename(each)} for each in waiting]
        return {"active": running, "queued": queued, "busy": bool(running or queued),
                "remaining": len(running) + len(queued)}

    def now(self):
        """What the Activity page shows of this queue: the run of the indexer under way --
        {"run", "parents", "started", "folders", "name", "percent", "message", "progressed"}, or None -- and the jobs
        waiting, each {"name", "folders"} (a batch of sync's is one job, one run)."""
        with self._lock:
            current = dict(self._current) if self._current else None
            waiting = [{"name": os.path.basename(job["folder"]), "folders": len(_folders_of(job))}
                       for job in self._pending]
        if current is not None:
            status = current.pop("status")
            current.update(percent=status.get("percent", 0), message=status.get("message", ""),
                           progressed=status.get("progressed"))
        return {"running": current, "queued": waiting}

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
                              "message": job.get("message") or "Starting indexing...", "folder": job["folder"]}
                    self._current = {"run": runs.index_tag(self.library),
                                     "started": job_runs.stamp(time.time()),
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
                # What its row holds from the claim to its end, beside its counts.
                held = {"run": run, "folders": len(_folders_of(job))}
                syncs = runs.syncs(self.library, job.get("parents") or ())
                if syncs:
                    held["syncs"] = syncs
                try:
                    with runs.running(*(job.get("parents") or ()), run):
                        # Claimed here, under the tags it was queued under: its parent is the run that queued it. Not
                        # recorded (the library behind, written by another for longer than the claim tries: RECORD_WITHIN),
                        # it indexes all the same; another process's index under way is no reason (RECORDS_ONLY). Done
                        # when it completed or was cancelled (counted `cancelled`), its status's message the note; failed
                        # as job_runs.failure says, or STOPPED when its thread stopped (a BaseException).
                        with job_runs.run(self._recorded_in, job_runs.INDEX, self.library, changed=held,
                                          stopped=STOPPED) as recorded:
                            result, error = self._index(job, progress)
                            recorded.ends(result, error,
                                          counts={"cancelled": 1} if status["status"] == "cancelled" else None,
                                          note=status.get("message") or None)
                finally:
                    with self._lock:
                        if status["status"] == "running":
                            # Its thread stopped before the index said how it ended: its folders say so, not running on.
                            status.update(status="failed", percent=0, message=STOPPED)
                        self._progress, self._current_folders = None, []
                    # Gone from Now only once its row has ended: the page lists the row instead while it runs on.
                    with self._lock:
                        self._current = None
                self._queue_what_was_missed(job, status)
        finally:
            # Only this worker's own entry: a newer one may have started already.
            with self._lock:
                if self._runner is threading.current_thread():
                    self._runner = None

    def _queue_what_was_missed(self, job, status):
        """After a run: queue once more the folders that were asked for while it ran, if
        it completed. A run that was cancelled or failed is not repeated: nothing says the
        next would do better. The re-run is skipped from only when something asks again
        during it, so the chain ends when the arrivals do.

        Taken and queued in one hold of the (re-entrant) lock, so a cancel cannot fall
        between them and miss the folder. The re-run is part of the runs that asked for it."""
        with self._lock:
            missed = [self._missed.pop(paths.key(each), None) for each in _folders_of(job)]
            missed = [each for each in missed if each]
            if status.get("status") != "completed":
                return
            batch = [each for each in missed if each["together"]]
            for ask in missed:
                if not ask["together"]:
                    self._enqueue([ask["folder"]], ask["index"], False, ask["parents"], AGAIN, looked=True)
            if batch:
                parents = tuple(dict.fromkeys(tag for each in batch for tag in each["parents"]))
                self._enqueue([each["folder"] for each in batch], batch[0]["index"], True, parents, AGAIN, looked=True)

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
        """Run the job's index, its status said in `report.status`: (its Result, None), or (None, what it raised), the
        status then saying why as its run does (job_runs.failure)."""
        status = report.status
        try:
            result = job["index"](job["folders"] if "folders" in job else job["folder"], report)
        except Exception as e:
            logger.exception("Indexing %s failed", job["folder"])
            status.update(status="failed", percent=0, message=job_runs.failure(error=e).sentence)
            return None, e
        if result.details.get("cancelled"):
            status.update(status="cancelled", percent=0, message=result.details.get("message", "Cancelled."))
        elif result.ok:
            status.update(status="completed", percent=result.details.get("percent", 100),
                          message=result.details.get("message", "Folder indexed."))
            if result.details.get(indexing_service.REEXAMINE):
                # The folder its closing sentence offers Re-examine of: the pages' action (index_folder).
                status[indexing_service.REEXAMINE] = result.details[indexing_service.REEXAMINE]
        else:
            status.update(status="failed", percent=result.details.get("percent", 0),
                          message=result.message())
        return result, None

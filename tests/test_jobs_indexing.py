"""tagpup.jobs.indexing: the folders waiting to be added to a library, and the worker.

The queue is driven directly here, with indexing stood in for. What indexing does is
test_service_indexing.py's; what the routes accept and answer is in
test_tuner_server_api.py.
"""
import inspect
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths, runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing  # noqa: E402
from tagpup.jobs.indexing import IndexQueue  # noqa: E402


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.queue = IndexQueue()
        self.root = tempfile.mkdtemp(prefix="index_queue_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def folders(self, n):
        made = []
        for i in range(n):
            folder = os.path.join(self.root, "shoot %d" % i)
            os.makedirs(folder)
            made.append(folder)
        return made

    def wait_until_idle(self, timeout=10.0):
        deadline = time.time() + timeout
        while self.queue.active()["busy"] or self.queue._runner is not None:
            if time.time() > deadline:
                self.fail("still busy after %ss: %s" % (timeout, self.queue.active()))
            time.sleep(0.01)

    def statuses(self, folders):
        return [self.queue.status(folder)["status"] for folder in folders]


class TheWorker(QueueTest):
    def test_folders_are_indexed_one_at_a_time_in_the_order_given(self):
        folders = self.folders(3)
        seen, running, most = [], [0], [0]
        lock = threading.Lock()

        def index(folder, report):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.02)
            seen.append(folder)
            with lock:
                running[0] -= 1
            return Result(changed=1)

        self.queue.start(folders, index)
        self.wait_until_idle()
        self.assertEqual(seen, folders)
        self.assertEqual(most[0], 1, "two folders were indexed at once")
        self.assertEqual(self.statuses(folders), ["completed"] * 3)

    def test_one_folder_that_fails_does_not_cost_the_others(self):
        folders = self.folders(3)

        def index(folder, report):
            if folder == folders[1]:
                raise RuntimeError("that folder is unreadable")
            return Result(changed=1)

        self.queue.start(folders, index)
        self.wait_until_idle()
        self.assertEqual(self.statuses(folders), ["completed", "failed", "completed"])
        self.assertEqual(self.queue.status(folders[1])["message"], "Error: that folder is unreadable")

    def test_a_failed_result_is_recorded_with_what_it_said(self):
        folder = self.folders(1)[0]

        def index(folder, report):
            result = Result(attempted=1)
            result.fail(folder, "Indexing failed with exit code 3.")
            result.details["percent"] = 0
            return result

        self.queue.start([folder], index)
        self.wait_until_idle()
        self.assertEqual(self.queue.status(folder), {
            "status": "failed", "percent": 0, "message": "Indexing failed with exit code 3.",
            "folder": folder})

    def test_a_folder_indexed_says_what_indexing_said(self):
        folder = self.folders(1)[0]
        self.queue.start([folder], lambda f, report: Result(
            changed=1, details={"percent": 100, "message": "Folder indexed."}))
        self.wait_until_idle()
        self.assertEqual(self.queue.status(folder)["message"], "Folder indexed.")

    def test_progress_is_seen_while_the_folder_is_indexed(self):
        folder = self.folders(1)[0]
        reported, release = threading.Event(), threading.Event()

        def index(folder, report):
            report("Generating embeddings: 42% (21/50)", 37)
            reported.set()
            release.wait(10)
            return Result(changed=1)

        self.queue.start([folder], index)
        self.assertTrue(reported.wait(10))
        try:
            status = self.queue.status(folder)
            self.assertEqual((status["status"], status["percent"], status["message"]),
                             ("running", 37, "Generating embeddings: 42% (21/50)"))
            active = self.queue.active()
            self.assertEqual([a["folder"] for a in active["active"]], [folder])
            self.assertEqual(active["remaining"], 1)
        finally:
            release.set()
        self.wait_until_idle()

    def test_it_stops_when_the_queue_empties_and_a_later_start_starts_another(self):
        first, second = self.folders(2)
        self.queue.start([first], lambda f, r: Result(changed=1))
        self.wait_until_idle()
        self.assertIsNone(self.queue._runner)
        self.queue.start([second], lambda f, r: Result(changed=1))
        self.wait_until_idle()
        self.assertEqual(self.statuses([first, second]), ["completed", "completed"])

    def test_a_folder_whose_status_went_missing_is_still_recorded(self):
        folder = self.folders(1)[0]
        self.queue._ensure_runner = lambda: None
        self.queue.start([folder], lambda f, r: Result(changed=1))
        self.queue._statuses.clear()
        self.queue.run_pending()
        self.assertEqual(self.queue.status(folder)["status"], "completed")


class WhatIsQueued(QueueTest):
    def setUp(self):
        super().setUp()
        # Held back, so what waits can be looked at.
        self.queue._ensure_runner = lambda: None

    def start(self, folders, **options):
        return self.queue.start(folders, lambda f, r: Result(changed=1), **options)

    def test_each_folder_waits_in_the_order_given_spelled_as_stored(self):
        folders = self.folders(2)
        result = self.start([f.replace(os.sep, "/") for f in folders])
        self.assertEqual(result.details["queued"], [paths.stored(f) for f in folders])
        self.assertEqual([job["folder"] for job in self.queue.pending()], folders)
        self.assertEqual(self.statuses(folders), ["queued", "queued"])

    def test_a_folder_asked_for_twice_or_already_waiting_is_queued_once(self):
        folder = self.folders(1)[0]
        self.start([folder, folder])
        result = self.start([folder])
        self.assertEqual((result.details["queued"], result.details["already_queued"]), ([], [folder]))
        self.assertEqual(len(self.queue.pending()), 1)

    def test_a_folder_being_indexed_is_not_queued_behind_itself(self):
        folder = self.folders(1)[0]
        self.queue._statuses[paths.key(folder)] = {"status": "running", "folder": folder}
        result = self.start([folder])
        self.assertEqual(result.details["already_queued"], [folder])
        self.assertEqual(self.queue.pending(), [])

    def test_what_is_not_a_folder_is_reported_and_none_at_all_is_refused(self):
        folder = self.folders(1)[0]
        missing = os.path.join(self.root, "not here")
        result = self.start([folder, missing, None])
        self.assertEqual(result.details["invalid"], [missing, "None"])
        self.assertEqual(result.details["queued"], [folder])

        refused = self.start([missing])
        self.assertEqual(refused.refused, "No valid folder path: %s" % missing)
        self.assertEqual(len(self.queue.pending()), 1)

    def test_cancelling_drops_what_waits_and_says_so(self):
        folders = self.folders(3)
        self.start(folders)
        result = self.queue.cancel([folders[1]])
        self.assertEqual((result.details["cancelled"], result.details["pending"]), ([folders[1]], 2))
        self.assertEqual(self.statuses(folders), ["queued", "cancelled", "queued"], "a cancelled folder is not 'completed'")
        self.assertEqual(self.queue.cancel(everything=True).details["cancelled"],
                         [folders[0], folders[2]])
        self.assertEqual(self.queue.pending(), [])

    def test_cancelling_leaves_the_folder_being_indexed_alone(self):
        running, waiting = self.folders(2)
        self.queue._statuses[paths.key(running)] = {"status": "running", "folder": running}
        self.start([waiting])
        self.queue.cancel(everything=True)
        self.assertEqual(self.queue.status(running)["status"], "running")
        self.assertEqual([a["folder"] for a in self.queue.active()["active"]], [running])

    def test_a_folder_nobody_asked_about_is_ready(self):
        self.assertEqual(self.queue.status(self.root), indexing.READY)


class FilesArrivingDuringAnIndex(QueueTest):
    """The watcher's sync queues the folder of what arrived while that folder is being
    indexed (#1565): the run began before the files came, so it is run again."""

    def hold(self, folder, runs, release_first=True):
        """An index that blocks in its first run until released; `runs` counts its runs."""
        started, release = threading.Event(), threading.Event()

        def index(folder_, report):
            runs.append(folder_)
            if len(runs) == 1:
                started.set()
                release.wait(10)
            return Result(changed=1)
        return index, started, release

    def test_files_that_arrive_during_an_index_are_indexed_by_the_end(self):
        folder = self.folders(1)[0]
        runs = []
        index, started, release = self.hold(folder, runs)
        self.queue.start([folder], index)
        self.assertTrue(started.wait(10))
        skipped = self.queue.start([folder], index)
        self.assertEqual(skipped.details["already_queued"], [folder])
        release.set()
        self.wait_until_idle()
        self.assertEqual(runs, [folder, folder])
        self.assertEqual(self.queue.status(folder)["status"], "completed")

    def test_ten_rapid_skips_make_exactly_one_rerun(self):
        folder = self.folders(1)[0]
        runs = []
        index, started, release = self.hold(folder, runs)
        self.queue.start([folder], index)
        self.assertTrue(started.wait(10))
        for _ in range(10):
            self.queue.start([folder], index)
        release.set()
        self.wait_until_idle()
        self.assertEqual(len(runs), 2)

    def test_a_cancelled_or_failed_run_is_not_repeated(self):
        folder = self.folders(1)[0]
        for outcome in ("cancelled", "failed", "raised"):
            with self.subTest(outcome=outcome):
                self.queue = IndexQueue()
                runs, started, release = [], threading.Event(), threading.Event()

                def index(folder_, report, outcome=outcome, runs=runs, started=started, release=release):
                    runs.append(folder_)
                    started.set()
                    release.wait(10)
                    if outcome == "raised":
                        raise RuntimeError("unreadable")
                    result = Result()
                    if outcome == "cancelled":
                        result.details["cancelled"] = True
                    else:
                        result.fail(folder_, "exit code 3")
                    return result

                self.queue.start([folder], index)
                self.assertTrue(started.wait(10))
                self.queue.start([folder], index)
                release.set()
                self.wait_until_idle()
                self.assertEqual(len(runs), 1)
                self.assertNotEqual(self.queue.status(folder)["status"], "completed")

    def test_the_rerun_that_finds_nothing_ends_the_chain(self):
        folder = self.folders(1)[0]
        runs = []
        index, started, release = self.hold(folder, runs)
        self.queue.start([folder], index)
        self.assertTrue(started.wait(10))
        self.queue.start([folder], index)
        release.set()
        self.wait_until_idle()
        time.sleep(0.1)
        self.assertEqual(len(runs), 2)
        self.assertFalse(self.queue.busy())

    def test_a_folder_waiting_is_not_run_a_second_time(self):
        running, waiting = self.folders(2)
        runs = []
        index, started, release = self.hold(running, runs)
        self.queue.start([running], index)
        self.assertTrue(started.wait(10))
        self.queue.start([waiting], index)
        self.queue.start([waiting], index)
        release.set()
        self.wait_until_idle()
        self.assertEqual(sorted(runs), sorted([running, waiting]))

    def test_each_library_requeues_in_its_own_queue_with_its_own_index(self):
        root = tempfile.mkdtemp(prefix="index_queues_")
        self.addCleanup(shutil.rmtree, root, True)
        one = Library(os.path.join(root, "one.db"))
        other = Library(os.path.join(root, "other.db"))
        self.addCleanup(indexing.forget, one)
        self.addCleanup(indexing.forget, other)
        folder = self.folders(1)[0]
        ran = {"one": [], "other": []}
        started, release = threading.Event(), threading.Event()

        def index_for(name):
            def index(folder_, report):
                ran[name].append(folder_)
                if name == "one" and len(ran[name]) == 1:
                    started.set()
                    release.wait(10)
                return Result(changed=1)
            return index
        queue_one = indexing.queue_for(one)
        queue_one.start([folder], index_for("one"))
        self.assertTrue(started.wait(10))
        # The owner switches library: the same folder is asked for in the other, and again in the first.
        indexing.queue_for(other).start([folder], index_for("other"))
        queue_one.start([folder], index_for("one"))
        release.set()
        queue_one.wait()
        indexing.queue_for(other).wait()
        self.assertEqual((len(ran["one"]), len(ran["other"])), (2, 1))

    def test_a_folder_cancelled_while_it_was_asked_for_again_runs_once(self):
        # The owner presses Cancel queue (or cancels the folder by name) while the watcher's
        # sync has asked for the running folder again: cancel drops what has not started,
        # and the run that was to follow had not.
        for how in ("everything", "by name"):
            with self.subTest(how=how):
                self.queue = IndexQueue()
                folder = os.path.join(self.root, how)
                os.makedirs(folder)
                runs_ = []
                index, started, release = self.hold(folder, runs_)
                self.queue.start([folder], index)
                self.assertTrue(started.wait(10))
                self.queue.start([folder], index)
                if how == "everything":
                    cancelled = self.queue.cancel(everything=True)
                else:
                    cancelled = self.queue.cancel([folder])
                self.assertEqual(cancelled.details["rerun_dropped"], [folder])
                self.assertEqual(cancelled.details["cancelled"], [])
                self.assertEqual(cancelled.changed, 1)
                release.set()
                self.wait_until_idle()
                self.assertEqual(runs_, [folder])
                self.assertEqual(self.queue.status(folder)["status"], "completed")

    def test_the_rerun_is_part_of_the_sync_that_asked_and_says_why(self):
        # The Activity page shows the re-run under the sync that asked for it, not as an
        # index nobody asked for, and its status says why the folder is indexed again.
        folder = self.folders(1)[0]
        seen = []
        started, release = threading.Event(), threading.Event()

        def index(folder_, report):
            seen.append((runs.held(), report.status["message"]))
            if len(seen) == 1:
                started.set()
                release.wait(10)
            return Result(changed=1)
        self.queue.start([folder], index)
        self.assertTrue(started.wait(10))
        with runs.running("sync-kr-track-20261016-2200"):
            self.queue.start([folder], index)
        release.set()
        self.wait_until_idle()
        self.assertEqual(len(seen), 2)
        held, message = seen[1]
        self.assertEqual(held[0], "sync-kr-track-20261016-2200")
        self.assertIn("arrived", message)
        self.assertEqual(self.queue.history()[0]["parents"], ["sync-kr-track-20261016-2200"])


class TheQueueIsChangedAndReadUnderOneLock(QueueTest):
    """Lifted from test_index_queue_is_locked and test_index_queue_active_is_one_read (#750's branch). index-start,
    index-cancel and the worker each read the waiting list, changed it and wrote it back with no lock: a job the worker had
    just taken could be written back by a start and indexed twice, and a worker that had found the list empty still looked
    alive to a start in the moment before it cleared itself. Breaks: cancel's `with self._lock:` made `if True:` (the
    first test); active() reading the running folders apart from the waiting ones (the second)."""

    #: A write of the waiting list or the worker.
    WRITE = re.compile(r"self\._(pending|runner)\s*=[^=]|self\._pending\.(append|pop|insert|remove|clear)\("
                       r"|self\._missed\[.*\]\s*=[^=]|self\._missed\.(pop|clear)\(")

    def test_the_waiting_list_and_the_worker_are_only_written_under_the_lock(self):
        with open(inspect.getsourcefile(indexing), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        writes = [i for i, line in enumerate(lines) if self.WRITE.search(line)]
        self.assertGreaterEqual(len(writes), 7)
        unlocked = []
        for i in writes:
            indent = len(lines[i]) - len(lines[i].lstrip())
            for j in range(i - 1, -1, -1):
                text = lines[j].strip()
                own = len(lines[j]) - len(lines[j].lstrip())
                if not text or own >= indent:
                    continue
                if text.startswith("with ") and "self._lock" in text:
                    break
                if text.startswith(("def ", "class ")):
                    if not text.startswith("def __init__("):   # nothing else can see a queue while it is being made
                        unlocked.append("indexing.py:%d: %s" % (i + 1, lines[i].strip()))
                    break
                indent = own
        self.assertEqual([], unlocked, "written without the queue's lock")

    def test_a_job_taken_while_the_queue_is_asked_what_is_running_is_still_seen(self):
        folder = self.folders(1)[0]
        self.queue._ensure_runner = lambda: None      # queued with no worker: the test plays the worker's step itself
        self.queue.start([folder], lambda folder, cluster, report: None)
        took = threading.Event()

        def worker_takes_the_job():
            with self.queue._lock:
                job = self.queue._pending.pop(0)
                self.queue._statuses[paths.key(job["folder"])] = {"status": "running", "percent": 0,
                                                                  "message": "Starting indexing...", "folder": job["folder"]}
            took.set()
        asked = self.queue.pending

        def pending_after_the_worker():
            threading.Thread(target=worker_takes_the_job, daemon=True).start()
            took.wait(1.0)
            return asked()
        self.queue.pending = pending_after_the_worker
        found = self.queue.active()
        self.assertTrue(took.wait(5))
        self.assertEqual((True, 1), (found["busy"], found["remaining"]), found)


class EachLibraryHasAQueue(unittest.TestCase):
    def test_one_library_one_queue_however_it_is_spelled(self):
        root = tempfile.mkdtemp(prefix="index_queues_")
        self.addCleanup(shutil.rmtree, root, True)
        one = Library(os.path.join(root, "one.db"))
        other = Library(os.path.join(root, "other.db"))
        self.addCleanup(indexing.forget, one)
        self.addCleanup(indexing.forget, other)
        self.assertIs(indexing.queue_for(one), indexing.queue_for(Library(one.path.replace(os.sep, "/"))))
        self.assertIsNot(indexing.queue_for(one), indexing.queue_for(other))

    def test_forgetting_a_library_starts_it_afresh(self):
        library = Library(os.path.join(tempfile.gettempdir(), "forgotten.db"))
        queue = indexing.queue_for(library)
        indexing.forget(library)
        self.assertIsNot(indexing.queue_for(library), queue)
        indexing.forget(library)


if __name__ == "__main__":
    unittest.main()

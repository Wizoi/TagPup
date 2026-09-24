"""tagpup.jobs.indexing: the folders waiting to be added to a library, and the worker.

The queue is driven directly here, with indexing stood in for. What indexing does is
test_service_indexing.py's; what the routes accept and answer is in
test_tuner_server_api.py.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import paths  # noqa: E402
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

        def index(folder, cluster, report):
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

        def index(folder, cluster, report):
            if folder == folders[1]:
                raise RuntimeError("that folder is unreadable")
            return Result(changed=1)

        self.queue.start(folders, index)
        self.wait_until_idle()
        self.assertEqual(self.statuses(folders), ["completed", "failed", "completed"])
        self.assertEqual(self.queue.status(folders[1])["message"], "Error: that folder is unreadable")

    def test_a_failed_result_is_recorded_with_what_it_said(self):
        folder = self.folders(1)[0]

        def index(folder, cluster, report):
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
        self.queue.start([folder], lambda f, c, report: Result(
            changed=1, details={"percent": 100, "message": "Folder indexed."}))
        self.wait_until_idle()
        self.assertEqual(self.queue.status(folder)["message"], "Folder indexed.")

    def test_progress_is_seen_while_the_folder_is_indexed(self):
        folder = self.folders(1)[0]
        reported, release = threading.Event(), threading.Event()

        def index(folder, cluster, report):
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

    def test_it_is_told_whether_to_cluster(self):
        folder = self.folders(1)[0]
        asked = []
        self.queue.start([folder], lambda f, cluster, report: asked.append(cluster) or Result(),
                         cluster=True)
        self.wait_until_idle()
        self.assertEqual(asked, [True])

    def test_it_stops_when_the_queue_empties_and_a_later_start_starts_another(self):
        first, second = self.folders(2)
        self.queue.start([first], lambda f, c, r: Result(changed=1))
        self.wait_until_idle()
        self.assertIsNone(self.queue._runner)
        self.queue.start([second], lambda f, c, r: Result(changed=1))
        self.wait_until_idle()
        self.assertEqual(self.statuses([first, second]), ["completed", "completed"])

    def test_a_folder_whose_status_went_missing_is_still_recorded(self):
        folder = self.folders(1)[0]
        self.queue._ensure_runner = lambda: None
        self.queue.start([folder], lambda f, c, r: Result(changed=1))
        self.queue._statuses.clear()
        self.queue.run_pending()
        self.assertEqual(self.queue.status(folder)["status"], "completed")


class WhatIsQueued(QueueTest):
    def setUp(self):
        super().setUp()
        # Held back, so what waits can be looked at.
        self.queue._ensure_runner = lambda: None

    def start(self, folders, **options):
        return self.queue.start(folders, lambda f, c, r: Result(changed=1), **options)

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
        self.assertEqual(self.statuses(folders), ["queued", "cancelled", "queued"])
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

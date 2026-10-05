"""What the index queue says is running and waiting is read at one moment.

`IndexQueue.active()` read the running folders without the lock, then the waiting ones
with it. The worker takes a job off the waiting list and marks it running in one step
under the lock, so a job it took between those two reads was in neither: the queue said
it was idle while a folder was being indexed -- the page's "is anything indexing" and a
test's "wait until idle" both believed it. Found when the timing of tagpup.services.
indexing changed (docs/findings.md, #750's branch) and a test of TagTuner's queue failed.

The worker's step is made to land between the reads: the queue's waiting list, asked for,
lets the worker run first and waits for it. Read apart, the worker gets in and the job is
lost from both lists; read under the lock, the worker waits for it.
"""
import os
import sys
import tempfile
import threading
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.jobs.indexing import IndexQueue  # noqa: E402


class ActiveIsOneRead(unittest.TestCase):
    def test_a_job_taken_while_it_is_asked_is_still_seen(self):
        folder = tempfile.mkdtemp(prefix="queue_read_")
        self.addCleanup(own_home.remove, folder)
        queue = IndexQueue("harbour")
        # Queued, with no worker started: the test plays the worker's step itself.
        with mock_runner(queue):
            queue.start([folder], lambda folder, cluster, report: None)
        took = threading.Event()

        def worker_takes_the_job():
            with queue._lock:
                job = queue._pending.pop(0)
                queue._statuses[paths.key(job["folder"])] = {"status": "running", "percent": 0,
                                                            "message": "Starting indexing...",
                                                            "folder": job["folder"]}
            took.set()

        asked = queue.pending

        def pending_after_the_worker():
            threading.Thread(target=worker_takes_the_job, daemon=True).start()
            took.wait(1.0)
            return asked()
        queue.pending = pending_after_the_worker
        found = queue.active()
        self.assertTrue(took.wait(5))
        self.assertTrue(found["busy"], found)
        self.assertEqual(1, found["remaining"], found)


class mock_runner:
    """Start no worker while the block runs."""

    def __init__(self, queue):
        self.queue = queue

    def __enter__(self):
        self.queue._ensure_runner = lambda: None

    def __exit__(self, *exc):
        del self.queue._ensure_runner
        return False


if __name__ == "__main__":
    unittest.main()

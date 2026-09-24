"""The index queue is changed under one lock, and a cancel says so.

index-start, index-cancel and the worker each read the waiting list, changed it and
wrote it back with no lock: a job the worker had just taken could be written back by a
start and indexed twice, and a worker that had found the list empty still looked alive
to a start in the moment before it cleared itself, leaving that start's job queued with
nothing to run it. A race cannot be timed in a test, so the first test reads the code:
every write of the waiting list and the worker happens inside the lock. The queue was
TagTuner's; it is tagpup.jobs.indexing now, for both apps.

Cancelling a queued folder deleted its status, so asking about it answered "completed".
"""
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_tuner_server_api import TestFolderQueue  # noqa: E402

#: A write of the waiting list or the worker.
WRITE = re.compile(r"self\._(pending|runner)\s*=[^=]|self\._pending\.(append|pop|insert|remove|clear)\(")


class EveryQueueWriteIsLocked(unittest.TestCase):
    def test_the_waiting_list_and_the_worker_are_only_written_under_the_lock(self):
        with open(os.path.join(WORKSPACE_DIR, "tagpup", "jobs", "indexing.py"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        writes = [i for i, line in enumerate(lines) if WRITE.search(line)]
        self.assertGreaterEqual(len(writes), 7)
        unlocked = []
        for i in writes:
            indent = len(lines[i]) - len(lines[i].lstrip())
            for j in range(i - 1, -1, -1):
                text = lines[j].strip()
                if not text:
                    continue
                own = len(lines[j]) - len(lines[j].lstrip())
                if own < indent and text.startswith("with ") and "self._lock" in text:
                    break
                if own < indent and (text.startswith("def ") or text.startswith("class ")):
                    # Nothing else can see a queue while it is being made.
                    if not text.startswith("def __init__("):
                        unlocked.append("indexing.py:%d: %s" % (i + 1, lines[i].strip()))
                    break
                indent = min(indent, own) if own < indent else indent
        self.assertEqual([], unlocked, "written without the queue's lock")


class ACancelSaysSo(TestFolderQueue):
    def test_a_cancelled_folder_reports_cancelled(self):
        import urllib.parse

        folders = self.make_folders(2)
        self.post_start(folders)
        self.post("/api/folder/index-cancel", {"folder_paths": [folders[0]]})
        body = self.get("/api/folder/index-status?path=" + urllib.parse.quote(folders[0]))
        self.assertEqual("cancelled", body["status"])


if __name__ == "__main__":
    unittest.main()

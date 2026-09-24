"""TagTuner's index queue is changed under one lock, and a cancel says so.

index-start, index-cancel and the runner each read the pending list, changed it and
wrote it back with no lock: a job the runner had just taken could be written back by a
start and indexed twice, and a runner that had found the list empty still looked alive
to a start in the moment before it cleared itself, leaving that start's job queued with
nothing to run it. A race cannot be timed in a test, so the first test reads the code:
every write of the pending list happens inside the lock.

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


class EveryQueueWriteIsLocked(unittest.TestCase):
    def test_the_pending_list_is_only_written_under_the_lock(self):
        with open(os.path.join(WORKSPACE_DIR, "scripts", "tuner_server.py"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        writes = [i for i, line in enumerate(lines) if re.search(r'index_queue\["(pending|runner)"\]\s*=', line)]
        self.assertGreaterEqual(len(writes), 4)
        unlocked = []
        for i in writes:
            indent = len(lines[i]) - len(lines[i].lstrip())
            for j in range(i - 1, -1, -1):
                text = lines[j].strip()
                if not text:
                    continue
                own = len(lines[j]) - len(lines[j].lstrip())
                if own < indent and text.startswith("with ") and "_index_queue_lock" in text:
                    break
                if own < indent and (text.startswith("def ") or text.startswith("class ")):
                    unlocked.append("tuner_server.py:%d: %s" % (i + 1, lines[i].strip()))
                    break
                indent = min(indent, own) if own < indent else indent
        self.assertEqual([], unlocked, "written without the queue lock")


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

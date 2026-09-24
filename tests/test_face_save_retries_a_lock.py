"""Faces detected by Suggest survive a moment's database lock.

save_faces_if_absent runs its insert inside db.write, which waits out a locked database
and tries again. The insert caught every error itself and returned 0, so the retry
never saw one: a lock held for a moment by another writer lost that photo's faces
until it was indexed again.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
import index as index_module  # noqa: E402
from index import PhotoIndex  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"
FACE = {"box": [1, 2, 3, 4], "embedding": [0.0] * 4, "prob": 0.99}


class FaceSaveRetriesALock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_retry_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.index = PhotoIndex(os.path.join(self.dir, "lib.db"))
        self.index.load()
        self.addCleanup(self.index.close)

    def test_a_lock_once_is_waited_out(self):
        real_connect = tagpup_db.connect
        calls = []

        def connect(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real_connect(*args, **kwargs)

        with mock.patch.object(index_module.tagpup_db, "connect", side_effect=connect), \
                mock.patch("time.sleep"):
            saved = self.index.save_faces_if_absent(PHOTO, [FACE])

        self.assertEqual(1, saved, "the faces were dropped instead of retried")

    def test_a_failure_that_outlasts_the_retries_is_reported_not_raised(self):
        with mock.patch.object(index_module.tagpup_db, "connect",
                               side_effect=sqlite3.OperationalError("database is locked")), \
                mock.patch("time.sleep"), \
                self.assertLogs("tagpup_cli.index", level="ERROR"):
            self.assertEqual(0, self.index.save_faces_if_absent(PHOTO, [FACE]))


if __name__ == "__main__":
    unittest.main()

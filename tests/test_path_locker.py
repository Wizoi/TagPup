"""The path lock must not outlive the process that took it.

A lock is a file created exclusively, which is what makes it atomic between
processes. The failure this covers is the one that actually happened: a run was
killed, its lock files stayed on disk, and every later run silently skipped those
photos. 348 had built up over three months, and nothing in the logs said so.
"""
import os
import sys
import json
import time
import shutil
import socket
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PathLocker


class PathLockerTestBase(unittest.TestCase):
    def setUp(self):
        self.lock_dir = tempfile.mkdtemp(prefix="locks_")
        self.addCleanup(shutil.rmtree, self.lock_dir, True)
        self.photo = os.path.join(self.lock_dir, "photo.jpg")

    def locker(self, **kw):
        return PathLocker(lock_dir=self.lock_dir, **kw)

    def lock_file_for(self, locker, path):
        return locker._get_lock_path(path)

    def write_lock(self, locker, path, pid, host=None, age=0.0, legacy=False):
        """Plant a lock as some other process would have left it."""
        lock_file = self.lock_file_for(locker, path)
        with open(lock_file, "w", encoding="utf-8") as f:
            if legacy:
                f.write(os.path.abspath(path))  # the old format: just a path
            else:
                json.dump({
                    "path": os.path.abspath(path),
                    "pid": pid,
                    "host": socket.gethostname() if host is None else host,
                    "acquired": time.time() - age,
                }, f)
        if age:
            stamp = time.time() - age
            os.utime(lock_file, (stamp, stamp))
        return lock_file


class TestOrdinaryLocking(PathLockerTestBase):
    def test_it_is_told_where_its_locks_are(self):
        # A default of data/locks meant the working directory's, and two indexers
        # started in different folders locked in different places.
        with self.assertRaises(TypeError):
            PathLocker()

    def test_a_free_path_can_be_locked(self):
        self.assertTrue(self.locker().acquire(self.photo))

    def test_a_live_holder_is_respected(self):
        first = self.locker()
        self.assertTrue(first.acquire(self.photo))
        # Same process, so the holder is demonstrably alive.
        self.assertFalse(self.locker().acquire(self.photo))

    def test_releasing_frees_it_again(self):
        first = self.locker()
        first.acquire(self.photo)
        first.release(self.photo)
        self.assertTrue(self.locker().acquire(self.photo))

    def test_release_all_frees_everything_this_process_holds(self):
        locker = self.locker()
        paths = [os.path.join(self.lock_dir, "p%d.jpg" % i) for i in range(3)]
        for p in paths:
            self.assertTrue(locker.acquire(p))
        locker.release_all()
        other = self.locker()
        for p in paths:
            self.assertTrue(other.acquire(p), "%s stayed locked" % p)

    def test_the_lock_records_who_holds_it(self):
        locker = self.locker()
        locker.acquire(self.photo)
        with open(self.lock_file_for(locker, self.photo), encoding="utf-8") as f:
            info = json.load(f)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["host"], socket.gethostname())
        self.assertEqual(info["path"], os.path.abspath(self.photo))
        self.assertLessEqual(info["acquired"], time.time() + 1)


class TestAbandonedLocks(PathLockerTestBase):
    """The bug: a dead holder's lock excluded the photo from every future run."""

    def test_a_lock_from_a_dead_process_is_taken_over(self):
        locker = self.locker()
        self.write_lock(locker, self.photo, pid=999999)
        with mock.patch.object(PathLocker, "_process_alive", return_value=False):
            self.assertTrue(locker.acquire(self.photo),
                            "a dead process's lock still blocked the photo")
        self.assertEqual(locker.stolen, 1)

    def test_taking_over_rewrites_the_lock_as_ours(self):
        locker = self.locker()
        self.write_lock(locker, self.photo, pid=999999)
        with mock.patch.object(PathLocker, "_process_alive", return_value=False):
            locker.acquire(self.photo)
        with open(self.lock_file_for(locker, self.photo), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["pid"], os.getpid())

    def test_a_live_process_keeps_its_lock(self):
        locker = self.locker()
        self.write_lock(locker, self.photo, pid=999999)
        with mock.patch.object(PathLocker, "_process_alive", return_value=True):
            self.assertFalse(locker.acquire(self.photo))
        self.assertEqual(locker.stolen, 0)

    def test_an_old_enough_lock_is_taken_over_whatever_the_pid_says(self):
        locker = self.locker(max_age=60)
        self.write_lock(locker, self.photo, pid=os.getpid(), age=3600)
        self.assertTrue(locker.acquire(self.photo))

    def test_a_lock_in_the_old_format_still_expires_by_age(self):
        """348 real locks are in the old format; they must not be immortal."""
        locker = self.locker(max_age=60)
        self.write_lock(locker, self.photo, pid=0, age=3600, legacy=True)
        self.assertTrue(locker.acquire(self.photo))

    def test_a_fresh_lock_in_the_old_format_is_respected(self):
        locker = self.locker(max_age=3600)
        self.write_lock(locker, self.photo, pid=0, age=0, legacy=True)
        self.assertFalse(locker.acquire(self.photo))

    def test_another_machines_lock_is_not_stolen_on_a_pid_check(self):
        """PIDs are not comparable across hosts -- ours could match theirs."""
        locker = self.locker(max_age=3600)
        self.write_lock(locker, self.photo, pid=os.getpid(), host="some-other-box")
        with mock.patch.object(PathLocker, "_process_alive", return_value=False):
            self.assertFalse(locker.acquire(self.photo))

    def test_another_machines_lock_still_expires_by_age(self):
        locker = self.locker(max_age=60)
        self.write_lock(locker, self.photo, pid=1, host="some-other-box", age=3600)
        self.assertTrue(locker.acquire(self.photo))

    def test_many_abandoned_locks_are_all_recovered(self):
        """The real case had 348 of them, held by the same dead run."""
        locker = self.locker()
        paths = [os.path.join(self.lock_dir, "q%d.jpg" % i) for i in range(5)]
        for p in paths:
            self.write_lock(locker, p, pid=999999)
        with mock.patch.object(PathLocker, "_process_alive", return_value=False):
            for p in paths:
                self.assertTrue(locker.acquire(p), "%s was not recovered" % p)
        self.assertEqual(locker.stolen, 5)


class TestProcessLiveness(PathLockerTestBase):
    def test_our_own_process_is_reported_alive(self):
        self.assertTrue(self.locker()._process_alive(os.getpid()))

    def test_an_implausible_pid_is_reported_dead(self):
        self.assertFalse(self.locker()._process_alive(4294967294))

    def test_the_answer_is_cached_per_pid(self):
        """348 locks from one dead run must not mean 348 subprocess calls."""
        locker = self.locker()
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout="")
            with mock.patch("os.kill", side_effect=ProcessLookupError()):
                for _ in range(5):
                    locker._process_alive(999999)
        self.assertLessEqual(
            run.call_count, 1,
            "liveness was asked %d times for one pid" % run.call_count,
        )

    def test_an_unanswerable_check_assumes_alive(self):
        """Never steal a lock on the strength of a failed check."""
        locker = self.locker()
        with mock.patch("subprocess.run", side_effect=OSError("no process list")), \
             mock.patch("os.kill", side_effect=OSError("nope")):
            self.assertTrue(locker._process_alive(12345))


if __name__ == "__main__":
    unittest.main()

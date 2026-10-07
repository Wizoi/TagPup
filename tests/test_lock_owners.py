"""Which program holds a file (tagpup.files.lock_owners; docs/ARCHITECTURE.md, "File access check"):
Restart Manager names the holder of a file this very test holds open exclusively; an unlocked file, a
file that is not there and a path on a share that does not answer have none; a lookup leaks neither a
handle nor a session; and a sentence names a known scanner by what it is.
"""
import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from unittest import mock

from tagpup.files import lock_owners

ON_WINDOWS = sys.platform == "win32"


def hold_exclusively(path):
    """Open `path` with share mode 0, as a program that lets nobody else in does; returns the handle."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "could not hold the file")
    return handle


def release(handle):
    kernel = ctypes.WinDLL("kernel32")
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(handle)


def handle_count():
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    count = wintypes.DWORD(0)
    kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def lookups_ended():
    """Wait for every lookup's thread to end. One that timed out on a busy machine is still running, holding its
    handles, and was counted as a leak (#721)."""
    for thread in threading.enumerate():
        if thread.name == "lock-owners":
            thread.join(30)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="lock_owners_")
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "IMG_0001.jpg")
        with open(self.path, "wb") as handle:
            handle.write(b"x" * 100)


@unittest.skipUnless(ON_WINDOWS, "Restart Manager is Windows'")
class RestartManagerNamesTheHolder(Base):
    def test_a_file_held_exclusively_names_this_process(self):
        handle = hold_exclusively(self.path)
        try:
            found = lock_owners.holders(self.path)
        finally:
            release(handle)
        self.assertIn(os.getpid(), [each["pid"] for each in found], found)
        mine = [each for each in found if each["pid"] == os.getpid()][0]
        self.assertEqual({"name", "pid", "kind", "service"}, set(mine))
        self.assertEqual(os.path.basename(sys.executable).lower(), mine["name"].lower().replace("pythonw", "python"))
        self.assertIn("this TagPup process", lock_owners.describe(found))

    def test_an_unlocked_file_has_no_holder(self):
        self.assertEqual([], lock_owners.holders(self.path))

    def test_a_file_that_is_not_there_has_none(self):
        self.assertEqual([], lock_owners.holders(self.path + ".gone"))
        self.assertEqual([], lock_owners.holders(""))

    def test_a_lookup_leaks_no_handle(self):
        lock_owners.holders(self.path)
        lookups_ended()
        before = handle_count()
        for _ in range(200):
            lock_owners.holders(self.path)
        lookups_ended()
        self.assertLessEqual(handle_count() - before, 3)

    def test_a_held_file_leaks_no_handle_either(self):
        handle = hold_exclusively(self.path)
        try:
            lock_owners.holders(self.path)
            lookups_ended()
            before = handle_count()
            for _ in range(50):
                lock_owners.holders(self.path)
            lookups_ended()
            self.assertLessEqual(handle_count() - before, 3)
        finally:
            release(handle)


class NeverRaisesNeverWaitsLong(Base):
    def test_a_share_that_hangs_is_given_up_on_within_the_timeout(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def hang(_path):
            gate.wait(30)
            return [(1, "", 0, "x")]

        with mock.patch.object(lock_owners, "_ask", hang), mock.patch.object(lock_owners, "available", lambda: True):
            started = time.monotonic()
            found = lock_owners.holders(r"\\nas\photos\IMG_0001.jpg", timeout=0.2)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual([], found)
            self.assertEqual(1, lock_owners._stuck)
            gate.set()
            for _ in range(100):
                if lock_owners._stuck == 0:
                    break
                time.sleep(0.05)
            self.assertEqual(0, lock_owners._stuck, "the thread that ended is no longer counted as stuck")

    def test_stuck_lookups_are_capped(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with mock.patch.object(lock_owners, "_ask", lambda _p: gate.wait(30) or []), \
                mock.patch.object(lock_owners, "available", lambda: True):
            for _ in range(lock_owners.MOST_STUCK):
                lock_owners.holders(self.path, timeout=0.05)
            started = time.monotonic()
            self.assertEqual([], lock_owners.holders(self.path, timeout=5))
            self.assertLess(time.monotonic() - started, 1.0, "past the cap a lookup does not wait at all")
            gate.set()
            for _ in range(100):
                if lock_owners._stuck == 0:
                    break
                time.sleep(0.05)

    def test_any_failure_is_no_holder(self):
        def broken(_path):
            raise OSError("rstrtmgr.dll is not there")

        with mock.patch.object(lock_owners, "_ask", broken), mock.patch.object(lock_owners, "available", lambda: True):
            self.assertEqual([], lock_owners.holders(self.path))

    def test_where_restart_manager_is_unavailable_it_is_silent(self):
        with mock.patch.object(lock_owners, "available", lambda: False):
            self.assertEqual([], lock_owners.holders(self.path))
            self.assertEqual({}, lock_owners.running())


class TheSentence(unittest.TestCase):
    def holder(self, name, pid=4242):
        return {"name": name, "pid": pid, "kind": "service", "service": None}

    def test_known_programs_are_named_by_what_they_are(self):
        said = lock_owners.describe([self.holder("MsMpEng.exe")])
        self.assertEqual("held open by MsMpEng.exe (Windows Security / Microsoft Defender real-time scanning)", said)
        self.assertIn("(Windows Search)", lock_owners.describe([self.holder("SearchIndexer.exe")]))
        self.assertEqual("held open by OneDrive.exe (OneDrive sync)", lock_owners.describe([self.holder("OneDrive.exe")]))

    def test_python_is_another_tagpup_process(self):
        self.assertIn("python.exe (another TagPup process)", lock_owners.describe([self.holder("python.exe")]))
        self.assertIn("(this TagPup process)", lock_owners.describe([self.holder("python.exe", os.getpid())]))

    def test_an_unknown_program_is_named_and_nobody_is_not_a_sentence(self):
        self.assertEqual("held open by Sketchy.exe", lock_owners.describe([self.holder("Sketchy.exe")]))
        self.assertEqual("held open by an unknown process", lock_owners.describe([self.holder("an unknown process")]))
        self.assertEqual("", lock_owners.describe([]))

    def test_two_holders_are_both_named_once(self):
        said = lock_owners.describe([self.holder("MsMpEng.exe"), self.holder("SearchIndexer.exe", 7),
                                     self.holder("MsMpEng.exe", 8)])
        self.assertEqual(1, said.count("MsMpEng.exe"))
        self.assertIn("SearchIndexer.exe", said)


class ExplainAFailure(unittest.TestCase):
    def test_a_held_file_error_gains_the_holder(self):
        error = PermissionError(13, "Permission denied")
        with mock.patch.object(lock_owners, "holders", lambda path, timeout=2.0: [
                {"name": "MsMpEng.exe", "pid": 9, "kind": "service", "service": "WinDefend"}]):
            said = lock_owners.explain(error, "x.jpg")
        self.assertIn("It is held open by MsMpEng.exe (Windows Security", said)
        self.assertNotIn("x.jpg", said, "the sentence adds no path")

    def test_no_holder_leaves_the_error_itself(self):
        error = PermissionError(13, "Permission denied")
        with mock.patch.object(lock_owners, "holders", lambda path, timeout=2.0: []):
            self.assertIs(error, lock_owners.explain(error, "x.jpg"))

    def test_an_error_of_another_kind_asks_nobody(self):
        asked = []
        with mock.patch.object(lock_owners, "holders", lambda *a, **k: asked.append(a) or []):
            error = FileNotFoundError(2, "No such file or directory")
            self.assertIs(error, lock_owners.explain(error, "x.jpg"))
            self.assertEqual("the disk is full", lock_owners.explain("the disk is full", "x.jpg"))
        self.assertEqual([], asked)

    def test_exiftools_words_for_a_held_file_count(self):
        with mock.patch.object(lock_owners, "holders", lambda path, timeout=2.0: [
                {"name": "OneDrive.exe", "pid": 9, "kind": "application", "service": None}]):
            said = lock_owners.explain("Error renaming temporary file to IMG_0001.jpg", "x.jpg")
        self.assertTrue(said.endswith("It is held open by OneDrive.exe (OneDrive sync)."), said)

    def test_a_lookup_that_raises_leaves_the_error(self):
        def raises(*_a, **_k):
            raise RuntimeError("boom")

        error = OSError(32, "used by another process")
        with mock.patch.object(lock_owners, "holders", raises):
            self.assertIs(error, lock_owners.explain(error, "x.jpg"))


if __name__ == "__main__":
    unittest.main()

"""Which program holds a file (tagpup.files.lock_owners; docs/ARCHITECTURE.md, "File access check"):
Restart Manager names the holder of a file this very test holds open exclusively; an unlocked file, a
file that is not there and a path on a share that does not answer have none; a lookup leaks neither a
handle nor a session; and a sentence names a known scanner by what it is, each program once with every pid. A rename of a file
held exclusively -- by this very test -- names this process; a failed write (file-only or journaled) names the holder the error text
has no word for; a write that succeeds, and a failure of another kind, ask nobody. Access denied is a holder's only of a file there
and not read-only (#2970); a file on a share no program on this PC holds says so; a lookup that does not answer keeps the error.
Every operation asks the one explanation (lock_owners.explain, owner 2026-11-13): a rename and a clip's patch refused by the same
holder say the same sentence of who. (Folded in from test_a_failed_write_names_the_holder.)
"""
import ctypes
import os
import re
import stat
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from unittest import mock

from tagpup.core.result import Refused
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from waiting import wait_until  # noqa: E402
from tagpup.files import field_values, in_place, lock_owners, names
from tagpup.services import file_changes, file_only

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
            wait_until(lambda: lock_owners._stuck == 0, 5, 0.05, "the thread that ended no longer counted as stuck")

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
            wait_until(lambda: lock_owners._stuck == 0, 5, 0.05, "the stuck lookups' threads let go")

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

    def test_known_programs_are_named_by_what_they_are_with_their_pid(self):
        self.assertEqual("held open by MsMpEng.exe (Windows Security / Microsoft Defender real-time scanning, pid 4242)",
                         lock_owners.describe([self.holder("MsMpEng.exe")]))
        self.assertEqual("held open by SearchIndexer.exe (Windows Search, pid 4242)",
                         lock_owners.describe([self.holder("SearchIndexer.exe")]))
        self.assertEqual("held open by OneDrive.exe (OneDrive sync, pid 4242)",
                         lock_owners.describe([self.holder("OneDrive.exe")]))

    def test_python_is_another_tagpup_process(self):
        self.assertEqual("held open by python.exe (another TagPup process, pid 4242)",
                         lock_owners.describe([self.holder("python.exe")]))
        self.assertEqual("held open by python.exe (this TagPup process, pid %d)" % os.getpid(),
                         lock_owners.describe([self.holder("python.exe", os.getpid())]))

    def test_an_unknown_program_is_named_with_its_pid_and_nobody_is_not_a_sentence(self):
        self.assertEqual("held open by Sketchy.exe (pid 4242)", lock_owners.describe([self.holder("Sketchy.exe")]))
        self.assertEqual("held open by an unknown process (pid 9)",
                         lock_owners.describe([self.holder("an unknown process", 9)]))
        self.assertEqual("", lock_owners.describe([]))

    def test_each_program_is_named_once_with_every_pid_that_holds_it(self):
        said = lock_owners.describe([self.holder("MsMpEng.exe"), self.holder("SearchIndexer.exe", 7),
                                     self.holder("MsMpEng.exe", 8), self.holder("MsMpEng.exe", 8)])
        self.assertEqual("held open by MsMpEng.exe (Windows Security / Microsoft Defender real-time scanning, pids 4242, 8),"
                         " SearchIndexer.exe (Windows Search, pid 7)", said)
        self.assertEqual("held open by Sketchy.exe (pids 7, 8)",
                         lock_owners.describe([self.holder("Sketchy.exe", 7), self.holder("Sketchy.exe", 8)]))


def holding(found):
    """lock_owners.holders stood in for: `found` for any path; what it was asked, in `asked`."""
    asked = []

    def holders(path, timeout=lock_owners.TIMEOUT):
        asked.append(path)
        return found
    return mock.patch.object(lock_owners, "holders", holders), asked


class ExplainAFailure(Base):
    def test_access_denied_gains_the_holder_only_of_a_file_there_and_writable(self):
        error = PermissionError(13, "Permission denied")
        stand_in, asked = holding([{"name": "MsMpEng.exe", "pid": 9, "kind": "service", "service": "WinDefend"}])
        with stand_in:
            said = lock_owners.explain(error, self.path, writable=True)
            self.assertIs(error, lock_owners.explain(error, self.path), "not known writable: no holder is asked")
        self.assertEqual("[Errno 13] Permission denied. It is held open by MsMpEng.exe (Windows Security / Microsoft"
                         " Defender real-time scanning, pid 9).", said)
        self.assertEqual([self.path], asked)
        self.assertNotIn("IMG_0001", said, "the sentence adds no path")

    def test_a_read_only_files_access_denied_is_unchanged_and_asks_nobody(self):
        denied = OSError(None, "Access is denied", self.path, 5)
        stand_in, asked = holding([{"name": "MsMpEng.exe", "pid": 9, "kind": "service", "service": None}])
        with stand_in:
            self.assertIs(denied, lock_owners.explain(denied, self.path))
            self.assertEqual("it could not be renamed", lock_owners.explain(denied, self.path, "it could not be renamed"))
        self.assertEqual([], asked)

    def test_no_holder_on_this_pcs_disk_leaves_the_error_itself(self):
        error = OSError(None, "The process cannot access the file", self.path, 32)
        stand_in, asked = holding([])
        with stand_in:
            self.assertIs(error, lock_owners.explain(error, self.path))
            self.assertEqual("it is open elsewhere", lock_owners.explain(error, self.path, "it is open elsewhere"))
        self.assertEqual([self.path, self.path], asked)

    def test_a_file_on_a_share_no_program_on_this_pc_holds_says_so(self):
        on_the_server = "\\\\fileserver\\Pictures\\Harbour\\IMG_0001.jpg"
        violation = OSError(None, "The process cannot access the file", on_the_server, 32)
        stand_in, _asked = holding([])
        with stand_in:
            said = lock_owners.explain(violation, on_the_server, "it is open in another program")
        self.assertEqual("it is open in another program. " + lock_owners.NOT_ON_THIS_PC, said)

    def test_a_lookup_that_does_not_answer_keeps_the_error(self):
        let_go = threading.Event()
        self.addCleanup(lookups_ended)
        self.addCleanup(let_go.set)
        error = OSError(None, "The process cannot access the file", self.path, 32)
        with mock.patch.object(lock_owners, "available", return_value=True), \
                mock.patch.object(lock_owners, "_holders", side_effect=lambda path: let_go.wait(30) and []):
            self.assertIs(error, lock_owners.explain(error, self.path, timeout=0.05))

    def test_an_error_of_another_kind_asks_nobody(self):
        stand_in, asked = holding([])
        with stand_in:
            error = FileNotFoundError(2, "No such file or directory")
            self.assertIs(error, lock_owners.explain(error, self.path, writable=True))
            self.assertEqual("the disk is full", lock_owners.explain("the disk is full", self.path))
        self.assertEqual([], asked)

    def test_exiftools_words_count_as_its_numbers_do(self):
        stand_in, asked = holding([{"name": "OneDrive.exe", "pid": 9, "kind": "application", "service": None}])
        with stand_in:
            said = lock_owners.explain("Permission denied: IMG_0001.jpg", self.path, writable=True)
            held = lock_owners.explain("The file is used by another process", self.path)
            unknown = lock_owners.explain("Permission denied: IMG_0001.jpg", self.path)
        self.assertTrue(said.endswith("It is held open by OneDrive.exe (OneDrive sync, pid 9)."), said)
        self.assertTrue(held.endswith("It is held open by OneDrive.exe (OneDrive sync, pid 9)."), held)
        self.assertEqual("Permission denied: IMG_0001.jpg", unknown, "access denied's words need writable")
        self.assertEqual(2, len(asked))

    def test_a_lookup_that_raises_leaves_the_error(self):
        def raises(*_a, **_k):
            raise RuntimeError("boom")

        error = OSError(32, "used by another process")
        with mock.patch.object(lock_owners, "holders", raises):
            self.assertIs(error, lock_owners.explain(error, self.path))


MSMPENG = [{"name": "MsMpEng.exe", "pid": 9, "kind": "service", "service": "WinDefend"}]
FIELDS_BEFORE, FIELDS_AFTER = {"XMP-dc:Subject": ["a"]}, {"XMP-dc:Subject": ["b"]}


@unittest.skipUnless(sys.platform == "win32", "a held file is Windows'")
class ARenameOfAHeldFile(unittest.TestCase):
    def test_names_the_process_that_holds_it(self):
        with tempfile.TemporaryDirectory(prefix="held_rename_") as folder:
            old, new = os.path.join(folder, "IMG_0001.jpg"), os.path.join(folder, "Regatta - 1.jpg")
            with open(old, "wb") as handle:
                handle.write(b"x")
            handle = hold_exclusively(old)
            try:
                with self.assertRaises(names.RenameFailed) as raised, self.assertLogs(names.logger, "ERROR") as logged:
                    names.rename_all({old: new})
            finally:
                release(handle)
            said = raised.exception.message()
            self.assertNotIn("IMG_0001", " ".join(logged.output), "a file name can name a person (#2973)")
            self.assertIn("It is held open by", said)
            self.assertIn("this TagPup process", said)
            self.assertIn("Every photo was put back under its old name.", said)
            self.assertNotIn("..", said)
            self.assertTrue(os.path.exists(old))

    def test_a_rename_that_works_asks_nobody(self):
        with tempfile.TemporaryDirectory(prefix="held_rename_") as folder:
            old, new = os.path.join(folder, "IMG_0001.jpg"), os.path.join(folder, "Regatta - 1.jpg")
            with open(old, "wb") as handle:
                handle.write(b"x")
            with mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
                names.rename_all({old: new})
            self.assertTrue(os.path.exists(new))


class AFailedWrite(Base):
    def only(self, error):
        result = mock.Mock(details={"conflicts": []})
        with mock.patch.object(field_values, "write", side_effect=error), \
                mock.patch.object(field_values, "read_one", return_value=FIELDS_BEFORE):
            return file_only._write_one(object(), self.path, FIELDS_BEFORE, FIELDS_AFTER, True, result)

    def test_a_file_only_write_names_the_holder(self):
        with mock.patch.object(lock_owners, "holders", return_value=MSMPENG):
            said = self.only(PermissionError(13, "Permission denied"))
        self.assertIn("It is held open by MsMpEng.exe (Windows Security / Microsoft Defender real-time scanning, pid 9).",
                      str(said))
        self.assertNotIn("IMG_0001", str(said).split("It is held open")[1], "the sentence adds no path")

    def test_a_read_only_files_access_denied_asks_nobody(self):
        os.chmod(self.path, stat.S_IREAD)
        self.addCleanup(os.chmod, self.path, stat.S_IREAD | stat.S_IWRITE)
        with mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
            said = self.only(PermissionError(13, "Permission denied"))
        self.assertEqual("[Errno 13] Permission denied", str(said))

    def test_a_failure_of_another_kind_asks_nobody(self):
        with mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
            said = self.only(RuntimeError("ExifTool said: File format error"))
        self.assertEqual("ExifTool said: File format error", str(said))

    def test_a_journaled_write_names_the_holder(self):
        library = mock.Mock(path="library.db")
        row = mock.Mock(path=self.path, id=3, stamp=None, patched=False, stale=())
        error = RuntimeError("Permission denied: IMG_0001.jpg")
        with mock.patch.object(field_values, "read_one", return_value=FIELDS_BEFORE), \
                mock.patch.object(file_changes.file_journal, "withdraw") as withdraw, \
                mock.patch.object(lock_owners, "holders", return_value=MSMPENG):
            outcome, why = file_changes._after_failure(object(), library, row, FIELDS_BEFORE, FIELDS_AFTER,
                                                       "done", "withdraw", error)
        withdraw.assert_called_once()
        self.assertEqual("failed", outcome)
        self.assertIn("It is held open by MsMpEng.exe", str(why))

    def test_a_write_that_turns_out_to_have_worked_asks_nobody(self):
        library = mock.Mock(path="library.db")
        row = mock.Mock(path=self.path, id=3, stamp=None, patched=False, stale=())
        with mock.patch.object(field_values, "read_one", return_value=FIELDS_AFTER), \
                mock.patch.object(file_changes, "_record_held"), \
                mock.patch.object(lock_owners, "holders", side_effect=AssertionError("asked")):
            outcome, why = file_changes._after_failure(object(), library, row, FIELDS_BEFORE, FIELDS_AFTER,
                                                       "done", "withdraw", PermissionError(13, "Permission denied"))
        self.assertEqual(("done", None), (outcome, why))


@unittest.skipUnless(ON_WINDOWS, "a held file is Windows'")
class OneHolderOneSentence(Base):
    """The owner's scenario: a viewer holds a file; Smart Rename fails on it, and a Date Taken save of a clip fails on it.
    Both say who holds it in the same words, from the one explanation (owner, 2026-11-13)."""

    def test_a_rename_and_a_clips_patch_refused_by_one_holder_say_the_same_who(self):
        clip = os.path.join(os.path.dirname(self.path), "jetty.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"x" * 100)
        renamed_to = os.path.join(os.path.dirname(self.path), "Regatta - 1.jpg")
        handles = [hold_exclusively(self.path), hold_exclusively(clip)]
        try:
            with self.assertRaises(names.RenameFailed) as rename:
                names.rename_all({self.path: renamed_to})
            with self.assertRaises(Refused) as patch:
                in_place._opened_to_patch(clip)
        finally:
            for handle in handles:
                release(handle)
        said = [rename.exception.message(), str(patch.exception)]
        who = [re.findall(r"It is held open by [^()]*\([^()]*\)\.", each) for each in said]
        self.assertEqual([["It is held open by %s (this TagPup process, pid %d)." % (who[0][0].split()[5], os.getpid())]] * 2,
                         who, said)
        self.assertTrue(said[1].startswith(in_place.OPEN_ELSEWHERE), said[1])


if __name__ == "__main__":
    unittest.main()

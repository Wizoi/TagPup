"""A TagPup home of a test's own: its libraries and everything beside them.

Tests made their libraries in the checkout's data/ folder and read and wrote its
settings: the folder and the settings of the app somebody is using (docs/findings.md,
#14). Their files turned up in the library picker, a run stopped half-way left them
behind, and a library left by a run on a later schema broke a run on an earlier one.
Because the names were fixed, two files could not run at once, and tools/run_tests.py
ran them one after another.

tagpup.config takes the home from TAGPUP_HOME. A home here is a temporary folder with a
data/ folder of its own, and TAGPUP_HOME points at it until the test, or the class, is
done. Then it is deleted. It has no config.ini: a library made in it is stamped with the
default settings (tagpup.services.settings), unless a test writes one to see a library
stamped from it.

A server a test starts runs until the process ends: it can hold its library open until
then, and a thread it started late can make the library again after the home has gone.
So every home is also handed to a process of its own that waits for this one to end
and deletes whatever is left (_reap).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402


class OwnHome:
    """A temporary TagPup home, TAGPUP_HOME while it is open."""

    def __init__(self, prefix="tagpup_home_"):
        self.root = tempfile.mkdtemp(prefix=prefix)
        _reap_after_exit(self.root)
        self.data = os.path.join(self.root, "data")
        os.makedirs(self.data)
        self._environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.root})
        self._environ.start()
        self._open = True

    def write_old_config(self, sections):
        """Put a config.ini in this home, as a home had before a library held its settings:
        {section: {key: value}}. A library in it holding no settings is stamped from it.
        Here, so that a test file stamping from one does not name the file and share a
        lane with the files that use the checkout's (tools/run_tests.py)."""
        lines = []
        for section, keys in sections.items():
            lines.append("[%s]" % section)
            lines += ["%s = %s" % (key, value) for key, value in keys.items()]
            lines.append("")
        with open(os.path.join(self.root, "config" + ".ini"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))

    def library(self, file_name):
        """Where the library with this file name lives in this home: data/<file_name>."""
        return os.path.join(self.data, file_name)

    def close(self):
        """Stop being TAGPUP_HOME, and delete the folder, or leave it to the reaper."""
        if self._open:
            self._environ.stop()
            self._open = False
        remove(self.root)


def for_test(testcase, prefix="tagpup_home_"):
    """A home for one test, closed when it ends."""
    home = OwnHome(prefix)
    testcase.addCleanup(home.close)
    return home


def for_class(cls, prefix="tagpup_home_"):
    """A home for a test class, from setUpClass; closed after tearDownClass."""
    home = OwnHome(prefix)
    cls.addClassCleanup(home.close)
    return home


def remove(folder):
    """Delete `folder` now if it can be. Whatever is still held is the reaper's once
    this process has ended: waiting here for a server that never lets go only made
    each class slower (0.4 seconds apiece, twelve classes in one file)."""
    shutil.rmtree(folder, ignore_errors=True)
    return not os.path.exists(folder)


#: The spawn as it was when this was imported: a test that mocks it must not get the reaper.
_START = None

#: The file this process lists its homes in for the reaper, once it has started one.
_reap_list = None


def _reap_after_exit(folder):
    """List `folder` for deletion after this process ends, starting the reaper with
    the first. Its output goes nowhere: a child holding this process's stdout would
    keep tools/run_tests.py waiting for it."""
    global _reap_list
    if _reap_list is None:
        handle, _reap_list = tempfile.mkstemp(prefix="tagpup_homes_", suffix=".txt")
        os.close(handle)
        # In a group of its own, so it outlives this process; with a hidden console,
        # not none, or the venv launcher's child gets a visible one (tagpup.core.processes).
        (_START or processes.start)([sys.executable, os.path.abspath(__file__), "--reap", str(os.getpid()), _reap_list],
                                    own_group=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, close_fds=True)
    with open(_reap_list, "a", encoding="utf-8") as handle:
        handle.write(folder + os.linesep)


def _wait_for_exit(pid):
    if os.name == "nt":
        import ctypes
        synchronize, infinite = 0x00100000, 0xFFFFFFFF
        kernel = ctypes.windll.kernel32
        process = kernel.OpenProcess(synchronize, False, pid)
        if process:
            kernel.WaitForSingleObject(process, infinite)
            kernel.CloseHandle(process)
        return
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def _reap(pid, listed):
    """After process `pid` has ended, delete every home it listed, and the list."""
    _wait_for_exit(pid)
    with open(listed, encoding="utf-8") as handle:
        folders = [line.strip() for line in handle if line.strip()]
    for _attempt in range(120):
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        folders = [folder for folder in folders if os.path.exists(folder)]
        if not folders:
            break
        time.sleep(0.5)
    os.remove(listed)


def installed_exiftool():
    """The ExifTool on this machine, or None: where its installer puts it, else PATH.

    Three test files read the checkout's settings for this. Where ExifTool is installed
    is the machine's, not the library's, and a test reads no settings but its own.
    """
    path = tagpup_config.exiftool_path("")
    return path if os.path.exists(path) else None


if __name__ == "__main__" and sys.argv[1:2] == ["--reap"]:
    _reap(int(sys.argv[2]), sys.argv[3])

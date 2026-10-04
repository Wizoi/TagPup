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

A test that starts servers or supervisors ends them with `end_processes(home.root)`,
registered before it starts any: each it started, and each they started, ending those
too. A test that failed part-way left four running for hours (docs/findings.md, #727):
its cleanup ended only the processes the records named at that moment, and a supervisor
waiting to take over started a server once the one before it was gone. The reaper does
the same after the test's process has ended, however it ended.
"""
import json
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
        # The Downloads folder a delete from a share is copied through is the home's too (tagpup.files.recycle_bin, #694):
        # a test never writes the owner's Downloads; and the Recycle Bin's size and settings are a large empty Bin's (#703),
        # so no test reads the owner's (asking Windows took 11.8 s here).
        # The records a server writes of where it answers (tagpup.launcher) are the home's too,
        # not the user's folder the owner's servers write theirs in.
        self._environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.root,
                                                     "TAGPUP_SERVERS": os.path.join(self.root, "servers"),
                                                     "TAGPUP_DOWNLOADS": os.path.join(self.root, "Downloads"),
                                                     "TAGPUP_RECYCLE_BIN": "1000000,0,0"})
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


#: The records the processes of a home keep of themselves (tagpup.supervisor, tagpup.launcher).
_RECORDS = (("data", "supervisor.json"), ("data", "server.json"), ("data", "supervisor.handover.json"))

#: Lists the processes whose command line names the folder in TAGPUP_END_UNDER (Windows).
_UNDER = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
          "$_.CommandLine.Contains($env:TAGPUP_END_UNDER) } | ForEach-Object { $_.ProcessId }")


def _recorded(root):
    """The live processes the home's records name: [pid]."""
    paths = [os.path.join(root, *parts) for parts in _RECORDS]
    servers = os.path.join(root, "servers")
    if os.path.isdir(servers):
        paths += [os.path.join(servers, name) for name in os.listdir(servers) if name.endswith(".json")]
    found = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            continue
        pid = record.get("pid") if isinstance(record, dict) else None
        # By its start too: an id is soon another process's once its own has ended.
        alive = isinstance(pid, int) and pid != os.getpid() and processes.started(pid) is not None
        if alive and processes.started(pid) == record.get("started"):
            found.append(pid)
    return found


def _named_under(root):
    """The processes whose command line names `root` (Windows; elsewhere none)."""
    if os.name != "nt":
        return []
    try:
        out = processes.run(["powershell", "-NoProfile", "-Command", _UNDER], capture_output=True, text=True,
                            timeout=60, env=dict(os.environ, TAGPUP_END_UNDER=os.path.normpath(root))).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in out.split() if line.strip().isdigit() and int(line) != os.getpid()]


def ran_processes(root):
    """Did processes keep records in the home `root`: is there anything to end?"""
    return os.path.isdir(os.path.join(root, "servers")) or any(
        os.path.exists(os.path.join(root, *parts)) for parts in _RECORDS)


def end_processes(root, rounds=10):
    """End every process started from the home `root` -- each its records name, and each
    whose command line names it -- and ask again until none is left, for at most `rounds`.
    Returns the ids ended."""
    ended = []
    for _round in range(rounds):
        found = sorted(set(_recorded(root)) | set(_named_under(root)))
        if not found:
            break
        for pid in found:
            processes.kill_tree(pid)
            ended.append(pid)
        time.sleep(0.5)
    return ended


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
    for folder in folders:
        if ran_processes(folder):
            end_processes(folder)
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

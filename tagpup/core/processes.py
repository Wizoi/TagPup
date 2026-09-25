"""Starting, running and stopping other processes: the one place that knows how.

Every spawn used to call subprocess itself, and each decided on its own whether the
child got a console window. Most did not decide at all, and on Windows a console
program started by a process without a console -- a test run, the runner, the
test-home reaper -- is given a new one: a terminal window on the desktop for every
server a test started, and one for every test process's reaper, 140 a run. It took
watching the desktop's process list to find which spawn it was (docs/findings.md,
#124). So the flag lives here, once, and tests/test_processes_single_owner.py fails
any other module that calls subprocess.Popen or run, or spells a creation flag or
taskkill.

    from tagpup.core import processes
    processes.run(["git", "status"], capture_output=True, text=True)
    server = processes.start([sys.executable, "tagpup_web.py"])
    processes.kill_tree(server.pid)

A child never gets a window of its own. A program that must show one -- Explorer, a
dialog -- is a GUI program, which makes its own window regardless.
"""
import os
import subprocess

#: Windows: a hidden console for the child, which its own children inherit. Not
#: DETACHED_PROCESS, which gives none: the venv's python.exe is a launcher that starts
#: the real interpreter as a child, and a child with no console to inherit is given a
#: new, visible one.
_HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Windows: the child answers to Ctrl+C on its own, not with this process's group.
_OWN_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _flags(own_group):
    flags = _HIDDEN
    if own_group:
        flags |= _OWN_GROUP
    return flags


def start(args, own_group=False, **kwargs):
    """subprocess.Popen, without a console window. `own_group` puts the child in a
    process group of its own, for one meant to outlive this process."""
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _flags(own_group)
    return subprocess.Popen(args, **kwargs)


def run(args, **kwargs):
    """subprocess.run, without a console window."""
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _flags(False)
    return subprocess.run(args, **kwargs)


def kill_tree(pid, timeout=30):
    """End process `pid` and everything it started. On Windows through taskkill /T,
    the one thing that reaches a server's indexer; elsewhere SIGTERM to the process."""
    if os.name == "nt":
        run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout)
        return
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def is_alive(pid):
    """Is there a process `pid`? Best effort; when in doubt, alive."""
    try:
        if os.name == "nt":
            out = run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                      capture_output=True, text=True, timeout=10).stdout
            return str(int(pid)) in out
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


#: Windows: OpenProcess's right to read a process's times, and the exit code of one
#: still running.
_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def started(pid):
    """When process `pid` started, as a whole number that two processes given the same id
    one after the other do not share: Windows reuses an id soon after its process ends.
    None when there is no such process running, or its start cannot be read."""
    try:
        if os.name == "nt":
            return _started_on_windows(int(pid))
        with open("/proc/%d/stat" % int(pid), encoding="ascii", errors="replace") as handle:
            # The 22nd field, after the name in parentheses (which may hold spaces).
            return int(handle.read().rpartition(")")[2].split()[19])
    except Exception:
        return None


def _started_on_windows(pid):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        # A process that has ended but whose handle another still holds is not running.
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)

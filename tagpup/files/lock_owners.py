"""Which program holds a file open: named after a write fails, never before one is tried
(docs/ARCHITECTURE.md, "File access check").

A write that fails with "access is denied" or "used by another process" says nothing of WHO.
The answer is often not TagPup: Windows Defender scanning the file it was just given, the
Search indexer, a cloud-sync client, a backup, or another TagPup process. Windows' Restart
Manager (the API an installer asks "which programs must close to replace this file?") lists
them, with no administrator and without touching the file: RmStartSession,
RmRegisterResources, RmGetList, RmEndSession.

`holders(path)` is that list, `describe(holders)` a short sentence for the owner, and
`explain(error, path)` an error with that sentence added, when -- and only when -- the error
is the kind a held file makes. It is called after a failure, never on the way to a success, and
it is bounded: a lookup that is not answered within `timeout` seconds (a share that stopped
answering) is given up on, its thread left to end when Windows lets it, and is [] -- no
holder known, which is a fact about the lookup, not about the file. It never raises and
off Windows it is [].

Names only, as `MsMpEng.exe`: no path, no command line, no user.
"""
import ctypes
import os
import re
import sys
import threading

try:
    import ctypes.wintypes as wintypes
except (ImportError, ValueError):   # a platform without the Windows types: every lookup there is []
    wintypes = None

#: How long a lookup is waited for, in seconds.
TIMEOUT = 2.0

#: The most lookups left waiting on a share that does not answer; past it a lookup is [] at once, so a dead share
#: cannot pile up threads.
MOST_STUCK = 4

#: What a program is called to the owner, by its executable's name without ".exe", lower case.
KNOWN = {
    "msmpeng": "Windows Security / Microsoft Defender real-time scanning",
    "mpdefendercoreservice": "Windows Security / Microsoft Defender",
    "nissrv": "Windows Security / Microsoft Defender network inspection",
    "mpcmdrun": "Windows Security / Microsoft Defender scan",
    "securityhealthservice": "Windows Security",
    "searchindexer": "Windows Search",
    "searchprotocolhost": "Windows Search",
    "searchfilterhost": "Windows Search",
    "onedrive": "OneDrive sync",
    "filecoauth": "OneDrive sync",
    "dropbox": "Dropbox sync",
    "googledrivefs": "Google Drive sync",
    "icloud": "iCloud sync",
    "iclouddrive": "iCloud sync",
    "backblaze": "Backblaze backup",
    "bzserv": "Backblaze backup",
    "bztransmit": "Backblaze backup",
    "bztransmit64": "Backblaze backup",
    "crashplanservice": "CrashPlan backup",
    "acronis": "Acronis backup",
    "tbsvc": "Acronis backup",
    "veeam": "Veeam backup",
    "plex media server": "Plex media scanning",
    "avastsvc": "Avast antivirus",
    "avgsvc": "AVG antivirus",
    "mbamservice": "Malwarebytes",
    "ekrn": "ESET antivirus",
    "bdagent": "Bitdefender antivirus",
    "vsserv": "Bitdefender antivirus",
    "avp": "Kaspersky antivirus",
    "mfemms": "McAfee antivirus",
    "mcshield": "McAfee antivirus",
    "ns": "Norton antivirus",
    "nortonsecurity": "Norton antivirus",
    "ccsvchst": "Norton antivirus",
    "savservice": "Sophos antivirus",
    "sophosfilescanner": "Sophos antivirus",
    "explorer": "Windows Explorer, previewing or thumbnailing it",
    "dllhost": "a Windows thumbnail or preview host",
    "prevhost": "the Windows preview pane",
}

#: Programs that are TagPup's own (or run it): another of them is the likeliest holder.
TAGPUP = {"python": "another TagPup process", "pythonw": "another TagPup process",
          "exiftool": "ExifTool, which TagPup started", "exiftool(-k)": "ExifTool, which TagPup started"}

#: Windows' error numbers of a file held open by another program: access denied, sharing violation, lock violation.
HELD = (5, 32, 33)
#: What an error says in words (ExifTool's, which has no number) when a held file is the likely cause.
HELD_WORDS = re.compile(r"permission denied|access is denied|used by another process|sharing violation|"
                        r"error renaming temporary file|locked by another", re.IGNORECASE)

ERROR_MORE_DATA = 234
_SESSION_KEY = 33
_stuck = 0
_guard = threading.Lock()


def available():
    """Can Restart Manager be asked here (Windows, with the Windows types)?"""
    return sys.platform == "win32" and wintypes is not None


if wintypes is not None:
    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


    class _UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", _FILETIME)]


    class _PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", _UNIQUE_PROCESS), ("strAppName", ctypes.c_wchar * 256),
                    ("strServiceShortName", ctypes.c_wchar * 64), ("ApplicationType", ctypes.c_int),
                    ("AppStatus", ctypes.c_ulong), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]


#: RM_APP_TYPE: how Restart Manager knows the program.
_KINDS = {0: "unknown", 1: "application", 2: "application", 3: "service", 4: "explorer", 5: "console",
          1000: "critical"}


if wintypes is not None:
    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def running():
    """{process id: executable name} of every process now (a snapshot of the process table; no
    administrator needed, and the name of a protected process too). {} where it cannot be read."""
    if not available():
        return {}
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot in (None, ctypes.c_void_p(-1).value, wintypes.HANDLE(-1).value):
            return {}
        found = {}
        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found[entry.th32ProcessID] = entry.szExeFile
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return found
    except Exception:
        return {}


def _ask(path):
    """Restart Manager's list of the programs holding `path`, as holders() gives it; may raise."""
    manager = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    manager.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p]
    manager.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    manager.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                                  ctypes.POINTER(_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD)]
    manager.RmEndSession.argtypes = [wintypes.DWORD]
    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(_SESSION_KEY)
    if manager.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        names = (ctypes.c_wchar_p * 1)(path)
        if manager.RmRegisterResources(session, 1, names, 0, None, 0, None) != 0:
            return []
        needed, have, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        found = []
        for _attempt in range(3):
            listed = (_PROCESS_INFO * max(have.value, 1))()
            have = wintypes.UINT(len(listed))
            code = manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(have), listed,
                                     ctypes.byref(reasons))
            if code == 0:
                found = [listed[i] for i in range(have.value)]
                break
            if code != ERROR_MORE_DATA:
                return []
            have = wintypes.UINT(needed.value + 4)
        return [(each.Process.dwProcessId, each.strServiceShortName, each.ApplicationType, each.strAppName)
                for each in found]
    finally:
        manager.RmEndSession(session)


def _holders(path):
    names = None
    found = []
    for pid, service, kind, app in _ask(path):
        if names is None:
            names = running()
        found.append({"name": names.get(pid) or app or "an unknown process", "pid": int(pid),
                      "kind": _KINDS.get(kind, "unknown"), "service": service or None})
    return found


def holders(path, timeout=TIMEOUT):
    """[{"name", "pid", "kind", "service"}]: the programs holding `path` open now, by Restart Manager.
    [] for none known, for a path that is not there, off Windows, when Restart Manager cannot be asked,
    and when it did not answer within `timeout` seconds. Never raises."""
    global _stuck
    if not available() or not path:
        return []
    with _guard:
        if _stuck >= MOST_STUCK:
            return []
    box = {}

    def look():
        global _stuck
        try:
            value = _holders(os.fspath(path))
        except Exception:
            value = []
        with _guard:
            box["value"] = value
            if box.get("counted"):
                _stuck -= 1

    thread = threading.Thread(target=look, name="lock-owners", daemon=True)
    thread.start()
    thread.join(timeout)
    with _guard:
        if "value" in box:
            return box["value"]
        box["counted"] = True
        _stuck += 1
    return []


def _called(holder):
    stem = holder["name"]
    low = stem.lower()
    low = low[:-4] if low.endswith(".exe") else low
    if low in TAGPUP:
        return "%s (%s)" % (holder["name"], "this TagPup process" if holder["pid"] == os.getpid() else TAGPUP[low])
    if low in KNOWN:
        return "%s (%s)" % (holder["name"], KNOWN[low])
    return holder["name"]


def describe(holders_found):
    """A short sentence for the owner, to follow "It is": 'held open by MsMpEng.exe (Windows Security / Microsoft
    Defender real-time scanning)'. '' for no holder known."""
    if not holders_found:
        return ""
    named, seen = [], set()
    for each in holders_found:
        text = _called(each)
        if text not in seen:
            seen.add(text)
            named.append(text)
    if named == ["an unknown process"] or not named:
        return "held open by an unknown process"
    return "held open by " + ", ".join(named)


def looks_held(error):
    """Is `error` the kind a file held open by another program makes (access denied, a sharing or lock violation,
    ExifTool's words for them)?"""
    if isinstance(error, OSError) and getattr(error, "winerror", None) in HELD:
        return True
    if isinstance(error, PermissionError):
        return True
    return bool(HELD_WORDS.search(str(error)))


def explain(error, path, timeout=TIMEOUT):
    """`error` as it is -- the same object -- unless it is the kind a held file makes and a holder is known: then
    the text of it and ' It is held open by <who>.' Called after a failure only; at most `timeout` seconds."""
    try:
        if not looks_held(error):
            return error
        said = describe(holders(path, timeout))
    except Exception:
        return error
    if not said:
        return error
    text = str(error).rstrip()
    return "%s%s It is %s." % (text, "" if text.endswith((".", "!")) else ".", said)

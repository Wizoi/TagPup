"""Which program holds a file open: named after a write fails, never before one is tried
(docs/ARCHITECTURE.md, "File access check").

A write that fails with "access is denied" or "used by another process" says nothing of WHO.
The answer is often not TagPup: Windows Defender scanning the file it was just given, the
Search indexer, a cloud-sync client, a backup, or another TagPup process. Windows' Restart
Manager (the API an installer asks "which programs must close to replace this file?") lists
them, with no administrator and without touching the file: RmStartSession,
RmRegisterResources, RmGetList, RmEndSession.

`holders(path)` is that list, `describe(holders)` a short sentence for the owner (each program once, with
every pid that holds the file), and `explain(error, path)` THE explanation of a failure: every operation that
can fail on a file another program holds open -- a write, a patch in place, a rename, a delete, a Convert --
asks it, and no site spells its own sentence of who (owner, 2026-11-13). `looks_held(error)` is the one test of
"is this a held file's error". `opened(path, sharing)` is the one open of a file that says what
others may do while it is open (CreateFileW): nothing (a patch in place), or anything, a
rename and a delete too (a piece of a video read while it plays). It is called after a failure, never on the way to a success, and
it is bounded: a lookup that is not answered within `timeout` seconds (a share that stopped
answering) is given up on, its thread left to end when Windows lets it, and is [] -- no
holder known, which is a fact about the lookup, not about the file. It never raises and
off Windows it is [].

Names only, as `MsMpEng.exe`: no path, no command line, no user.
"""
import ctypes
import logging
import os
import re
import sys
import threading

try:
    import ctypes.wintypes as wintypes
except (ImportError, ValueError):   # a platform without the Windows types: every lookup there is []
    wintypes = None

logger = logging.getLogger(__name__)

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

#: Windows' error numbers of a file another program has open: a sharing violation, a lock violation. An open, a rename
#: and a delete of a held file answer these (os.rename, os.remove, CreateFileW sharing nothing, SHFileOperation).
SHARING = (32, 33)
#: Access denied: what a move OVER a file another program has open answers (os.replace, the write's last step), and
#: what any change of a file marked read-only answers too. A held file's only where the site has seen the file there
#: and not read-only (looks_held's `writable`); an open's access denied is never a held file's (#2970).
DENIED = 5
#: What an error with no number (ExifTool's) says when another program holds the file.
HELD_WORDS = re.compile(r"used by another process|sharing violation|locked by another", re.IGNORECASE)
#: What an error with no number says for access denied: a held file's only as DENIED is.
DENIED_WORDS = re.compile(r"permission denied|access is denied|error renaming temporary file", re.IGNORECASE)
#: What follows a held file's failure when no program on this PC holds it and the file is on a network share: Restart
#: Manager sees this PC's handles alone, and names none for another computer's or the server's own (holders answers []).
NOT_ON_THIS_PC = "No program on this PC was found holding it: another computer, or the server itself, may have it open."

#: What others may do to a file while `opened` has it open: nothing (they cannot open it, and one that has it open
#: already makes the open fail, winerror 32), or read, write, rename and delete it (Python's open() shares read and write
#: but never delete, so a reader's handle refuses a rename of the file for as long as it is open).
NO_SHARING, ALL_SHARING = 0, 7

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


def opened(path, sharing, write=False):
    """The file at `path` open in binary, to read (to read and write when `write`), letting others share it as `sharing`
    says (NO_SHARING, ALL_SHARING). Raises the OSError of the open (FileNotFoundError, a sharing violation's winerror 32).
    Off Windows there is no sharing to ask for: open()."""
    mode = "r+b" if write else "rb"
    if os.name != "nt":
        return open(path, mode)
    import msvcrt

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE)
    generic_read, generic_write, existing, normal = 0x80000000, 0x40000000, 3, 0x80
    access = generic_read | (generic_write if write else 0)
    handle = create(path, access, sharing, None, existing, normal, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_BINARY
    return os.fdopen(msvcrt.open_osfhandle(handle, flags), mode)


def _called(holder):
    """(the program's name, what it is to the owner or None) of a holder."""
    name = holder["name"]
    low = name.lower()
    low = low[:-4] if low.endswith(".exe") else low
    if low in TAGPUP:
        return name, "this TagPup process" if holder.get("pid") == os.getpid() else TAGPUP[low]
    return name, KNOWN.get(low)


def describe(holders_found):
    """A short sentence for the owner, to follow "It is": each program once, with every pid that holds the file:
    'held open by MsMpEng.exe (Windows Security / Microsoft Defender real-time scanning, pid 4120)', 'held open by
    Sketchy.exe (pids 7, 8)', 'held open by an unknown process (pid 9)'. '' for no holder known."""
    pids = {}
    for each in holders_found:
        found = pids.setdefault(_called(each), [])
        if each.get("pid") is not None and each["pid"] not in found:
            found.append(each["pid"])
    named = []
    for (name, what), found in pids.items():
        ids = ("pid %d" % found[0] if len(found) == 1 else "pids " + ", ".join("%d" % pid for pid in found)
               ) if found else ""
        inside = ", ".join(part for part in (what, ids) if part)
        named.append("%s (%s)" % (name, inside) if inside else name)
    return "held open by " + ", ".join(named) if named else ""


def looks_held(error, writable=False):
    """Is `error` the kind a file another program holds open makes? The one test of it. A sharing or lock violation
    (SHARING), or an error with no number in those words (HELD_WORDS), always. Access denied (DENIED, a PermissionError
    with no number, DENIED_WORDS) only when `writable`: the site has seen the file there and not marked read-only, so its
    move over the file was refused by a holder (os.replace answers DENIED for a held file); an open's, a rename's or a
    delete's access denied is a read-only file's or a share's permission, never "open in another program" (#2970)."""
    number = getattr(error, "winerror", None)
    if number in SHARING:
        return True
    if number == DENIED:
        return writable
    if number is not None:
        return False
    text = str(error)
    if HELD_WORDS.search(text):
        return True
    return writable and (isinstance(error, PermissionError) or bool(DENIED_WORDS.search(text)))


def _on_a_share(path):
    from tagpup.files import shares     # here, not at the top: shares imports this module
    return bool(path) and shares.on_a_network_drive(os.fspath(path))


def _who(path, timeout):
    """'It is held open by <who>.' when Restart Manager names a holder of `path` on this PC; NOT_ON_THIS_PC when it names
    none and `path` is on a network share; '' when it names none on this PC's own disk (the program has let go, or
    Restart Manager failed: no sentence that cannot be true, #2971). Never raises."""
    try:
        who = describe(holders(path, timeout))
        if who:
            return "It is %s." % who
        return NOT_ON_THIS_PC if _on_a_share(path) else ""
    except Exception:
        return ""


def explain(error, path, said=None, writable=False, timeout=TIMEOUT):
    """THE explanation of an operation that failed with `error` on the file at `path` (owner, 2026-11-13: every site
    that can meet a file another program holds open asks this, and none spells its own sentence of who).

    `said` -- the site's own sentence of the failure; `error` itself, the same object, when None -- as it is when
    `error` is not the kind a held file makes (looks_held, `writable` as it says). Else it followed by who holds the
    file (_who): 'It is held open by <program> (<what it is>, pid N).', or NOT_ON_THIS_PC for a file on a share no
    program on this PC holds; nothing more when Restart Manager names none on this PC's own disk. Called after a failure
    only, never on the way to a success; at most `timeout` seconds; never raises. Logged as a warning, without the
    path (a folder or a file name can name a person, #2973)."""
    told = error if said is None else said
    try:
        held = looks_held(error, writable)
    except Exception:
        held = False
    if not held:
        return told
    more = _who(path, timeout)
    logger.warning("A file another program holds open (%s): %s", getattr(error, "winerror", None) or "no number",
                   more or "no program on this PC was found holding it")
    if not more:
        return told
    text = str(told).rstrip()
    return "%s%s %s" % (text, "" if text.endswith((".", "!")) else ".", more)

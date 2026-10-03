"""Sending a photo to the Windows Recycle Bin, where it can be restored from.

Only a photo FILE goes (problem): SHFileOperation with FOF_ALLOWUNDO takes a directory with
everything in it as readily (2026-10-02, the Just-look review, #520).

And only a file on a local fixed drive can be restored from the Bin. The Bin is per volume and
there is none on a network share, a mapped network drive or a removable drive: SHFileOperation
reports success for a file on one, and the file is gone for good. Checked 2026-10-02 on a UNC path
to local storage (\\\\localhost\\C$\\...): it reported success, the file was gone, and the Bin's
$Recycle.Bin held no entry for it, where the same file by its local path held one. So `goes_to_bin`
says it beforehand, for the page to tell the owner before it deletes, and the reply after.
"""
import ctypes
import os

from tagpup.core import paths
from tagpup.files import images

#: GetDriveType's answers.
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE = 2, 3, 4

#: Why a delete there is permanent, as a phrase after "it is" (the page and the reply say them).
NETWORK = "on a network share"
REMOVABLE = "on a removable drive"
SUBST = "on a substituted (SUBST) drive"
NO_BIN = "on a drive without a Recycle Bin"


def _long_path(path):
    """`path` with its 8.3 names spelled out (GetLongPathNameW); as given if Windows cannot say."""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetLongPathNameW(path, buffer, 32768):
            return buffer.value
    except (OSError, AttributeError):
        pass
    return path


def problem(file_path):
    """Why `file_path` may not be sent to the Recycle Bin, or None: it is no existing regular file
    (a folder, a path with a trailing separator, one that is not there) or no photo by its
    extension. The path is judged by its LONG name: A4413~1.JPG may be holiday.jpgold (#530)."""
    given = str(file_path or "")
    name = os.path.basename(given.rstrip("\\/")) or given
    if not given or given[-1] in "\\/" or not os.path.isfile(paths.stored(given)):
        return "%s is not a file; only a photo file is deleted" % name
    long = _long_path(paths.stored(given))
    if not os.path.isfile(long) or not images.is_photo(long):
        return "%s is not a photo; only a photo file is deleted" % os.path.basename(long)
    return None


def _without_prefix(path):
    """`path` without the \\\\?\\ (or \\\\.\\) prefix: \\\\?\\UNC\\server\\share is \\\\server\\share."""
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if path.startswith(prefix):
            rest = path[len(prefix):]
            return "\\\\" + rest[4:] if rest[:4].upper() == "UNC\\" else rest
    return path


def _real(path):
    """`path` with its links followed (a symlink or junction to a share leads there)."""
    return os.path.realpath(path)


def _mount_point(path):
    """The root of the volume `path` is on: "C:\\", or the folder a volume is mounted in."""
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetVolumePathNameW(path, buffer, 32768):
        raise OSError("no volume for %s" % path)
    return buffer.value


def _drive_type(mount):
    return ctypes.windll.kernel32.GetDriveTypeW(mount)


def _dos_device(drive):
    """What the drive letter `drive` ("Z:") stands for: \\Device\\HarddiskVolume3 for a disk,
    \\??\\C:\\dir for a SUBST drive; None if Windows cannot say."""
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.QueryDosDeviceW(drive, buffer, 32768):
        return None
    return buffer.value


def _reason_for(path):
    """Why a file at `path`, as spelled, would not go to the Bin, or None."""
    spelled = _without_prefix(paths.stored(path))
    if spelled.startswith("\\\\"):
        return NETWORK
    try:
        # A SUBST drive first: GetVolumePathName cannot say which volume one is on.
        drive = os.path.splitdrive(spelled)[0]
        device = _dos_device(drive) if drive else None
        if device and device.startswith("\\??\\"):
            return SUBST
        mount = _mount_point(spelled)
        kind = _drive_type(mount)
        if kind == DRIVE_REMOTE:
            return NETWORK
        if kind == DRIVE_REMOVABLE:
            return REMOVABLE
        if kind != DRIVE_FIXED:
            return NO_BIN
        if drive and mount.rstrip("\\/") == drive:
            # A bare drive letter must stand for a real disk volume.
            if device is None or not device.startswith("\\Device\\Harddisk"):
                return NO_BIN
    except (OSError, AttributeError, ValueError):
        return NO_BIN
    return None


def on_a_network_drive(path):
    """Is `path` on a mapped network drive (GetDriveType says remote)? A UNC path is the caller's to tell by its spelling.
    Cheap: a drive's type, no read of the path; False when Windows cannot say."""
    try:
        spelled = _without_prefix(paths.stored(path))
        if spelled.startswith("\\\\") or not os.path.splitdrive(spelled)[0]:
            return False
        return _drive_type(_mount_point(spelled)) == DRIVE_REMOTE
    except (OSError, AttributeError, ValueError):
        return False


def no_bin_reason(file_path):
    """Why a file at `file_path` would be deleted for good, not moved to the Recycle Bin -- NETWORK,
    REMOVABLE, SUBST or NO_BIN -- or None when it goes to the Bin. Judged by the path as spelled (a
    SUBST drive resolves to its folder only once its links are followed) and again by where its links
    lead (a junction or symlink to a share); a \\\\?\\ prefix is looked through; when unsure, NO_BIN.
    Reads nothing of the file."""
    reason = _reason_for(file_path)
    if reason:
        return reason
    try:
        real = _real(paths.stored(file_path))
    except OSError:
        return NO_BIN
    return _reason_for(real)


def goes_to_bin(file_path):
    """Can a file at `file_path` be restored from the Recycle Bin after it is sent there? Only one on a
    fixed local disk with a real volume (no_bin_reason)."""
    return no_bin_reason(file_path) is None


def send_to_recycle_bin(file_path):
    """Move the photo file to the Recycle Bin; False when Windows would not. Raises ValueError,
    moving nothing, for anything `problem` names. A file for which `no_bin_reason` names a reason is
    deleted permanently, and Windows says it succeeded."""
    from ctypes import wintypes

    # SHFileOperationW wants native separators, which is what stored() gives.
    why = problem(file_path)
    if why:
        raise ValueError(why)
    file_path = paths.stored(file_path)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004

    pFrom = file_path + "\0\0"

    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = pFrom
    fileop.pTo = None
    fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    fileop.fAnyOperationsAborted = False
    fileop.hNameMappings = None
    fileop.lpszProgressTitle = None

    SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
    SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    SHFileOperationW.restype = ctypes.c_int

    res = SHFileOperationW(ctypes.byref(fileop))
    return res == 0 and not fileop.fAnyOperationsAborted

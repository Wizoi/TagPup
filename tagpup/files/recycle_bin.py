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
import os

from tagpup.core import paths
from tagpup.files import images

#: GetDriveType's answer for a drive with a Recycle Bin of its own.
DRIVE_FIXED = 3


def problem(file_path):
    """Why `file_path` may not be sent to the Recycle Bin, or None: it is no existing regular file
    (a folder, a path with a trailing separator, one that is not there) or no photo by its
    extension (images.is_photo; the short 8.3 spelling of a .txt is a .txt)."""
    given = str(file_path or "")
    name = os.path.basename(given.rstrip("\\/")) or given
    if not given or given[-1] in "\\/" or not os.path.isfile(paths.stored(given)):
        return "%s is not a file; only a photo file is deleted" % name
    if not images.is_photo(given):
        return "%s is not a photo; only a photo file is deleted" % name
    return None


def goes_to_bin(file_path):
    """Can a file at `file_path` be restored from the Recycle Bin after it is sent there? No for a
    UNC path (\\\\server\\share, \\\\?\\UNC\\...) or a drive that is not a local fixed disk; yes
    otherwise. Reads nothing of the file."""
    spelled = paths.stored(file_path)
    if spelled.startswith("\\\\"):
        return False
    drive = os.path.splitdrive(spelled)[0]
    if not drive:
        return True
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_FIXED


def send_to_recycle_bin(file_path):
    """Move the photo file to the Recycle Bin; False when Windows would not. Raises ValueError,
    moving nothing, for anything `problem` names. A file for which `goes_to_bin` is False is
    deleted permanently, and Windows says it succeeded."""
    import ctypes
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

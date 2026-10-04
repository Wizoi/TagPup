"""Sending a photo to the Windows Recycle Bin, where it can be restored from.

Only a photo FILE goes (problem): SHFileOperation with FOF_ALLOWUNDO takes a directory with
everything in it as readily (2026-10-02, the Just-look review, #520).

And only a file on a local fixed drive can be restored from the Bin. The Bin is per volume and
there is none on a network share, a mapped network drive or a removable drive: SHFileOperation
reports success for a file on one, and the file is gone for good. Checked 2026-10-02 on a UNC path
to local storage (\\\\localhost\\C$\\...): it reported success, the file was gone, and the Bin's
$Recycle.Bin held no entry for it, where the same file by its local path held one. So `goes_to_bin`
says it beforehand, for the page to tell the owner before it deletes, and the reply after.

**Nothing is deleted for good** (#694, the owner's decision, 2026-10-04). A photo in a place with no Recycle Bin goes THROUGH THIS
PC (`delete_file`, the one way every delete of a photo takes: Organize's and the bulk Delete's): it is copied under
<Downloads>/TagPup deleted from shares/<server>/<share>/<path> (the Downloads known folder, wherever it was moved), the copy is
checked against the original (size, then a SHA-256 of each), the COPY is sent to this PC's Recycle Bin, and only then is the
original deleted at its place. Each step that fails leaves the original where it is: a copy that fails or differs is taken away; a
Bin that refuses the copy, the copy taken away; an original that cannot be deleted after its copy is in the Bin is still there,
and so the photo is there twice -- the error says so. Restored from the Bin, the copy goes back to the mirror folder under
Downloads, not to the share. Before copying, the drive of that folder must have room for the copy and SPARE_BYTES more.
"""
import ctypes
import hashlib
import os
import re
import shutil
import time
import uuid

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


#: {drive letter: (when asked, whether it is a mapped network drive)}: a drive's type does not change from one card to the
#: next, and asking Windows costs about 0.6 ms (GetVolumePathName), which 200 cards in a batch would pay 200 times.
_drive_kinds = {}
DRIVE_KIND_FRESH = 30.0


def on_a_network_drive(path):
    """Is `path` on a mapped network drive (GetDriveType says remote)? A UNC path is the caller's to tell by its spelling.
    A drive's type, no read of the path, remembered per drive letter for DRIVE_KIND_FRESH seconds; False when Windows
    cannot say."""
    try:
        spelled = _without_prefix(paths.stored(path))
        drive = os.path.splitdrive(spelled)[0]
        if spelled.startswith("\\\\") or not drive:
            return False
        key = drive.upper()
        now = time.monotonic()
        held = _drive_kinds.get(key)
        if held is not None and now - held[0] < DRIVE_KIND_FRESH:
            return held[1]
        remote = _drive_type(_mount_point(spelled)) == DRIVE_REMOTE
        _drive_kinds[key] = (now, remote)
        return remote
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


#: The folder under Downloads that copies of photos from places with no Recycle Bin are recycled from (#694).
MIRROR_FOLDER = "TagPup deleted from shares"

#: What must be left free on the drive of the Downloads folder after the copies: a full system disk is worse than a delete refused.
SPARE_BYTES = 1 << 30

#: Windows' FOLDERID_Downloads.
_DOWNLOADS_ID = "{374DE290-123F-4565-9164-39C4925E467B}"


def _known_downloads():
    """The Downloads known folder as Windows says it (SHGetKnownFolderPath): where the owner moved it, if they did. Raises
    OSError when Windows cannot say."""
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    raw = uuid.UUID(_DOWNLOADS_ID).bytes_le
    folder_id = GUID.from_buffer_copy(raw)
    found = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    result = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(found))
    try:
        if result != 0 or not found.value:
            raise OSError("SHGetKnownFolderPath answered %d" % result)
        return found.value
    finally:
        ole32.CoTaskMemFree(found)


def downloads_folder():
    """This PC's Downloads folder: TAGPUP_DOWNLOADS when set (a test's home sets it: no test writes the owner's Downloads), else
    the known folder (SHGetKnownFolderPath, FOLDERID_Downloads: it can have been moved to another drive), else the profile's
    Downloads when Windows cannot say."""
    given = os.environ.get("TAGPUP_DOWNLOADS")
    if given:
        return given
    try:
        return _known_downloads()
    except (OSError, AttributeError, ValueError):
        return os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Downloads")


def mirror_root():
    """Where copies of photos from places with no Recycle Bin are put to be recycled, and so where Windows restores them:
    <Downloads>\\TagPup deleted from shares."""
    return os.path.join(downloads_folder(), MIRROR_FOLDER)


def mirror_of(file_path, root=None):
    """Where the copy of `file_path` goes under `root` (mirror_root): \\\\server\\share\\a\\b.jpg at root\\server\\share\\a\\b.jpg,
    E:\\a\\b.jpg at root\\E\\a\\b.jpg; a \\\\?\\ prefix looked through."""
    spelled = _without_prefix(paths.stored(file_path))
    if spelled.startswith("\\\\"):
        rest = spelled[2:]
    else:
        drive, tail = os.path.splitdrive(spelled)
        rest = drive.rstrip(":") + "\\" + tail
    parts = [part for part in re.split(r"[\\/]+", rest) if part]
    return os.path.join(root or mirror_root(), *parts)


def _existing(folder):
    """`folder`, or the nearest folder above it that is there."""
    while folder and not os.path.isdir(folder):
        above = os.path.dirname(folder)
        if above == folder:
            break
        folder = above
    return folder


def room_for(byte_count, root=None):
    """None when the drive of the copies' folder has room for `byte_count` more and SPARE_BYTES to spare; else the sentence."""
    root = root or mirror_root()
    try:
        free = shutil.disk_usage(_existing(root)).free
    except OSError as e:
        return "Could not tell how much room there is on this PC for the copies (%s). Nothing was deleted." % (e.strerror or e)
    if free >= byte_count + SPARE_BYTES:
        return None
    mb = 1024 * 1024
    return ("There is not room on this PC for the copies: %s MB needed in %s and %s MB free on its drive, keeping 1 GB spare. "
            "Nothing was deleted." % ("{:,}".format(-(-byte_count // mb)), root, "{:,}".format(free // mb)))


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _free_name(target):
    """`target`, or `name (2).jpg` and so on when a copy of that name is in the mirror already (an earlier one, restored)."""
    stem, ext = os.path.splitext(target)
    n = 1
    while os.path.exists(target) or os.path.exists(target + ".partial"):
        n += 1
        target = "%s (%d)%s" % (stem, n, ext)
    return target


def _take_away(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def recycle_through_this_pc(file_path, root=None):
    """Delete `file_path`, in a place with no Recycle Bin, through this PC (the module's docstring): copy, check, the copy to this
    PC's Recycle Bin, then the original. Returns where the copy was put (so where Windows restores it). Raises ValueError for a
    path that is no photo file (problem) and OSError, with a sentence, for any step that failed: the original is then left where it
    is (and, when only its own delete failed, is there twice: the error says so)."""
    why = problem(file_path)
    if why:
        raise ValueError(why)
    source = paths.stored(file_path)
    root = root or mirror_root()
    size = os.path.getsize(source)
    full = room_for(size, root)
    if full:
        raise OSError(full)
    target = _free_name(mirror_of(source, root))
    partial = target + ".partial"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if no_bin_reason(target):
            raise OSError("%s has no Recycle Bin either" % os.path.dirname(target))
        shutil.copy2(source, partial)
        if os.path.getsize(partial) != size or _hash(partial) != _hash(source):
            raise OSError("the copy did not match the file")
        os.replace(partial, target)
    except OSError as e:
        _take_away(partial)
        _take_away(target)
        raise OSError("Could not copy it to this PC to recycle it there (%s): the original was left where it is."
                      % (e.strerror or e)) from None
    try:
        moved = send_to_recycle_bin(target)
    except Exception:
        moved = False
    if not moved:
        _take_away(target)
        raise OSError("This PC's Recycle Bin would not take the copy: the original was left where it is.")
    try:
        os.remove(source)
    except OSError as e:
        raise OSError("A copy is in this PC's Recycle Bin, but the original could not be deleted (%s): it is still where it was, so "
                      "the photo is there twice." % (e.strerror or e)) from None
    return target


def delete_file(file_path):
    """Delete a photo FILE the one way every delete of a photo takes (#694): to its own Recycle Bin, or -- where its place has none
    -- through this PC (recycle_through_this_pc). {"through_this_pc": bool, "reason": why its place has no Bin or None, "copy":
    where the copy was put, or None}. Raises ValueError (problem) or OSError with a sentence; the original is then still there."""
    reason = no_bin_reason(file_path)
    if reason:
        return {"through_this_pc": True, "reason": reason, "copy": recycle_through_this_pc(file_path)}
    if not send_to_recycle_bin(file_path):
        raise OSError("Failed to move file to Recycle Bin")
    return {"through_this_pc": False, "reason": None, "copy": None}


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

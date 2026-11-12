"""Sending a photo or a clip to the Windows Recycle Bin, where it can be restored from.

Only a photo or clip FILE goes (problem): SHFileOperation with FOF_ALLOWUNDO takes a directory with
everything in it as readily (2026-10-02, the Just-look review, #520). A clip goes as a photo does, up to
MOST_CLIP_BYTES; a larger one is deleted for good, on any drive, only once the owner was told so and confirmed it, never copied
through this PC (too_large_to_copy; DECISIONS 2026-11-02 item 7 and 2026-11-08).

And only a file on a local fixed drive can be restored from the Bin. The Bin is per volume and
there is none on a network share, a mapped network drive or a removable drive: SHFileOperation
reports success for a file on one, and the file is gone for good. Checked 2026-10-02 on a UNC path
to local storage (\\\\localhost\\C$\\...): it reported success, the file was gone, and the Bin's
$Recycle.Bin held no entry for it, where the same file by its local path held one. So `goes_to_bin`
says it beforehand, for the page to tell the owner before it deletes, and the reply after. Nor does a Bin keep a file
larger than its capacity (its per-volume MaxCapacity), or any file when it is set to NukeOnDelete: asked without a
question (FOF_NOCONFIRMATION), Windows deletes such a file for good and reports success. So a file on a drive with a Bin
is first weighed against what that Bin keeps (`keeps_up_to`): one it would not keep is deleted for good only when the
owner was told so and confirmed it (`delete_file`'s `for_good`; DECISIONS 2026-11-08), else refused untouched (NotKept),
and the reply says it was deleted for good, never that it went to the Bin.

**Nothing is deleted for good** (#694, the owner's decision, 2026-10-04). A photo or clip in a place with no Recycle Bin goes
THROUGH THIS PC (`delete_file`, the one way every delete of one takes: Organize's and the bulk Delete's): it is copied under
<Downloads>/TagPup deleted from shares/<server>/<share>/<path> (the Downloads known folder, wherever it was moved), the copy is
checked against the original (size, then a SHA-256 of each), the COPY is sent to this PC's Recycle Bin, and only then is the
original deleted at its place. Each step that fails leaves the original where it is: a copy that fails or differs is taken away; a
Bin that refuses the copy, the copy taken away; an original that cannot be deleted after its copy is in the Bin is still there,
and so the file is there twice -- the error says so. Restored from the Bin, the copy goes back to the mirror folder under
Downloads, not to the share. Before copying, the drive of that folder must have room for the copy and SPARE_BYTES more.

And a copy is made only where it would be KEPT (the owner's review, #703-#706; `can_copy_here`): Windows makes room in a full
Recycle Bin by purging its OLDEST items for good, so the Bin of the Downloads volume must hold what it holds, the copies, and
BIN_MARGIN of its capacity (its per-volume MaxCapacity; a Bin set to NukeOnDelete keeps nothing, and a file larger than the Bin
is not kept either). A Bin that would not keep the copies -- full, set to delete at once, or smaller than one -- is
`copies_not_kept`: the file is then deleted for good where it is only when the owner was told so and confirmed it (the one
confirm path of `for_good`, NotKept with `full` when the Bin is full: the page asks "The Recycle Bin is full: delete
permanently instead?"; owner, 2026-11-12), else refused untouched; no copy is made. The Bin's size is asked of Windows (SHQueryRecycleBinW: 11.8 s for 3,353 items here) once, then counted
on by what this process sends (`note_binned`), and asked again after BIN_FRESH. A copy whose path would be MAX_PATH or more is
refused up front (SHFileOperation takes none). A Downloads folder under OneDrive is refused: a copy would be synced to the cloud
before it was recycled. A `.partial` a crash left for the same name is taken away on the way in.

A file is UNDER DELETE (`being_deleted`) from the first look `delete_file` takes at it to its last step, however that ends: a
copy through this PC can take minutes, and a read of the original as it is removed makes the remove fail, the copy in the Bin
and the file there twice. The server's reader of a file a range at a time (/api/video-file, tagpup.services.photos.video)
refuses one meanwhile; the page cannot know which files a delete holds (a bulk Delete of a Select all names a source).
"""
import contextlib
import ctypes
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
import winreg

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


#: The largest clip TagPup sends to a Recycle Bin (DECISIONS 2026-11-08, A5): one up to 2 GiB goes as a photo does -- from a
#: share, a copy of the whole file through this PC, the share read twice to check it --; a larger one is deleted for good once
#: confirmed, on any drive, never copied (too_large_to_copy; DECISIONS 2026-11-02 item 7: a 10 GB copy over the share is the
#: reason). 2,168 of photo_index's 2,179 clips are within it, 11 over, the largest 9,635 MB (counted 2026-11-08).
MOST_CLIP_BYTES = 2 << 30


def too_large_to_copy(file_path, size):
    """Is `file_path` a clip (images.is_clip) of more than MOST_CLIP_BYTES at `size` bytes: one deleted for good once
    confirmed, never sent to a Bin nor copied through this PC. The one rule in Python; store/selection_folders is its SQL
    (`clips_over`, and what bytes_by_folder leaves out of the copies)."""
    return images.is_clip(file_path) and size > MOST_CLIP_BYTES


def _judged(file_path):
    """(why `file_path` is no photo or clip file, or None; its LONG name: A4413~1.JPG may be holiday.jpgold, #530)."""
    given = str(file_path or "")
    name = os.path.basename(given.rstrip("\\/")) or given
    if not given or given[-1] in "\\/" or not os.path.isfile(paths.stored(given)):
        return "%s is not a file; only a photo or video file is deleted" % name, None
    long = _long_path(paths.stored(given))
    if not os.path.isfile(long) or not images.is_media(long):
        return "%s is not a photo or video; only a photo or video file is deleted" % os.path.basename(long), long
    return None, long


def problem(file_path):
    """Why `file_path` may not be deleted, or None: no existing regular file (a folder, a path with a trailing separator,
    one that is not there), no photo or clip by its extension (images.is_media). The path is judged by its LONG name
    (_judged)."""
    return _judged(file_path)[0]


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


#: The Recycle Bin (SHFileOperation) takes no path of this many characters or more.
MAX_PATH = 260

#: What is kept free of the Recycle Bin's capacity: a Bin filled to the brim purges its oldest items for the next file.
BIN_MARGIN = 0.05

#: How long the Bin's size, as Windows last said it plus what this process sent since, is trusted before it is asked again.
BIN_FRESH = 600.0

#: The Bin's per-volume settings, under HKEY_CURRENT_USER.
_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"

_bin_lock = threading.Lock()
_bin_seen = {}      # {volume key: [when asked (monotonic), bytes Windows said, bytes this process sent since]}


def forget_bin():
    """Forget what was asked of the Bins (a test; nothing else needs to)."""
    with _bin_lock:
        _bin_seen.clear()


def _volume_root(path):
    """The volume the folder `path` is (or would be) on: "C:\\"."""
    return _mount_point(_existing(path) or path)


def _volume_guid(mount):
    buffer = ctypes.create_unicode_buffer(300)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(mount, buffer, 300):
        raise OSError("no volume name for %s" % mount)
    found = re.search(r"\{[0-9A-Fa-f-]+\}", buffer.value)
    if not found:
        raise OSError("no volume GUID for %s" % mount)
    return found.group(0)


def _registry_value(key_path, name):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        return winreg.QueryValueEx(key, name)[0]


def _test_bin():
    """TAGPUP_RECYCLE_BIN, "capacity MB,used MB,nuke": a test home's Bin (tests/own_home.py), or None."""
    given = os.environ.get("TAGPUP_RECYCLE_BIN")
    if not given:
        return None
    capacity, used, nuke = (int(part) for part in given.split(","))
    return capacity << 20, used << 20, bool(nuke)


def _bin_settings(mount):
    """(the Bin's capacity in bytes, whether it is set to delete at once) of the volume `mount`, from its BitBucket key. A volume
    with no key has Windows' default, taken as 5 % of the volume (a guess on the safe side)."""
    test = _test_bin()
    if test is not None:
        return test[0], test[2]
    key = _BITBUCKET + "\\" + _volume_guid(mount)
    try:
        capacity = int(_registry_value(key, "MaxCapacity")) << 20
    except OSError:
        capacity = shutil.disk_usage(mount).total // 20
    try:
        nuke = bool(_registry_value(key, "NukeOnDelete"))
    except OSError:
        nuke = False
    return capacity, nuke


def _query_bin(mount):
    """What the Bin of `mount` holds now, in bytes (SHQueryRecycleBinW: seconds on a Bin of thousands of items)."""
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    result = ctypes.windll.shell32.SHQueryRecycleBinW(mount, ctypes.byref(info))
    if result != 0:
        raise OSError("SHQueryRecycleBinW answered %d" % result)
    return int(info.i64Size)


def _bin_size(mount):
    test = _test_bin()
    return test[1] if test is not None else _query_bin(mount)


def _bin_used(mount, fresh=False):
    """The Bin's size: as Windows last said it plus what this process has sent since; asked again when `fresh` or older than
    BIN_FRESH."""
    key = paths.key(mount)
    with _bin_lock:
        seen = _bin_seen.get(key)
        if seen is not None and not fresh and time.monotonic() - seen[0] < BIN_FRESH:
            return seen[1] + seen[2]
    size = _bin_size(mount)
    with _bin_lock:
        _bin_seen[key] = [time.monotonic(), size, 0]
    return size


def note_binned(folder, byte_count):
    """`byte_count` more went to the Bin of the volume of `folder` from this process: counted until the Bin is asked again."""
    try:
        key = paths.key(_volume_root(folder))
    except (OSError, AttributeError, ValueError):
        return
    with _bin_lock:
        if key in _bin_seen:
            _bin_seen[key][2] += byte_count


def _mb(byte_count):
    return "{:,} MB".format(-(-int(byte_count) // (1 << 20)))


def keeps_up_to(path):
    """The largest file, in bytes, the Recycle Bin of the volume `path` is on (where its links lead) keeps: its capacity
    (_bin_settings), 0 when it is set to delete at once. Raises OSError when Windows cannot say."""
    return _keeps(_bin_mount(path))


def keeps_up_to_by_folder(folders):
    """{folder of `folders`: keeps_up_to of it, or None when Windows cannot say}, each volume's Bin asked once: the delete
    question's look at a selection's folders (a Select all of photo_index is about 2,600 folders on two drives). Asked afresh on
    every call: a Bin set smaller between the question and the start is seen by the start."""
    by_mount, found = {}, {}
    for folder in folders:
        try:
            mount = _bin_mount(folder)
            key = paths.key(mount)
            if key not in by_mount:
                by_mount[key] = _keeps(mount)
            found[folder] = by_mount[key]
        except OSError:
            found[folder] = None
    return found


def _bin_mount(path):
    """The volume whose Recycle Bin a file at `path` would go to (where its links lead). Raises OSError."""
    try:
        return _mount_point(_real(paths.stored(path)))
    except (AttributeError, ValueError) as e:
        raise OSError(str(e)) from None


def _keeps(mount):
    """keeps_up_to of the volume `mount`."""
    try:
        capacity, nuke = _bin_settings(mount)
    except (AttributeError, ValueError) as e:
        raise OSError(str(e)) from None
    return 0 if nuke else capacity


def would_keep(size, kept):
    """Would a Bin that keeps files of up to `kept` bytes (keeps_up_to) keep a file of `size`: never one set to delete at once
    (`kept` 0), an empty file not either. The one rule; store/selection_folders.larger_than is its SQL."""
    return kept > 0 and size <= kept


class NotKept(OSError):
    """A file the Recycle Bin of its drive would not keep (keeps_up_to) -- or, from a place with no Bin, this PC's Bin would not
    keep its copy (copies_not_kept) --, asked to be deleted without `for_good`: nothing was touched. The page names it and asks;
    confirmed, it is deleted for good. `full`: this PC's Bin is only full (BIN_FULL), so the page asks in those words."""

    def __init__(self, sentence, full=False):
        super().__init__(sentence)
        self.full = full


def not_kept(file_path, kept):
    """The sentence of NotKept for `file_path`, whose drive's Bin keeps files of up to `kept` bytes."""
    why = ("the Recycle Bin of its drive is set to delete files at once" if kept <= 0
           else "it is larger than the Recycle Bin of its drive keeps (%s)" % _mb(kept))
    return _not_kept_said(file_path, why)


def clip_not_kept(file_path, size):
    """The sentence of NotKept for the clip `file_path` of `size` bytes, over MOST_CLIP_BYTES (too_large_to_copy)."""
    return _not_kept_said(file_path, "it is a video of %s, larger than the %s TagPup copies to a Recycle Bin"
                          % (_mb(size), _mb(MOST_CLIP_BYTES)))


def _not_kept_said(file_path, why):
    name = os.path.basename(paths.stored(file_path))
    return "%s would be deleted for good, not moved to the Recycle Bin: %s. Nothing was deleted." % (name, why)


def _remove_for_good(source):
    """Remove `source`, confirmed for good: an OSError with a sentence when it cannot be (a player holding it open, a share
    that refused), the file then still where it was."""
    try:
        os.remove(source)
    except OSError as e:
        raise OSError("It could not be deleted (%s): it is still where it was, and nothing was deleted."
                      % (e.strerror or e)) from None


#: What copies_not_kept says of this PC's Recycle Bin: full (Windows would make room by purging its oldest items for good), or
#: keeping none of the copies (set to delete at once, or smaller than one of them).
BIN_FULL, BIN_KEEPS_NONE = "full", "keeps none"


def copies_not_kept(byte_count, largest, root=None, fresh=False):
    """None when the Bin of the copies' volume will keep `byte_count` more of copies (the largest `largest`) and what it holds,
    with BIN_MARGIN of its capacity spare; else (BIN_FULL or BIN_KEEPS_NONE, the phrase of why). Raises OSError (AttributeError,
    ValueError) when Windows cannot say. `fresh`: ask Windows the Bin's size now (the question before a delete), else trust what
    was asked within BIN_FRESH and what was sent since."""
    root = root or mirror_root()
    mount = _volume_root(root)
    capacity, nuke = _bin_settings(mount)
    used = _bin_used(mount, fresh)
    if nuke:
        return BIN_KEEPS_NONE, "this PC's Recycle Bin on %s deletes files at once (it is set not to keep them)" % mount
    if largest > capacity:
        return BIN_KEEPS_NONE, ("a file of %s is larger than this PC's Recycle Bin on %s (%s), which could not keep it"
                                % (_mb(largest), mount, _mb(capacity)))
    if used + byte_count > capacity * (1 - BIN_MARGIN):
        return BIN_FULL, ("this PC's Recycle Bin on %s is full: it holds %s of %s, and %s %s; Windows would make room by "
                          "deleting its oldest items for good"
                          % (mount, _mb(used), _mb(capacity), "the copy is" if byte_count == largest else "the copies are",
                             _mb(byte_count)))
    return None


def cannot_tell(e):
    """The sentence when Windows could not say what this PC's Bin keeps (copies_not_kept raised `e`)."""
    return "Could not tell how much this PC's Recycle Bin can keep (%s). Nothing was deleted." % (getattr(e, "strerror", None) or e)


def room_for_copies(byte_count, largest, root=None, fresh=False):
    """None when the Bin of the copies' volume will keep them (copies_not_kept); else the sentence that refuses them."""
    try:
        refused = copies_not_kept(byte_count, largest, root, fresh)
    except (OSError, AttributeError, ValueError) as e:
        return cannot_tell(e)
    if refused is None:
        return None
    kind, why = refused
    advice = "Empty the Recycle Bin, or delete fewer photos. " if kind == BIN_FULL else ""
    return "%s%s. %sNothing was deleted." % (why[0].upper(), why[1:], advice)


def _cloud_synced(folder):
    """The OneDrive folder `folder` is under (Known Folder Move puts Downloads there), or None."""
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        top = os.environ.get(name)
        if top and (paths.key(folder) + "\\").startswith(paths.key(top).rstrip("\\") + "\\"):
            return top
    return None


def cannot_make_copies(byte_count, root=None):
    """None when copies of `byte_count` can be made under the mirror folder; else the sentence: Downloads synced to OneDrive, or
    no room on its drive (#703, #706). Whether this PC's Bin keeps them is copies_not_kept's."""
    root = root or mirror_root()
    synced = _cloud_synced(root)
    if synced:
        return ("The Downloads folder is under OneDrive (%s): a copy of a deleted photo would be synced to the cloud before it was "
                "recycled. Nothing was deleted." % synced)
    return room_for(byte_count, root)


def can_copy_here(byte_count, largest, root=None, fresh=False):
    """None when copies of `byte_count` (the largest `largest`) can be made under the mirror folder and kept by this PC's Recycle
    Bin; else the sentence: cannot_make_copies, or a Bin that would not keep them (room_for_copies)."""
    return cannot_make_copies(byte_count, root) or room_for_copies(byte_count, largest, root, fresh)


def too_long(target):
    """The sentence when the copy at `target` (or its `.partial`) would be MAX_PATH characters or more, else None (#704)."""
    longest = len(target) + len(".partial")
    if longest < MAX_PATH:
        return None
    return ("Its copy's path would be %d characters under %s, and the Recycle Bin takes none of %d or more: it was left where it "
            "is." % (len(target), os.path.dirname(target), MAX_PATH))


def _clear_partials(target):
    """Take away `.partial` copies of `target`'s name -- `name.jpg.partial`, `name (2).jpg.partial` -- that a crash left (#706)."""
    folder = os.path.dirname(target)
    stem, ext = os.path.splitext(os.path.basename(target))
    pattern = re.compile(r"^%s(?: \(\d+\))?%s\.partial$" % (re.escape(stem), re.escape(ext)), re.IGNORECASE)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if pattern.match(name):
            _take_away(os.path.join(folder, name))


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
    while os.path.exists(target):
        n += 1
        target = "%s (%d)%s" % (stem, n, ext)
    return target


def _take_away(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def recycle_through_this_pc(file_path, root=None, for_good=False):
    """Delete `file_path`, in a place with no Recycle Bin, through this PC (the module's docstring): copy, check, the copy to this
    PC's Recycle Bin, then the original. Returns (where the copy was put -- so where Windows restores it --, None), or, when this
    PC's Bin would not keep the copy (copies_not_kept) and `for_good` (the owner was told and confirmed), (None, why it was deleted
    for good): it is then removed where it is, no copy made; unconfirmed, NotKept, nothing touched. Raises ValueError for a
    path that is no photo or clip file (problem) and OSError, with a sentence, for any step that failed: the original is then left where it
    is (and, when only its own delete failed, is there twice: the error says so)."""
    why, long = _judged(file_path)
    if why:
        raise ValueError(why)
    source = paths.stored(file_path)
    root = root or mirror_root()
    size = os.path.getsize(source)
    refused = cannot_make_copies(size, root)
    if refused:
        raise OSError(refused)
    try:
        not_kept_here = copies_not_kept(size, size, root)
    except (OSError, AttributeError, ValueError) as e:
        raise OSError(cannot_tell(e)) from None
    if not_kept_here:
        kind, why = not_kept_here
        if not for_good:
            raise NotKept(_not_kept_said(source, why), full=kind == BIN_FULL)
        _remove_for_good(source)
        return None, BIN_FULL_WHY if kind == BIN_FULL else COPY_NOT_KEPT
    _clear_partials(mirror_of(source, root))
    target = _free_name(mirror_of(source, root))
    long = too_long(target)
    if long:
        raise OSError(long)
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
    note_binned(root, size)
    try:
        os.remove(source)
    except OSError as e:
        raise OSError("A copy is in this PC's Recycle Bin, but the original could not be deleted (%s): it is still where it was, so "
                      "the file is there twice." % (e.strerror or e)) from None
    return target, None


_under_delete_lock = threading.Lock()
_under_delete = {}      # {paths.key of a file: how many deletes of it are under way (two pages may delete one file at once)}


@contextlib.contextmanager
def _deleting(file_path):
    """`file_path` is under delete (being_deleted) for the length of the block, and released however the block ends."""
    key = paths.key(file_path)
    with _under_delete_lock:
        _under_delete[key] = _under_delete.get(key, 0) + 1
    try:
        yield
    finally:
        with _under_delete_lock:
            _under_delete[key] -= 1
            if not _under_delete[key]:
                del _under_delete[key]


def being_deleted(file_path):
    """Is a delete of `file_path` under way in this process (delete_file, from its first look to its last step)? Only the server
    deletes a photo or clip, so this process is the one that knows."""
    key = paths.key(file_path)
    with _under_delete_lock:
        return key in _under_delete


def delete_file(file_path, for_good=False):
    """Delete a photo or clip FILE the one way every delete of one takes (#694): to its own Recycle Bin, or -- where its place has none
    -- through this PC (recycle_through_this_pc). {"through_this_pc": bool, "reason": why its place has no Bin or None, "copy":
    where the copy was put, or None, "for_good": bool, "why_for_good": the phrase of why it was deleted for good, or None}.
    Raises ValueError (problem) or OSError with a sentence; the original is then still there. Deleted for good only with
    `for_good` (the owner was told and confirmed), else NotKept, nothing touched: a clip over MOST_CLIP_BYTES on any drive
    (too_large_to_copy: removed where it is, no copy made, no Bin asked), and a file its own drive's Bin would not keep
    (keeps_up_to). The file is under delete (being_deleted) until this returns or raises: the panel's delete and each of a bulk
    Delete's alike."""
    with _deleting(file_path):
        return _delete_file(file_path, for_good)


#: Why a file was deleted for good, as the reply says it after "Deleted for good, as you confirmed: ".
BIN_WOULD_NOT_KEEP = "the Recycle Bin of its drive would not keep it"
CLIP_NOT_COPIED = "a video this large is not copied to a Recycle Bin"
BIN_FULL_WHY = ("this PC's Recycle Bin, which keeps the copies of files from places with no Recycle Bin, is full")
COPY_NOT_KEPT = ("this PC's Recycle Bin, which keeps the copies of files from places with no Recycle Bin, would not keep its copy")


def _delete_file(file_path, for_good):
    why, long = _judged(file_path)
    if why:
        raise ValueError(why)
    source = paths.stored(file_path)
    if images.is_clip(long):
        # A clip over MOST_CLIP_BYTES is weighed before its place: on a share it would be a copy of gigabytes through this PC.
        try:
            size = os.path.getsize(source)
        except OSError as e:
            raise OSError("Could not tell how large the video is (%s). Nothing was deleted." % (e.strerror or e)) from None
        if too_large_to_copy(long, size):
            if not for_good:
                raise NotKept(clip_not_kept(source, size))
            _remove_for_good(source)
            return {"through_this_pc": False, "reason": None, "copy": None, "for_good": True, "why_for_good": CLIP_NOT_COPIED}
    reason = no_bin_reason(file_path)
    if reason:
        copy, why_for_good = recycle_through_this_pc(file_path, for_good=for_good)
        return {"through_this_pc": copy is not None, "reason": reason, "copy": copy, "for_good": copy is None,
                "why_for_good": why_for_good}
    try:
        size, kept = os.path.getsize(source), keeps_up_to(source)
    except OSError as e:
        raise OSError("Could not tell whether the Recycle Bin of its drive can keep it (%s). Nothing was deleted."
                      % (e.strerror or e)) from None
    if not would_keep(size, kept):
        if not for_good:
            raise NotKept(not_kept(source, kept))
        _remove_for_good(source)
        return {"through_this_pc": False, "reason": None, "copy": None, "for_good": True, "why_for_good": BIN_WOULD_NOT_KEEP}
    if not send_to_recycle_bin(file_path):
        raise OSError("Failed to move file to Recycle Bin")
    return {"through_this_pc": False, "reason": None, "copy": None, "for_good": False, "why_for_good": None}


def goes_to_bin(file_path):
    """Can a file at `file_path` be restored from the Recycle Bin after it is sent there? Only one on a
    fixed local disk with a real volume (no_bin_reason)."""
    return no_bin_reason(file_path) is None


def send_to_recycle_bin(file_path):
    """Move the photo or clip file to the Recycle Bin; False when Windows would not. Raises ValueError,
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

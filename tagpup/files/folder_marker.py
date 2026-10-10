"""The `.tagpup` file in a folder: the marker a folder carries so a library follows it by name-free
evidence when it is renamed or moved (docs/ARCHITECTURE.md, "Folder ids").

A marker is a list of entries, one line each: a library's identifier and that library's id for
the folder, two lowercase UUIDs and a space, and nothing else -- no name, path, date or anything
that names a person. A folder in several libraries holds one entry for each. This module reads
and writes that file and nothing more; which folders to mark, and what an id means, are the
service's (tagpup.services.folder_ids).

- **What a file is.** `read` answers one of ABSENT, OK (with its entries), MALFORMED (a file
  of that name that does not parse as that list: not a marker, never rewritten, reported) or
  UNREADABLE (the folder or file could not be read: locked, a share gone). An empty file, a
  byte-order mark, a blank line, a line of another shape, a library twice, or more than
  MAX_BYTES, is MALFORMED: a hand-edited file is the owner's.
- **Writing is a replacement, never an edit in place.** `stage` writes the whole new content to
  a temporary name beside the file (`.tagpup.<random>.tmp`, hidden on Windows as it is written),
  and `publish` renames it over the marker, so an interrupted run leaves the old file or the
  new, and another reader never sees half of one. The new content is the old bytes, every
  entry kept byte for byte, and ours appended (`with_entry`). Nothing makes a file writable: a
  marker with the read-only attribute is left (the rename is refused, Windows' Access is
  denied) and counted by the caller.
- **Finding.** `read_in` reads the markers of the folders it is given and nothing else; `subfolders` lists the
  folders directly in one (one listing); `find` walks the folders it is given, listing each once, and reads the
  markers it meets for one library's entries. A junction or link is not walked into
  (tagpup.files.images.walks_into). They decide nothing; they report what they saw.
"""
import ctypes
import os
import re
import stat
import time
import uuid

from tagpup.core import paths
from tagpup.files import images

#: The marker's file name. No other TagPup file is called that.
NAME = ".tagpup"

#: A marker larger than this is not read: it is not a list of entries.
MAX_BYTES = 64 * 1024

ABSENT, OK, MALFORMED, UNREADABLE = "absent", "ok", "malformed", "unreadable"

_UUID = rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ENTRY = re.compile(rb"^(" + _UUID + rb") (" + _UUID + rb")$")

#: The temporary files a write makes; one older than STALE_SECONDS is a crashed run's.
_TEMP = re.compile(r"^\.tagpup\.[0-9a-f]{32}\.tmp$")
STALE_SECONDS = 3600

FILE_ATTRIBUTE_HIDDEN = 0x2


class Marker:
    """What a folder's `.tagpup` is: `state`, its `entries` [(library id, folder id)] when OK,
    its `data` (the bytes read; None when absent), whether it can be replaced (`writable`: it
    does not have the read-only attribute) and `error` for an UNREADABLE one."""

    def __init__(self, state, entries=(), data=None, writable=True, error=None):
        self.state = state
        self.entries = list(entries)
        self.data = data
        self.writable = writable
        self.error = error

    def entry_of(self, library_id):
        """The folder id this marker holds for `library_id`, or None."""
        for library, folder_id in self.entries:
            if library == library_id:
                return folder_id
        return None


def new_id():
    """A fresh id: a random UUID, lowercase."""
    return str(uuid.uuid4())


def parse(data):
    """The entries [(library id, folder id)] of a marker's bytes, or None when they are not a
    list of entries (see the module). A library twice is not a list either: which id is its
    own cannot be said."""
    if not data or len(data) > MAX_BYTES:
        return None
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    entries, seen = [], set()
    for line in lines:
        found = _ENTRY.match(line[:-1] if line.endswith(b"\r") else line)
        if not found:
            return None
        library, folder_id = found.group(1).decode(), found.group(2).decode()
        if library in seen:
            return None
        seen.add(library)
        entries.append((library, folder_id))
    return entries


def with_entry(data, library_id, folder_id):
    """The marker's new bytes: `data` (None for no file) with the entry for `library_id` added,
    every byte of the others kept."""
    line = ("%s %s\n" % (library_id, folder_id)).encode("ascii")
    if not data:
        return line
    return data + (b"" if data.endswith(b"\n") else b"\n") + line


def location(folder):
    return os.path.join(paths.stored(folder), NAME)


def read(folder):
    """The Marker `folder` holds. Never raises: a read that fails is UNREADABLE, which a caller
    counts and does not decide on."""
    target = location(folder)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return Marker(ABSENT)
    except NotADirectoryError:
        return Marker(UNREADABLE, error="the folder is not a folder")
    except OSError as e:
        return Marker(UNREADABLE, error="%s: %s" % (type(e).__name__, e))
    writable = bool(info.st_mode & stat.S_IWRITE)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        return Marker(MALFORMED, writable=writable)
    try:
        with open(target, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as e:
        return Marker(UNREADABLE, writable=writable, error="%s: %s" % (type(e).__name__, e))
    entries = parse(data)
    if entries is None:
        return Marker(MALFORMED, data=data, writable=writable)
    return Marker(OK, entries, data, writable)


def can_write_into(folder):
    """Could a new file be made in `folder`? As far as the system says without trying (the
    read-only attribute of a folder means nothing on Windows; a share's own permission only
    shows when a write is made, which counts the refusal)."""
    return os.access(paths.stored(folder), os.W_OK)


def _hide(target):
    """Give `target` the hidden attribute, on Windows; a dot-name is hidden elsewhere. Raises
    OSError when Windows refuses."""
    if os.name != "nt":
        return
    kernel = ctypes.windll.kernel32
    if not kernel.SetFileAttributesW(ctypes.c_wchar_p(target), FILE_ATTRIBUTE_HIDDEN):
        raise ctypes.WinError()


def is_hidden(target):
    """Does the file at `target` carry the hidden attribute (always True off Windows)?"""
    if os.name != "nt":
        return os.path.basename(target).startswith(".")
    return bool(os.stat(target).st_file_attributes & FILE_ATTRIBUTE_HIDDEN)


def stage(folder, data):
    """Write `data` as a new file beside the marker, hidden, flushed to disk. Returns its path;
    nothing is changed yet. OSError when it cannot be written (a read-only place, a refused
    write): the partial file is removed."""
    temp = os.path.join(paths.stored(folder), ".tagpup.%s.tmp" % uuid.uuid4().hex)
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _hide(temp)
    except OSError:
        discard(temp)
        raise
    return temp


def publish(temp, folder):
    """Put a staged file in place as the folder's marker, in one rename. OSError when refused:
    the staged file is left for `discard`."""
    os.replace(temp, location(folder))


def discard(temp):
    """Remove a staged file; one that is not there is fine."""
    try:
        os.remove(temp)
    except OSError:
        pass


def clear_stale(folder, now=None):
    """Remove the temporary files of a run that crashed, in `folder`: ours by name, and older
    than an hour. Returns how many went."""
    gone, now = 0, time.time() if now is None else now
    try:
        with os.scandir(paths.stored(folder)) as listing:
            for entry in listing:
                if _TEMP.match(entry.name):
                    try:
                        if now - entry.stat().st_mtime > STALE_SECONDS:
                            os.remove(entry.path)
                            gone += 1
                    except OSError:
                        continue
    except OSError:
        pass
    return gone


def subfolders(parent):
    """The folders directly in `parent`, as stored; [] for one that cannot be listed (one listing, no recursion)."""
    try:
        with os.scandir(paths.stored(parent)) as listing:
            return [entry.path for entry in listing if images.walks_into(entry)]
    except OSError:
        return []


def read_in(folders, library_id):
    """({folder id: [the folders among `folders` holding an entry of `library_id` for it]}, stats): the marker of
    each folder given, read and nothing listed. stats counts the markers read, malformed and unreadable."""
    found, stats = {}, {"folders": 0, "markers": 0, "malformed": 0, "unreadable": 0}
    seen = set()
    for folder in folders:
        key = paths.key(folder)
        if key in seen:
            continue
        seen.add(key)
        marker = read(folder)
        if marker.state == ABSENT:
            continue
        stats["markers"] += 1
        if marker.state == MALFORMED:
            stats["malformed"] += 1
        elif marker.state == UNREADABLE:
            stats["unreadable"] += 1
        elif marker.state == OK and marker.entry_of(library_id):
            found.setdefault(marker.entry_of(library_id), []).append(paths.stored(folder))
    return found, stats


def merge(into, found):
    """Add the places `found` to `into` ({folder id: [folders]}), each folder once."""
    for folder_id, places in found.items():
        have = into.setdefault(folder_id, [])
        for place in places:
            if not any(paths.same(place, each) for each in have):
                have.append(place)


def find(tops, library_id):
    """({folder id: [the folders holding an entry of `library_id` for it]}, stats) of the
    markers under `tops` (folders, at any depth), each folder listed once. stats counts the
    folders listed, markers read, markers malformed and folders or markers that could not be
    read. A folder that cannot be listed is passed over and counted: what lay in it is not
    known, so nothing is decided from its absence."""
    found, stats = {}, {"folders": 0, "markers": 0, "malformed": 0, "unreadable": 0}
    seen = set()
    pending = [paths.stored(top) for top in tops]
    while pending:
        folder = pending.pop()
        key = paths.key(folder)
        if key in seen:
            continue
        seen.add(key)
        try:
            listing = os.scandir(folder)
        except OSError:
            stats["unreadable"] += 1
            continue
        stats["folders"] += 1
        has_marker, subfolders = False, []
        with listing:
            for entry in listing:
                if images.walks_into(entry):
                    subfolders.append(entry.path)
                elif paths.name_key(entry.name) == paths.name_key(NAME):
                    has_marker = True
        pending.extend(reversed(subfolders))
        if not has_marker:
            continue
        marker = read(folder)
        stats["markers"] += 1
        if marker.state == MALFORMED:
            stats["malformed"] += 1
        elif marker.state == UNREADABLE:
            stats["unreadable"] += 1
        elif marker.state == OK:
            folder_id = marker.entry_of(library_id)
            if folder_id:
                found.setdefault(folder_id, []).append(folder)
    return found, stats

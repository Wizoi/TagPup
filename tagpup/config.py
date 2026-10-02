"""Where TagPup's libraries are, which ExifTool the machine has, and -- once, for each
library that has no settings of its own yet -- what the old config.ini said.

config.ini was read in 26 places, each with its own idea of where the file is and what
a missing value means, and then by this module alone. Its settings are each library's
now (tagpup.services.settings; docs/ARCHITECTURE.md, phase 7.6): the CLIP model a
library's vectors were made with, the face thresholds, Suggest's words, the rename
format and the ExifTool it names. What is left here is the machine's:

- TAGPUP_HOME, the folder whose data/ holds the libraries, their backups, locks and
  logs. Unset, it is the folder the code is in, where data/ has always been. Tests set
  it to a folder of their own, so nothing they do reaches the app somebody is using.
  Where the libraries are is not a setting: data/ in the home.
- Which ExifTool the machine has: where its installer puts it, else the one on PATH.
  A library may name another.
- Which folder on this machine holds each root of the libraries (machine_roots.json in the
  home; docs/ARCHITECTURE.md, "Roots and machines"). Absent means nothing is mapped.
- `code_version`, the installed version the code is (scripts/install_app.py writes its
  name beside it), or None for a checkout: what the pages say answers them.
- `config_ini`, what a home's config.ini says, which tagpup.runtime hands to the
  one-time stamping of a library holding no settings (tagpup.services.settings.of), so
  a library in use keeps the settings it was made with. Nothing else reads the file
  (tests/test_config_single_owner.py); once every library has been stamped it is unused,
  and can be deleted.
"""
import configparser
import contextlib
import json
import os
import platform
import shutil
import threading
import time

from tagpup.core import machine, paths

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The folder in a home that holds the libraries.
DATA = "data"

#: The file an installed version holds its name in, first line (scripts/install_app.py).
VERSION_FILE = "VERSION.txt"


def home():
    """The TagPup home: TAGPUP_HOME, else the code folder."""
    return os.path.abspath(os.environ.get("TAGPUP_HOME") or CODE_ROOT)


def data_dir():
    """The folder the libraries are in: data/ in the home."""
    return os.path.join(home(), DATA)


def code_version(code_root=None):
    """The name of the installed version this code is -- `20260926-101500-dc32868` -- or
    None when it runs from a checkout, which has no VERSION.txt."""
    try:
        with open(os.path.join(code_root or CODE_ROOT, VERSION_FILE), encoding="utf-8") as handle:
            return handle.readline().strip() or None
    except OSError:
        return None


def library_path(db_name):
    """Where the library with that file name lives."""
    return os.path.join(data_dir(), db_name)


def default_exiftool():
    """Where ExifTool's Windows installer puts it, or the one on PATH elsewhere."""
    if platform.system() == "Windows":
        return os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Username"),
                            r"AppData\Local\Programs\ExifTool\exiftool.exe")
    return shutil.which("exiftool") or "/usr/bin/exiftool"


def exiftool_path(named=""):
    """The ExifTool to run: the program a library names (its paths.exiftool setting) if
    it exists, else where the installer puts it, else the one on PATH.

    If none exists, the one asked for, so that the error names it.
    """
    named = (named or "").strip()
    path = os.path.expandvars(named) if named else default_exiftool()
    if os.path.exists(path):
        return path
    return shutil.which("exiftool") or path


def _same_file(a, b):
    """Are `a` and `b` one program on this machine? Asked of the file system, not the
    spelling (tagpup.core.paths spells photo paths; this is neither)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def config_ini(folder=None):
    """What the config.ini of this home (or of `folder`) says, as {"section.key": value},
    or None when there is none: for stamping a library that holds no settings yet, and
    for nothing else.

    An ExifTool it names that is the one the installer put where it puts it is given as
    "" -- found on each machine, rather than this machine's profile folder written into
    the library.
    """
    path = os.path.join(folder or home(), "config.ini")
    if not os.path.exists(path):
        return None
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    found = {"%s.%s" % (section, key): value for section in parser.sections()
             for key, value in parser.items(section)}
    exiftool = found.get("paths.exiftool", "").strip()
    if exiftool and _same_file(os.path.expandvars(exiftool), default_exiftool()):
        found["paths.exiftool"] = ""
    return found


#: The file in a home that says where this machine keeps each root of the libraries.
MACHINE_ROOTS_FILE = "machine_roots.json"


class MachineMapError(ValueError):
    """machine_roots.json is there and cannot be used. Never read as "no mapping": rows
    that hold a root's name would then be opened at the wrong place, or nowhere."""


def machine_roots_path():
    return os.path.join(home(), MACHINE_ROOTS_FILE)


def _no_duplicate_keys(pairs):
    found = {}
    for name, value in pairs:
        if name in found:
            raise ValueError("%r appears twice" % name)
        found[name] = value
    return found


def _read_whole(path):
    """The file's text, None when there is no file. Windows refuses a reader, for a moment,
    the file a writer is renaming a new one over; that is waited out, a second at most."""
    for attempt in range(40):
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:8]:
                raise MachineMapError("%s is saved as UTF-16; save it as UTF-8" % path)
            return raw.decode("utf-8-sig")
        except FileNotFoundError:
            return None
        except PermissionError as problem:
            if attempt == 39:
                raise MachineMapError("%s cannot be read: %s" % (path, problem)) from problem
            time.sleep(0.025)
        except UnicodeDecodeError as problem:
            raise MachineMapError("%s cannot be read as UTF-8; save it as UTF-8: %s" % (path, problem)) from problem
        except OSError as problem:
            raise MachineMapError("%s cannot be read: %s" % (path, problem)) from problem


def machine_roots(path=None):
    """Where this machine keeps each root: {name: (native location, ...)}, the first of
    a root's locations being where a path under it is put. {} when the home has no
    machine_roots.json, which means this machine maps nothing.

        {"version": 1, "roots": {"pictures": ["D:\\Training\\Pictures"]}}

    Several libraries on one machine share it: a root the library does not have is
    ignored by that library (tagpup.core.paths.Roots). Saved as UTF-8, with or without a
    BOM. Refused, with MachineMapError naming the file and the fault: unreadable, UTF-16,
    not JSON, a key twice, a key it does not know, a bad root name, a location that is not
    absolute or starts with the long-path or device prefix, a root with none, one location under two
    roots, and one root's locations nested (core.paths.check_locations).

    A missing file is an empty map, and that is not harmless once a library holds roots:
    every row under a root then raises paths.UnmappedRoot, and so does a path under that
    root's share address, naming this file and the line to add. Read-only: a process reading
    while another replaces the file sees the old file or the new, as long as the writer
    renames a finished file over it, and every reader loads the whole file once.
    """
    path = path or machine_roots_path()
    return _parse(_read_whole(path), path)


def _parse(text, path):
    if text is None:
        return {}
    try:
        found = json.loads(text, object_pairs_hook=_no_duplicate_keys)
        if not isinstance(found, dict) or set(found) - {"version", "roots"}:
            raise ValueError('it holds an object with "version" and "roots", and nothing else')
        if found.get("version") != 1:
            raise ValueError('"version" is 1')
        listed = found.get("roots")
        if not isinstance(listed, dict):
            raise ValueError('"roots" is an object of root name to a list of locations')
        for name, locations in listed.items():
            if isinstance(locations, str):
                locations = [locations]
            if not isinstance(locations, list) or not all(isinstance(each, str) for each in locations):
                raise ValueError("root %r: locations are a list of paths" % name)
            listed[name] = locations
        return paths.check_locations(listed)
    except ValueError as problem:
        raise MachineMapError("%s: %s" % (path, problem)) from problem


_MAPS = {}
_MAPS_LOCK = threading.Lock()


def _map_stamp(path):
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (found.st_mtime_ns, found.st_size)


#: How long a map is trusted on its stamp alone. A rewrite that keeps size and mtime (a
#: restore, a copy that keeps timestamps) is then found by content within this long.
RECHECK_SECONDS = 5


def _machine_map(path):
    """machine_roots(), read again when the file's (mtime_ns, size) has changed or more than
    RECHECK_SECONDS have passed since it was read -- then only to compare its content, and
    the parsed map is kept when it is the same. One stat per call otherwise. A missing file
    is remembered too, as an empty map."""
    stamp = _map_stamp(path)
    now = time.monotonic()
    with _MAPS_LOCK:
        held = _MAPS.get(path)
    if held is not None and held[0] == stamp and now - held[3] <= RECHECK_SECONDS:
        return held[1]
    text = _read_whole(path)
    digest = None if text is None else hash(text)
    if held is not None and held[2] == digest:
        loaded = held[1]
    else:
        loaded = _parse(text, path)
    with _MAPS_LOCK:
        _MAPS[path] = (stamp, loaded, digest, now)
    return loaded


def roots_of(library_roots, path=None):
    """The paths.Roots for a library's roots ({name: logical address}) on this machine.

    Costs one stat of the map file, and reads it only when its stamp changed or five seconds
    have passed (then to compare the content, keeping the prepared map when it is the same),
    so an edit to machine_roots.json is picked up by the next call, in this process, and
    2,000 quick calls with no change read nothing. Still, an operation builds one Roots and passes it down
    to what it calls (stage 2 does this for each request, run and job), instead of asking
    per row. A library with roots on a machine with no map raises paths.UnmappedRoot at
    the first path it is asked to convert."""
    path = path or machine_roots_path()
    return paths.Roots.of(library_roots, _machine_map(path), path)


def _old(file, seconds):
    try:
        return time.time() - os.path.getmtime(file) > seconds
    except OSError:
        return False


def _take_over(lock, stale):
    """Remove a stale `lock` -- one editor at a time doing it, through a guard file made
    exclusively, and only if it is still stale once the guard is held: two that both saw it stale
    would otherwise both remove it, the second removing the fresh lock the first's successor had
    just made, and two editors would be let in. True when the caller may try the lock again."""
    guard = lock + ".guard"
    try:
        os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except (FileExistsError, PermissionError):
        if _old(guard, stale):
            try:
                os.remove(guard)   # a crash held it
            except OSError:
                pass
        return False
    try:
        if _old(lock, stale):
            try:
                os.remove(lock)
            except OSError:
                pass
        return True
    finally:
        try:
            os.remove(guard)
        except OSError:
            pass


@contextlib.contextmanager
def _edit_lock(path, wait=5.0, stale=30.0):
    """The one editor of the map at a time, across processes: a file beside it made exclusively
    for as long as the read-modify-write takes. Two libraries adopting two roots at once would
    each read the map without the other's root and the second rename would drop the first's.
    A lock a crash left is taken over once it is `stale` seconds old."""
    lock = path + ".lock"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except (FileExistsError, PermissionError):   # Windows: a file being deleted refuses with the latter
            if _old(lock, stale) and _take_over(lock, stale):
                continue
            if time.monotonic() > deadline:
                raise MachineMapError("%s is being edited by another process (%s is there); try again in a moment"
                                      % (path, lock)) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def add_machine_root(name, location, path=None):
    """Say where this machine keeps root `name`: write it to machine_roots.json, keeping
    every other root in it. True when it was written; False when the map already places the
    root, at a place listed with `location` among them (nothing is written then). Refused,
    with MachineMapError and nothing written, when the map already places the root and
    `location` is not one of its places (it is not changed here: that is the owner's edit,
    which keeps the old place listed), when the file is there and cannot be used, or when
    the result would be refused at load (a place under two roots, nested places).

    The file is written whole to a temporary name beside it and renamed over it
    (os.replace), so a process reading meanwhile sees the old file or the new, never half of
    one; Windows refuses the rename for a moment while a reader has the file open, which is
    waited out as the reader waits out the rename. One editor at a time, across processes
    (`_edit_lock`): each reads the map as the one before left it."""
    path = path or machine_roots_path()
    folded = paths.root_name(name)
    with _edit_lock(path):
        return _add_machine_root(folded, location, path)


def _add_machine_root(folded, location, path):
    text = _read_whole(path)
    held = _parse(text, path)
    if folded in held:
        if any(paths.key(location) == paths.key(place) for place in held[folded]):
            return False
        raise MachineMapError("%s already places root %r at %s; %r is not one of its places. Edit the file "
                              "to add it (keep the old place listed, so every path stays recognised)"
                              % (path, folded, ", ".join(held[folded]), location))
    merged = {each: list(places) for each, places in held.items()}
    merged[folded] = [location]
    _write_map(merged, path)
    return True


def _write_map(merged, path):
    """Write `merged` ({name: [places]}) as the map, whole, to a temporary name beside it and
    renamed over it: refused (MachineMapError, nothing written) when the result would be refused at
    load. The caller holds `_edit_lock`."""
    try:
        merged = paths.check_locations(merged)
    except ValueError as problem:
        raise MachineMapError("%s: %s" % (path, problem)) from problem
    body = json.dumps({"version": 1, "roots": {each: list(places) for each, places in sorted(merged.items())}},
                      indent=2) + "\n"
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    temporary = path + ".%d.tmp" % os.getpid()
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(40):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as problem:
                if attempt == 39:
                    raise MachineMapError("%s cannot be replaced: %s" % (path, problem)) from problem
                time.sleep(0.025)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def set_location(name, new_location, path=None, expected=None, must_exist=True):
    """Move root `name` on this machine to `new_location`: the new place first -- where a path is
    put from now on -- and the old places kept after it, so that every path stays recognised and
    `change_back` is the reverse. Returns {"changed", "places", "previous"} (the places listed
    after and before, native); `changed` False, nothing written, when the root is at
    `new_location` already (a second click, a second tab that came after the first).

    Refused, with MachineMapError and nothing written: a location that is not an absolute folder
    on this machine, that starts with the long-path or device prefix, that does not exist (unless
    `must_exist` is False, for a caller that has asked the disk itself, with a deadline: a
    share that is away would hold this one for as long as Windows waits), that another root holds,
    or one the root's own other places are nested in; the map unreadable; and, when `expected` is
    given, a root whose first place is not `expected` (the map was changed since the caller looked
    -- by another tab, by hand -- and what it was about to confirm is not what it saw).

    One editor at a time across processes (`_edit_lock`), the file read inside it, written whole
    and renamed over: a reader sees the old map or the new."""
    path = path or machine_roots_path()
    folded = paths.root_name(name)
    if not isinstance(new_location, str) or not paths.is_native_absolute(new_location):
        raise MachineMapError("%r is not an absolute folder on this machine" % (new_location,))
    new_location = paths.stored(new_location)
    if must_exist and not os.path.isdir(new_location):
        raise MachineMapError("%s is not a folder that exists here" % new_location)
    with _edit_lock(path):
        held = _parse(_read_whole(path), path)
        before = list(held.get(folded, ()))
        if before and paths.key(before[0]) == paths.key(new_location):
            return {"changed": False, "places": before, "previous": before}
        if expected is not None and (not before or paths.key(before[0]) != paths.key(expected)):
            raise MachineMapError("The map has changed since you looked: %s is now at %s, not %s. Nothing was changed."
                                  % (folded, before[0] if before else "no place on this machine", expected))
        places = [new_location] + [place for place in before if paths.key(place) != paths.key(new_location)]
        merged = {each: list(listed) for each, listed in held.items()}
        merged[folded] = places
        _write_map(merged, path)
        return {"changed": True, "places": places, "previous": before}


def change_back(name, path=None, expected=None):
    """Put root `name` back at the place it was before the last `set_location`: the first two
    places swap. Returns what set_location does. Refused, nothing written, for a root with only
    one place, and for the map having changed since the caller looked (`expected`: the first
    place it saw). The disk is the caller's to ask (it may be away)."""
    path = path or machine_roots_path()
    folded = paths.root_name(name)
    with _edit_lock(path):
        held = _parse(_read_whole(path), path)
        before = list(held.get(folded, ()))
        if len(before) < 2:
            raise MachineMapError("%s has no previous place on this machine to go back to" % folded)
        if expected is not None and paths.key(before[0]) != paths.key(expected):
            raise MachineMapError("The map has changed since you looked: %s is now at %s, not %s. Nothing was changed."
                                  % (folded, before[0], expected))
        places = [before[1], before[0]] + before[2:]
        merged = {each: list(listed) for each, listed in held.items()}
        merged[folded] = places
        _write_map(merged, path)
        return {"changed": True, "places": places, "previous": before}


def describe_machine(library_roots, path=None):
    """Each of a library's roots with where this machine keeps it, for TagTuner's gear."""
    return roots_of(library_roots, path).describe()


def propose_row(native_path, library_roots, path=None):
    """The root-relative form a native path would have, "@name/under/it", or None when it
    is under no root of the library. Pure: nothing is written and no disk is asked."""
    return roots_of(library_roots, path).propose_row(native_path)


# The store converts paths at its boundary and may import only core: it asks core.machine,
# and this is what answers (docs/ARCHITECTURE.md, "Roots and machines").
machine.provide(roots_of)

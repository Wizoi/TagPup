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
import json
import os
import platform
import shutil
import threading
import time

from tagpup.core import paths

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


def describe_machine(library_roots, path=None):
    """Each of a library's roots with where this machine keeps it, for TagTuner's gear."""
    return roots_of(library_roots, path).describe()


def propose_row(native_path, library_roots, path=None):
    """The root-relative form a native path would have, "@name/under/it", or None when it
    is under no root of the library. Pure: nothing is written and no disk is asked."""
    return roots_of(library_roots, path).propose_row(native_path)

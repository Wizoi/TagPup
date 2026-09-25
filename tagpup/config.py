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
- `config_ini`, what a home's config.ini says, which tagpup.runtime hands to the
  one-time stamping of a library holding no settings (tagpup.services.settings.of), so
  a library in use keeps the settings it was made with. Nothing else reads the file
  (tests/test_config_single_owner.py); once every library has been stamped it is unused,
  and can be deleted.
"""
import configparser
import os
import platform
import shutil

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The folder in a home that holds the libraries.
DATA = "data"


def home():
    """The TagPup home: TAGPUP_HOME, else the code folder."""
    return os.path.abspath(os.environ.get("TAGPUP_HOME") or CODE_ROOT)


def data_dir():
    """The folder the libraries are in: data/ in the home."""
    return os.path.join(home(), DATA)


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

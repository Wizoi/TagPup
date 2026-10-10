"""Nothing reads config.ini.

It was read in 26 places. They disagreed about where the file is (beside the code, or
in whatever folder the program started in), how to decode it, what a relative data_dir
is relative to, and which ExifTool to fall back to, and the servers rewrote it from
three different handlers. Then tagpup/config.py alone read it, and then only to stamp a
library that held no settings; every live library has been stamped, so that reading is
gone too (docs/findings.md). Each library holds its own settings (tagpup.services.settings;
docs/ARCHITECTURE.md, phase 7.6); an old file in a home is ignored, never read, never
written, never deleted. This fails the build on:

* configparser anywhere -- building a parser means reading or writing a config;
* the "config.ini" name as a string anywhere -- nothing needs to know where it is;
* a `config_ini` reader, called or imported, anywhere;
* a reader of the old settings coming back to tagpup.config (load, embedder_settings...);
* config.example.ini, or setup's copy of it, coming back.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

sys.path.insert(0, ROOT)
from tagpup import config  # noqa: E402

OWNER = os.path.join("tagpup", "config.py")

FORBIDDEN = [
    (re.compile(r"\bconfigparser\b|\bConfigParser\s*\("), "configparser"),
    (re.compile(r"""(['"])config\.ini\1"""), 'the "config.ini" name'),
]

#: The reader that was: `tagpup_config.config_ini`, `config.config_ini(...)`, importing it, a
#: parameter named for it.
READS_THE_FILE = re.compile(r"\bconfig_ini\b")

#: What tagpup.config is now: the home, where the libraries are, the machine's ExifTool,
#: and nothing that reads a file of settings.
PUBLIC = {"home", "data_dir", "library_path", "default_exiftool", "exiftool_path",
          # Which installed version the code is (VERSION.txt beside it): not a setting.
          "code_version",
          # What makes a folder a home of its own (a test's, a sandbox's): where things are, not a setting.
          "own_home_environment",
          # Where this machine keeps each root of the libraries: the machine's, not a setting.
          "machine_roots", "machine_roots_path", "MachineMapError", "roots_of",
          "propose_row", "add_machine_root", "set_location", "change_back"}


def _lines(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.lstrip().startswith("#"):
                yield number, line


def offenders():
    found = []
    for relative in python_sources():
        for number, line in _lines(relative):
            for pattern, label in FORBIDDEN:
                if pattern.search(line):
                    found.append("%s:%d  %s\n    %s" % (relative, number, label, line.strip()))
    return found


def readers():
    found = []
    for relative in python_sources():
        for number, line in _lines(relative):
            if READS_THE_FILE.search(line):
                found.append("%s:%d  %s" % (relative, number, line.strip()))
    return found


class ConfigHasOneOwner(unittest.TestCase):
    def test_nothing_parses_or_names_the_file(self):
        problems = offenders()
        self.assertEqual(problems, [], "\n\nNothing knows config.ini:\n\n" + "\n".join(problems))

    def test_nothing_reads_it(self):
        problems = readers()
        self.assertEqual(problems, [], "\n\nconfig.ini is not read, not even to stamp a library holding no "
                         "settings (they get the defaults); a library's settings come from "
                         "tagpup.services.settings:\n\n" + "\n".join(problems))

    def test_config_reads_no_setting(self):
        public = {name for name in vars(config) if not name.startswith("_") and callable(getattr(config, name))
                  and getattr(getattr(config, name), "__module__", None) == config.__name__}
        self.assertEqual(public, PUBLIC, "tagpup.config is the home, the data folder, the machine's ExifTool "
                         "and nothing that reads a file of settings; a setting is the library's")
        for gone in ("load", "read_file", "write_file", "resolve", "embedder_settings", "face_settings",
                     "candidate_tags", "rename_format", "DEFAULTS", "config_path", "config_ini"):
            self.assertFalse(hasattr(config, gone), gone)

    def test_the_example_and_its_copy_are_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "config.example.ini")))
        for script in ("setup.bat", "setup.ps1"):
            with open(os.path.join(ROOT, script), encoding="utf-8") as handle:
                self.assertNotIn("config.example.ini", handle.read(), script)

    def test_the_guard_recognises_what_it_forbids(self):
        # A guard whose patterns quietly match nothing passes forever.
        samples = {
            "import configparser": True,
            "config = configparser.ConfigParser(interpolation=None)": True,
            'config_path = os.path.join(root, "config.ini")': True,
            'photo_list = data.get("paths", [])': False,
            '"""The ExifTool config.ini names."""': False,
        }
        for sample, expected in samples.items():
            hit = any(pattern.search(sample) for pattern, _ in FORBIDDEN)
            self.assertEqual(hit, expected, sample)
        reads = {
            "found = tagpup_config.config_ini()": True,
            "return settings.of(library, tagpup_config.config_ini)": True,
            "settings = runtimes.peek_settings(library)": False,
            "from tagpup.config import config_ini": True,
            "def of(library, config_ini=None):": True,
            "values = _stamping(_found(config_ini))": True,
            "def of(library):": False,
        }
        for sample, expected in reads.items():
            self.assertEqual(bool(READS_THE_FILE.search(sample)), expected, sample)

    def test_only_config_knows_where_the_code_is(self):
        """A package module that finds folders from its own __file__ breaks when it moves.

        tagpup/store/db.py found its backup folder two levels up: the repository while it
        was scripts/db.py, the inside of tagpup/ after the move. tagpup.config.CODE_ROOT
        is the one place that knows where the code is.
        """
        prefix = "tagpup" + os.sep
        found = []
        for relative in python_sources():
            if relative.startswith(prefix) and relative != OWNER:
                with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                    if "__file__" in handle.read():
                        found.append(relative)
        self.assertEqual(found, [])

    def test_an_old_file_in_the_home_changes_nothing(self):
        """The owner's file stays on disk, untouched, and is ignored."""
        import tempfile
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import own_home
        home = own_home.OwnHome()
        self.addCleanup(home.close)
        home.write_old_config({"paths": {"data_dir": tempfile.gettempdir()}, "model": {"name": "ViT-L-14"}})
        before = os.path.getmtime(os.path.join(home.root, "config" + ".ini"))
        self.assertEqual(config.data_dir(), os.path.join(home.root, "data"))
        self.assertEqual(before, os.path.getmtime(os.path.join(home.root, "config" + ".ini")))


if __name__ == "__main__":
    unittest.main()

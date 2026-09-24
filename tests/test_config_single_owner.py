"""Nothing but tagpup/config.py reads config.ini or decides what a path setting means.

It was read in 26 places. They disagreed about where the file is (beside the code, or
in whatever folder the program started in), how to decode it, what a relative data_dir
is relative to, and which ExifTool to fall back to, and the servers rewrote it from
three different handlers. This fails the build on:

* configparser anywhere else -- building a parser means reading or writing a config;
* the "config.ini" name as a string -- only the owner knows where the file lives;
* a path setting read raw, `.get("paths", "data_dir")` -- it would be relative to the
  working directory. tagpup.config.data_dir(), library_path() and the rest resolve it.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

OWNER = os.path.join("tagpup", "config.py")

FORBIDDEN = [
    (re.compile(r"\bconfigparser\b|\bConfigParser\s*\("), "configparser"),
    (re.compile(r"""(['"])config\.ini\1"""), 'the "config.ini" name'),
    (re.compile(r"""\.get(?:int|float|boolean)?\(\s*(['"])paths\1\s*,\s*['"]"""),
     "a path setting read raw"),
]


def offenders():
    found = []
    for relative in python_sources():
        if relative == OWNER:
            continue
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if line.lstrip().startswith("#"):
                    continue
                for pattern, label in FORBIDDEN:
                    if pattern.search(line):
                        found.append("%s:%d  %s\n    %s" % (relative, number, label, line.strip()))
    return found


class ConfigHasOneOwner(unittest.TestCase):
    def test_nothing_else_reads_the_config(self):
        problems = offenders()
        self.assertEqual(problems, [], "\n\nSettings come from tagpup.config (load, data_dir, "
                         "library_path, exiftool_path, embedder_settings, remember_library ...):\n\n"
                         + "\n".join(problems))

    def test_the_guard_recognises_what_it_forbids(self):
        # A guard whose patterns quietly match nothing passes forever.
        samples = {
            "import configparser": True,
            "config = configparser.ConfigParser(interpolation=None)": True,
            'config_path = os.path.join(root, "config.ini")': True,
            'data_dir = config.get("paths", "data_dir", fallback="data")': True,
            "cache = settings.get('paths', 'embedding_cache_dir')": True,
            'photo_list = data.get("paths", [])': False,
            'self.paths = set(data.get("paths", []))': False,
            '"""The ExifTool config.ini names."""': False,
            "settings = tagpup_config.load()": False,
        }
        for sample, expected in samples.items():
            hit = any(pattern.search(sample) for pattern, _ in FORBIDDEN)
            self.assertEqual(hit, expected, sample)

    def test_only_config_knows_where_the_code_is(self):
        """A package module that finds folders from its own __file__ breaks when it moves.

        tagpup/store/db.py found its backup folder two levels up: the repository while it
        was scripts/db.py, the inside of tagpup/ after the move. tagpup.config.CODE_ROOT
        is the one place that knows where the code is.
        """
        prefix = "tagpup" + os.sep
        offenders = []
        for relative in python_sources():
            if relative.startswith(prefix) and relative != OWNER:
                with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                    if "__file__" in handle.read():
                        offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_the_owner_is_where_the_file_is_read(self):
        """The check is worthless if the one allowed place stops matching too."""
        with open(os.path.join(ROOT, OWNER), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("configparser.ConfigParser(", source)
        self.assertIn('"config.ini"', source)


if __name__ == "__main__":
    unittest.main()

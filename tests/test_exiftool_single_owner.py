"""Every ExifTool process TagPup starts comes from tagpup/files/exiftool_session.py.

pyexiftool's own ExifTool / ExifToolHelper read a command's stdout to the end before
they read any stderr. On Windows the stderr pipe holds about 4 KB; a batch that draws
a warning per file fills it, ExifTool blocks, and pyexiftool waits for stdout forever
at 0% CPU. Two maintenance scripts sat like that for two days.

ExifToolSession drains both pipes at once and gives every command a deadline. It only
helps where it is used, and the fix was first applied one script at a time, so this
fails the build on a direct construction of pyexiftool's classes anywhere else.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

SOURCES = python_sources()
OWNER = os.path.join("tagpup", "files", "exiftool_session.py")

# A call of any of pyexiftool's session classes, qualified or imported bare.
# ExifToolSession( is not matched: the class name must end where the paren begins.
FORBIDDEN = re.compile(r"\b(?:exiftool\.)?(ExifToolHelper|ExifToolAlpha|ExifTool)\s*\(")


def offenders():
    found = []
    for relative in SOURCES:
        if relative == OWNER:
            continue
        full = os.path.join(ROOT, relative)
        if not os.path.exists(full):
            continue
        with open(full, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if line.lstrip().startswith("#"):
                    continue
                if FORBIDDEN.search(line):
                    found.append("%s:%d  %s" % (relative, number, line.strip()))
    return found


class ExifToolHasOneOwner(unittest.TestCase):
    def test_nothing_constructs_pyexiftool_directly(self):
        problems = offenders()
        self.assertEqual(problems, [], "\n\nStart ExifTool through exiftool_session."
                         "ExifToolSession, which cannot deadlock on a full stderr pipe:\n\n"
                         + "\n".join(problems))

    def test_the_guard_recognises_what_it_forbids(self):
        # A guard whose pattern quietly matches nothing passes forever.
        samples = {
            "with exiftool.ExifToolHelper(executable=executable) as et:": True,
            "with exiftool.ExifTool(executable=executable) as et:": True,
            "et = ExifToolHelper(executable=path)": True,
            "et = ExifTool (executable=path)": True,
            "with ExifToolSession(executable=executable) as et:": False,
            "from exiftool_session import ExifToolSession": False,
            'path = r"AppData\\Local\\Programs\\ExifTool\\exiftool.exe"': False,
        }
        for sample, expected in samples.items():
            self.assertEqual(bool(FORBIDDEN.search(sample)), expected, sample)

    def test_the_owner_is_where_the_classes_are_used(self):
        """The check is worthless if the one allowed place stops matching too."""
        with open(os.path.join(ROOT, OWNER), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("class ExifToolSession(exiftool.ExifToolHelper", source)


if __name__ == "__main__":
    unittest.main()

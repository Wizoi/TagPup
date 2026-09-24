"""Nothing but tagpup/core/paths.py decides how a photo path is spelled or compared.

Every component used to convert paths by hand, each its own way, and the database
was looked up in a spelling it never stored. Tag writes, renames and deletes reported
success and changed nothing, and the tests passed because their fixtures were built
with the same helper they were testing.

So this fails the build on the hand-made forms, anywhere outside paths.py:

* separator conversion: .replace("\\\\", "/") and the reverse;
* os.path.normcase, and local helpers named like the ones that caused this;
* SQL comparing a path column by LOWER() or LIKE -- LIKE reads "_" as any character
  and neither can use the path index. Use paths.sql_equals / paths.sql_under.

A line that is genuinely not about a photo path -- a tag hierarchy, a database
file's URI -- can say so with a `# not a path: <why>` comment, which keeps each
exception visible where it is made.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

SOURCES = python_sources()
OWNER = os.path.join("tagpup", "core", "paths.py")
MARKER = "# not a path:"

FORBIDDEN = [
    # Source text `.replace("\\", "/")`: the pattern is two literal backslashes.
    (re.compile(r"""\.replace\(\s*(['"])\\\\\1\s*,\s*(['"])/\2"""), 'replace("\\\\", "/")'),
    (re.compile(r"""\.replace\(\s*(['"])/\1\s*,\s*(['"])\\\\\2"""), 'replace("/", "\\\\")'),
    (re.compile(r"\bnormcase\("), "os.path.normcase"),
    (re.compile(r"^\s*def\s+(normalize_path|to_db_path|normalise_path|norm_path)\b"),
     "a local path-normalising helper"),
    (re.compile(r"LOWER\(\s*(\w+\.)?(photo_)?path\s*\)", re.I), "LOWER(path) in SQL"),
    (re.compile(r"\b(photo_)?path\s+(NOT\s+)?LIKE\b", re.I), "LIKE on a path column"),
]


class PathsHaveOneOwner(unittest.TestCase):
    def test_no_hand_made_path_spellings(self):
        problems = []
        for relative in SOURCES:
            if relative == OWNER:
                continue
            full = os.path.join(ROOT, relative)
            if not os.path.exists(full):
                continue
            with open(full, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if MARKER in line or line.lstrip().startswith("#"):
                        continue
                    for pattern, label in FORBIDDEN:
                        if pattern.search(line):
                            problems.append("%s:%d  %s\n    %s" % (relative, number, label, line.strip()))
        self.assertEqual(problems, [], "\n\nPaths are spelled and compared by tagpup/core/paths.py "
                         "only (stored / key / same / is_under / sql_equals / sql_under):\n\n"
                         + "\n".join(problems))

    def test_the_guard_recognises_what_it_forbids(self):
        # A guard whose patterns quietly match nothing passes forever.
        samples = {
            'x = p.replace("\\\\", "/")': True,
            "x = p.replace('/', '\\\\')": True,
            "k = os.path.normcase(p)": True,
            "def to_db_path(path):": True,
            'cur.execute("SELECT 1 FROM photos WHERE LOWER(path) = LOWER(?)")': True,
            'cur.execute("SELECT 1 FROM faces WHERE photo_path LIKE ?")': True,
            'cur.execute("SELECT 1 FROM photos WHERE people LIKE ?")': False,
            'x = tag.split("/")': False,
        }
        for sample, expected in samples.items():
            hit = any(pattern.search(sample) for pattern, _ in FORBIDDEN)
            self.assertEqual(hit, expected, sample)


if __name__ == "__main__":
    unittest.main()

"""Rules that were written more than once, each now said in one place (docs/findings.md,
#74). The copies agreed when they were found; nothing kept them agreeing. Each class
here holds one rule to its owner: a copy that reappears fails, and where a page must
keep a copy of its own, the page is pinned to the owner.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from shipped_sources import python_sources  # noqa: E402


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
        return f.read()


def sources_matching(pattern, owner=None):
    """Shipped Python files other than `owner` whose text matches `pattern`."""
    owner = os.path.normcase(owner) if owner else None
    return [path for path in python_sources()
            if os.path.normcase(path) != owner and re.search(pattern, read(path))]


class TheTinyBackgroundFace(unittest.TestCase):
    """A face far smaller than the largest in its photo, and small outright, is noise in
    the background: clustering gives it no name and the suggester offers none."""

    def test_is_decided_in_clustering(self):
        from tagpup.core import clustering
        big, small, middling = [0, 0, 100, 100], [0, 0, 20, 20], [0, 0, 40, 60]
        self.assertEqual({1}, clustering.background_faces([big, small, middling]))
        # Small beside a larger face, but not small outright: 50 x 50 is 2,500.
        self.assertEqual(set(), clustering.background_faces([[0, 0, 400, 400], [0, 0, 50, 50]]))
        # Alone in its photo, a face is never background.
        self.assertEqual(set(), clustering.background_faces([small]))
        self.assertEqual(set(), clustering.background_faces([]))
        self.assertEqual({1}, clustering.background_faces([big, None]))

    def test_nothing_else_writes_it(self):
        self.assertEqual([], sources_matching(r"0\.10?\s*\*\s*max_area|area\s*<\s*2000",
                                              os.path.join("tagpup", "core", "clustering.py")))


if __name__ == "__main__":
    unittest.main()

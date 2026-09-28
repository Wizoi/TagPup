"""The lists of damaged photos -- each page's header count, TagPup's folder notice, the
Activity page -- are asked inside a page's request, so they look at each record's folder
once, pass over a folder that is not there without looking at each of its photos, and
never wait on a network share gone away (docs/findings.md, #407).

The records are made as the indexer makes them (damaged_photos.remember); a share is a
UNC path the test's own, its listing stood in for.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damaged_photos  # noqa: E402
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402

SHARE = "\\" * 2 + "harbour-nas" + "\\" + "photos" + "\\" + "Regatta"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_lists_")
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.folder = os.path.join(self.home.root, "Harbour")
        self.here = [damaged_photos.truncated(os.path.join(self.folder, "cut %d.jpg" % n)) for n in range(3)]
        for path in self.here:
            self.found(path, os.stat(path))
        self.unplugged = os.path.join(self.home.root, "Unplugged")
        for n in range(3):
            self.found(os.path.join(self.unplugged, "gone %d.jpg" % n), os.stat(self.here[0]))
        damaged._away.clear()
        self.addCleanup(damaged._away.clear)

    def found(self, path, stat):
        damaged.remember(self.library, [(path, (stat.st_mtime, stat.st_size), "truncated", "found", 0)])


class EachFolderOnce(Case):
    def test_a_folder_that_is_not_there_is_passed_over_without_looking_at_each_photo(self):
        looked = []
        real = os.stat

        def stat(path, *args, **kwargs):
            looked.append(str(path))
            return real(path, *args, **kwargs)

        with mock.patch("os.stat", side_effect=stat):
            shown = damaged.listed(self.library)
        self.assertEqual(sorted(os.path.basename(p) for p in self.here), [each["name"] for each in shown])
        self.assertEqual([], [path for path in looked if "gone " in path], "a photo of a folder not there was looked at")
        self.assertEqual([], [path for path in looked if "cut " in path], "each photo was looked at, not its folder")


class AShareGoneAway(Case):
    def test_the_list_waits_at_most_a_moment_and_then_not_at_all(self):
        on_share = SHARE + "\\" + "far.jpg"
        self.found(on_share, os.stat(self.here[0]))
        hung = threading.Event()
        self.addCleanup(hung.set)
        real = damaged._stamps_in

        def listing(folder):
            if folder.startswith("\\" * 2):
                hung.wait(10)       # a share gone away: the listing does not come back
                return {}
            return real(folder)

        with mock.patch.object(damaged, "_stamps_in", side_effect=listing), \
                mock.patch.object(damaged, "SHARE_WAIT", 0.3):
            started = time.monotonic()
            first = damaged.listed(self.library)
            waited = time.monotonic() - started
            started = time.monotonic()
            damaged.counts(self.library)
            again = time.monotonic() - started
        self.assertLess(waited, 2.0)
        self.assertLess(again, 0.2, "the share gone away was waited on again")
        self.assertEqual(3, len(first), "the photos on disk were not listed")


if __name__ == "__main__":
    unittest.main()

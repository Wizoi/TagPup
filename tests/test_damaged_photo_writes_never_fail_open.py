"""Whether a photo is damaged, asked for a write, is never answered "no" for want of an
answer (docs/findings.md, #407).

The lists of damaged photos share one listing of each folder and take a share that did
not answer in time as away: fine for a count on a page, wrong for a write, which then saw
nothing damaged and wrote into the photo. A write asks each recorded photo's own file,
with the share's timeout, and a share that does not answer refuses the write. And the
lists take a share as away only once a wait on it timed out: a second folder of a healthy
share, asked while the first was being listed, was counted away.

A share is a UNC path the test's own, its answers stood in for.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import DAMAGED_PHOTOS, Result  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries  # noqa: E402

BS = chr(92)
SHARE = BS * 2 + "harbour-nas" + BS + "photos"
PHOTO = SHARE + BS + "Regatta" + BS + "cut short.jpg"


class Case(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="damaged_writes_")
        self.library = Library(home.library("harbour.db"))
        libraries.create(self.library.path)
        damaged.remember(self.library, [(PHOTO, (1_700_000_000.0, 9000), "truncated", "found", 0)])
        for held in (damaged._away, damaged._reading):
            held.clear()
            self.addCleanup(held.clear)
        self.hung = threading.Event()
        self.addCleanup(self.hung.set)

    def share_hangs(self, path):
        if str(path).startswith(BS * 2):
            self.hung.wait(10)
            return None
        return os.stat(path)


class AWrite(Case):
    def test_to_a_photo_on_a_share_that_does_not_answer_is_refused(self):
        result = Result(attempted=1)
        with mock.patch.object(damaged, "_stamp", side_effect=self.share_hangs), \
                mock.patch.object(damaged, "SHARE_WAIT", 0.2), \
                mock.patch.object(libraries, "not_held", return_value=[]):
            refused = libraries.refuse_writes(result, self.library, [PHOTO])
        self.assertTrue(refused, "a write went ahead though whether the photo is damaged could not be told")
        self.assertIn("Can't check", result.refused)
        self.assertEqual([PHOTO], result.details[DAMAGED_PHOTOS])

    def test_even_after_a_list_took_the_share_as_away(self):
        with mock.patch.object(damaged, "_stamps_in", side_effect=lambda folder: self.share_hangs(folder)), \
                mock.patch.object(damaged, "SHARE_WAIT", 0.2):
            self.assertEqual([], damaged.listed(self.library))   # the share taken as away, for the lists
        result = Result(attempted=1)
        with mock.patch.object(damaged, "_stamp", side_effect=self.share_hangs), \
                mock.patch.object(damaged, "SHARE_WAIT", 0.2), \
                mock.patch.object(libraries, "not_held", return_value=[]):
            self.assertTrue(libraries.refuse_writes(result, self.library, [PHOTO]))

    def test_a_bulk_tag_write_is_refused_too(self):
        result = Result(attempted=2)
        with mock.patch.object(damaged, "_stamp", side_effect=self.share_hangs), \
                mock.patch.object(damaged, "SHARE_WAIT", 0.2):
            kept, left = libraries.leave_out_damaged(result, self.library, [PHOTO, SHARE + BS + "jetty.jpg"])
        self.assertEqual((None, None), (kept, left))
        self.assertIn("Can't check", result.refused)


class TheLists(Case):
    def test_a_second_folder_of_a_healthy_share_is_not_taken_as_away(self):
        def slow(folder):
            time.sleep(0.2)    # well within SHARE_WAIT
            return {}

        found = {}
        folders = [SHARE + BS + year for year in ("2019", "2020")]
        with mock.patch.object(damaged, "_stamps_in", side_effect=slow):
            readers = [threading.Thread(target=lambda f=f: found.__setitem__(f, damaged._folder_stamps(f)))
                       for f in folders]
            readers[0].start()
            time.sleep(0.05)
            readers[1].start()
            for reader in readers:
                reader.join()
        self.assertEqual({folder: {} for folder in folders}, found)
        self.assertEqual({}, damaged._away, "a healthy share was taken as away")


if __name__ == "__main__":
    unittest.main()

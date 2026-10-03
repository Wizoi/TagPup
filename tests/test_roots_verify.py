"""Verify: how well a place holds the files a root's rows describe (tagpup.services.roots_verify;
docs/ARCHITECTURE.md, "Roots and machines", TagTuner's Roots).

The library is made as the roots tests make it (tests/roots_library.py: rows the indexer's code
makes, adopted by the explicit command, so the rows are `@pictures/...`), and the places are real
folders: the one the photos are in, a copy that keeps the files' times (robocopy does), a copy
that does not, another folder with some of the same names, a drive that is not connected, a share
that does not answer. Verify must say what each is, count a file that changed as "differs" and never
"missing", say "this location cannot be reached" for a place that is away and not "all missing",
and write nothing: no row, no file, not the map.
"""
import json
import os
import shutil
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.result import NotFound, Refused  # noqa: E402
from tagpup.services import roots_verify  # noqa: E402

WINDOWS = os.name == "nt"


def unused_drive():
    """A drive letter nothing on this machine uses."""
    for letter in "QRSTUVWXYZ":
        if not os.path.exists(letter + ":\\"):
            return letter + ":"
    raise unittest.SkipTest("every drive letter is in use")


class VerifyCase(unittest.TestCase):
    #: Photos on disk in each of three folders, and rows that have none.
    REAL, BULK = 6, 0

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_verify_")
        self.side = rl.Side(self.home, "photo_index", real=self.REAL, bulk=self.BULK, outside=2)
        result = self.side.adopt()
        self.assertIsNone(result.refused, result.refused)
        self.machine = rl.machine()
        self.map_file = config.machine_roots_path()

    def verify(self, location=None, **kwargs):
        return roots_verify.verify(self.side.library, "pictures", location or self.side.pictures, self.machine,
                                   **kwargs)

    def copy_of_pictures(self, keep_times=True, name="Copy"):
        target = os.path.join(self.home.root, name, "Pictures")
        shutil.copytree(self.side.pictures, target, copy_function=shutil.copy2 if keep_times else shutil.copy)
        return target

    def fingerprint(self):
        with open(self.map_file, "rb") as handle:
            return json.dumps(self.side.dump(), default=repr, sort_keys=True), handle.read()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhatItCounts(VerifyCase):
    def test_the_place_the_photos_are_in_matches_and_nothing_is_written(self):
        before = self.fingerprint()
        found = self.verify()
        self.assertTrue(found["reachable"], found)
        rows = 3 * self.REAL
        self.assertEqual((rows, rows, 0, 0, 0), (found["rows"], found["matches"], found["differs"], found["missing"],
                                                 found["unreadable"]))
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertEqual(rows, found["checked"])
        self.assertEqual(before, self.fingerprint(), "Verify wrote a row or the map")

    def test_the_rows_under_no_root_are_grouped_by_folder(self):
        found = self.verify()
        self.assertEqual(2, found["outside_rows"])
        self.assertEqual([2], [group["count"] for group in found["outside"]])
        drive_and_first_folder = os.sep.join(self.side.outside_folder.split(os.sep)[:2])
        self.assertEqual(drive_and_first_folder.lower(), found["outside"][0]["group"].lower())

    def test_a_copy_that_keeps_the_files_times_matches(self):
        found = self.verify(self.copy_of_pictures(keep_times=True))
        self.assertEqual((18, 0, 0), (found["matches"], found["differs"], found["missing"]), found["summary"])
        self.assertFalse(found["poor"])

    def test_a_copy_whose_times_differ_is_differs_never_missing(self):
        """A plain copy gets new modified times: sync settles those, and Verify must not call the
        files missing -- or the owner would be refused a move to a place holding every photo."""
        copy = self.copy_of_pictures(keep_times=False)
        for folder, _dirs, files in os.walk(copy):
            for name in files:
                stamp = time.time() + 3600
                os.utime(os.path.join(folder, name), (stamp, stamp))
        found = self.verify(copy)
        self.assertEqual((0, 18, 0), (found["matches"], found["differs"], found["missing"]), found["summary"])
        self.assertFalse(found["poor"], "a copy of every photo is not a poor result: %s" % found["poor_why"])

    def test_a_photo_fixed_since_it_was_indexed_differs(self):
        target = self.side.real[0]
        with open(target, "ab") as handle:
            handle.write(b"\x00" * 7)
        found = self.verify()
        self.assertEqual((17, 1, 0), (found["matches"], found["differs"], found["missing"]))

    def test_a_photo_that_is_not_there_is_missing(self):
        os.remove(self.side.real[3])
        found = self.verify()
        self.assertEqual((17, 0, 1), (found["matches"], found["differs"], found["missing"]))
        self.assertEqual(1, found["missing"])

    def test_a_folder_that_is_gone_is_all_its_rows_missing(self):
        shutil.rmtree(os.path.dirname(self.side.real[0]))
        found = self.verify()
        self.assertEqual(self.REAL, found["missing"])
        self.assertTrue(found["reachable"])

    def test_another_folder_with_similar_names_is_a_poor_result(self):
        """The same file names in another folder, a few of them: most rows are not there."""
        other = os.path.join(self.home.root, "Other", "Pictures")
        for number, folder in enumerate(rl.FOLDERS):
            rl.make_jpeg(os.path.join(other, folder, "IMG_%d001.jpg" % (number + 1)))
        found = self.verify(other)
        self.assertTrue(found["poor"], found)
        self.assertGreater(found["missing"], 0.05 * found["checked"])
        self.assertIn("another folder", " ".join(found["poor_why"]))

    def test_a_full_run_counts_the_photos_no_row_has_and_ignores_other_files(self):
        rl.make_jpeg(os.path.join(self.side.pictures, rl.FOLDERS[0], "Never indexed.jpg"))
        rl.make_jpeg(os.path.join(self.side.pictures, "Unlisted folder", "Also never.jpg"))
        with open(os.path.join(self.side.pictures, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("not a photo")
        found = self.verify(full=True)
        self.assertEqual(2, found["not_in_library"])
        self.assertEqual("all", found["mode"])
        self.assertEqual(18, found["matches"])
        self.assertIsNone(self.verify()["not_in_library"], "a sample does not walk the place for photos no row has")

    def test_rows_stored_native_where_the_place_would_be_are_warned_of(self):
        """A row kept as this machine spells it, under the place the root would be at: every
        lookup would miss it, and the next index would add the photo again."""
        from tagpup.store import db
        native = os.path.join(self.home.root, "Copy", "Pictures", rl.FOLDERS[0], "Native row.jpg")
        os.makedirs(os.path.dirname(native), exist_ok=True)
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("INSERT INTO photos (path, mtime, size) VALUES (?, 1, 1)", (native,))
            conn.commit()
        finally:
            conn.close()
        found = self.verify(os.path.join(self.home.root, "Copy", "Pictures"))
        self.assertEqual(1, found["native_inside"])
        self.assertTrue(found["poor"])
        self.assertIn("missed", " ".join(found["poor_why"]))

    def test_the_spelling_of_the_place_is_the_same_place(self):
        """Forward slashes and another case spell the folder the photos are in."""
        spelled = self.side.pictures.replace("\\", "/").upper()
        found = self.verify(spelled)
        self.assertEqual(18, found["matches"], found["summary"])
        self.assertEqual(self.side.pictures.upper(), found["location"].upper())
        self.assertNotIn("/", found["location"])

    def test_a_path_that_is_not_absolute_is_refused(self):
        with self.assertRaises(Refused):
            self.verify("Pictures")
        with self.assertRaises(Refused):
            self.verify("\\\\?\\" + self.side.pictures)

    def test_a_place_that_nests_in_another_root_is_refused_by_the_map_rules(self):
        """Another root of the library at the very place: the map would refuse the pair."""
        config.add_machine_root("scans", os.path.join(self.home.root, "Scans"))
        from tagpup.store import db
        from tagpup.store import roots as store_roots
        conn = db.connect(self.side.db_path)
        try:
            store_roots.insert(conn, "scans", "")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(Refused):
            self.verify(os.path.join(self.home.root, "Scans"))

    def test_a_root_the_library_does_not_have_is_not_found(self):
        with self.assertRaises(NotFound):
            roots_verify.verify(self.side.library, "scans", self.side.pictures, self.machine)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ASampleCoversEveryFolder(VerifyCase):
    REAL, BULK = 1, 50

    def test_it_takes_at_least_one_row_from_each_folder_and_the_same_rows_each_time(self):
        """The first rows of a library are in the first folder; a sample of them says nothing of the
        rest. Here 3 folders hold 51 rows each and the sample asks for 6."""
        found = self.verify(sample=6)
        self.assertEqual(3, found["folders"])
        self.assertGreaterEqual(found["checked"], 3)
        self.assertLessEqual(found["checked"], 9)
        self.assertEqual(found["checked"], self.verify(sample=6)["checked"])
        self.assertEqual(153, found["rows"])

    def test_a_sample_that_is_stopped_early_has_looked_at_folders_from_everywhere(self):
        """On a slow share a sample may run out of time: it must not have seen only the first folders."""
        seen = []
        real_list = roots_verify._list_one

        def listing(folder):
            seen.append(folder)
            return real_list(folder)

        with mock.patch.object(roots_verify, "_list_one", listing):
            found = self.verify(cancel=lambda: len(seen) >= 2)
        self.assertEqual("cancelled", found["stopped"])
        everything = sorted(os.path.dirname(path) for path in self.side.real)
        self.assertNotEqual(sorted(set(everything))[:2], sorted(seen), "the sample went through the folders in order")

    def test_more_folders_than_the_sample_still_each_get_a_row(self):
        by_folder = {"f%03d" % n: ("f%03d" % n, [("a", "a", 1, 1), ("b", "b", 1, 1)]) for n in range(40)}
        chosen = roots_verify.sample_of(by_folder, 10)
        self.assertEqual(40, len(chosen))
        self.assertTrue(all(len(rows) >= 1 for rows in chosen.values()))
        everything = roots_verify.sample_of(by_folder, 10_000)
        self.assertEqual(80, sum(len(rows) for rows in everything.values()))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class APlaceThatCannotBeReached(VerifyCase):
    def test_a_drive_that_is_not_connected_is_not_all_missing(self):
        before = self.fingerprint()
        found = self.verify(unused_drive() + "\\Pictures")
        self.assertFalse(found["reachable"])
        self.assertEqual("no_drive", found["state"])
        self.assertEqual((0, 0, 0), (found["checked"], found["missing"], found["matches"]))
        self.assertIn("cannot be reached", found["message"])
        self.assertIn("cannot be reached", found["summary"])
        self.assertTrue(found["poor"])
        self.assertEqual(before, self.fingerprint())

    def test_a_folder_that_does_not_exist_on_a_drive_that_does_is_said_so(self):
        found = self.verify(os.path.join(self.home.root, "Nothing here"))
        self.assertEqual("no_folder", found["state"])
        self.assertEqual(0, found["missing"])
        self.assertIn("does not exist", found["message"])

    def test_a_share_that_does_not_answer_is_away_and_is_asked_once(self):
        """Every look at the disk is on a thread that is waited for a moment: a share that
        hangs is 'away', the page is not held, and a second click does not start another thread
        for the same share."""
        release = threading.Event()
        calls = []

        def hang(location):
            calls.append(location)
            release.wait(30)
            return "ok"

        roots_verify.forget_away()
        self.addCleanup(roots_verify.forget_away)
        self.addCleanup(release.set)
        with mock.patch.object(roots_verify, "_probe", hang):
            started = time.monotonic()
            found = self.verify("\\\\idziserver\\Pictures\\Pictures", seconds=0.3)
            first = time.monotonic() - started
            again = self.verify("\\\\idziserver\\Pictures\\Pictures", seconds=0.3)
            second = time.monotonic() - started - first
        self.assertEqual("away", found["state"])
        self.assertFalse(found["reachable"])
        self.assertEqual(0, found["missing"], "an away share is not 'all missing'")
        self.assertLess(first, 5)
        self.assertEqual("away", again["state"])
        self.assertLess(second, 0.25, "a share already taken as away is not waited for again")
        self.assertEqual(1, len(calls), "a second thread was started for a share that has not answered")

    def test_a_share_that_stops_answering_half_way_says_so_and_counts_nothing_unlooked_at_as_missing(self):
        copy = self.copy_of_pictures()
        real_list = roots_verify._list_one
        release = threading.Event()
        seen = []

        def flaky(folder):
            seen.append(folder)
            if len(seen) > 1:
                release.wait(30)
            return real_list(folder)

        roots_verify.forget_away()
        self.addCleanup(roots_verify.forget_away)
        self.addCleanup(release.set)
        with mock.patch.object(roots_verify, "_list_one", flaky):
            found = self.verify(copy, seconds=0.3)
        self.assertEqual("unreachable", found["stopped"])
        self.assertTrue(found["partial"])
        self.assertEqual(0, found["missing"])
        self.assertEqual(self.REAL, found["checked"], "only the folder that answered was looked at")
        self.assertIn("stopped answering", found["message"])

    def test_cancel_stops_between_folders_and_the_result_says_it_is_partial(self):
        copy = self.copy_of_pictures()
        asked = []
        found = self.verify(copy, full=True, cancel=lambda: bool(asked.append(1)) or len(asked) > 2)
        self.assertEqual("cancelled", found["stopped"])
        self.assertTrue(found["partial"])
        self.assertIsNone(found["not_in_library"], "a cancelled run did not count the photos no row has")
        self.assertIn("Cancelled", found["summary"])

    def test_progress_is_reported_as_it_goes(self):
        seen = []
        self.verify(full=True, progress=lambda checked, rows, folders: seen.append((checked, rows, folders)))
        self.assertTrue(seen)
        self.assertEqual(18, seen[-1][1])
        self.assertEqual(sorted(seen), seen)


class ALibraryWithNoRoots(unittest.TestCase):
    def test_a_library_that_has_not_adopted_a_root_is_told_so(self):
        home = own_home.for_test(self, prefix="roots_verify_none_")
        side = rl.Side(home, "plain", real=1, bulk=0, outside=0)
        with self.assertRaises(NotFound):
            roots_verify.verify(side.library, "pictures", side.pictures, rl.machine())


if __name__ == "__main__":
    unittest.main()

"""Verify also reads the markers (`.tagpup`) of a root's marked folders at the place it is asked about, and Change
location refuses a place whose markers differ (tagpup.services.roots_verify, docs/ARCHITECTURE.md, "Roots and machines"
and "Folder ids"; finding #984).

The library is made as the roots tests make it (tests/roots_library.py: rows the indexer's code makes, adopted by the
explicit command, so the rows are `@pictures/...` and so are the `folder_ids` rows the command `folder-ids mark` then
records), the markers are written by that command, and the places are real folders: the one the photos are in, a copy
(which keeps the markers), another folder, a drive that is not connected, a share that stops answering.
"""
import json
import os
import shutil
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.files import folder_marker  # noqa: E402
from tagpup.services import folder_ids, roots_location, roots_verify  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import folder_ids as store  # noqa: E402

WINDOWS = os.name == "nt"
OTHER_LIBRARY = "11111111-2222-4333-8444-555555555555"
OTHER_FOLDER = "66666666-7777-4888-9999-aaaaaaaaaaaa"
LEAVES = len(rl.FOLDERS)


def overwrite(path, data):
    with open(path, "r+b" if os.path.exists(path) else "wb") as handle:
        handle.truncate(0)
        handle.write(data)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class MarkedCase(unittest.TestCase):
    MARK = True

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_markers_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.machine = roots_service.Machine(config.machine_roots, config.add_machine_root, config.machine_roots_path,
                                             config.set_location, config.change_back)
        self.library = self.side.library
        if self.MARK:
            result = folder_ids.mark(self.library, apply=True)
            self.assertIsNone(result.refused, result.refused)

    def verify(self, location=None, **kwargs):
        return roots_verify.verify(self.library, "pictures", location or self.side.pictures, self.machine, **kwargs)

    def copy_of_pictures(self, name="Copy"):
        target = os.path.join(self.home.root, name, "Pictures")
        shutil.copytree(self.side.pictures, target, copy_function=shutil.copy2)
        return target

    def marker_in(self, place, number=0):
        return os.path.join(place, rl.FOLDERS[number])

    def library_line(self, place, number=0):
        return folder_marker.read(self.marker_in(place, number)).entries[0]

    def move(self, location, **kwargs):
        return roots_location.change_location(self.library, "pictures", location, self.machine, **kwargs)

    def places(self):
        return list(config.machine_roots()["pictures"])


class WhatItCounts(MarkedCase):
    def test_the_rows_are_the_adopted_shape_the_real_library_holds(self):
        _identity, held = store.held(self.side.db_path)
        under = [path for _id, path in held if path.startswith("@pictures/")]
        self.assertEqual(LEAVES, len(under), held)
        self.assertTrue(all("\\" not in path for path in under), under)

    def test_the_place_the_photos_are_in_matches_in_every_marked_folder(self):
        found = self.verify()
        marks = found["markers"]
        self.assertEqual((LEAVES, LEAVES, LEAVES, 0, 0), (marks["rows"], marks["checked"], marks["match"],
                                                           marks["differs"], marks["unmarked"]))
        self.assertEqual("%d of %d marked folders match; 0 differ; 0 not marked" % (LEAVES, LEAVES), marks["line"])
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertNotIn("marked folders", found["summary"], "the line is its own, shown beside the summary")

    def test_a_copy_keeps_the_markers_and_matches(self):
        copy = self.copy_of_pictures()
        self.assertEqual(LEAVES, self.verify(copy)["markers"]["match"])

    def test_nothing_is_written_not_a_marker_a_row_or_the_map(self):
        copy = self.copy_of_pictures()
        def fingerprint():
            with open(config.machine_roots_path(), "rb") as handle:
                the_map = handle.read()
            return (json.dumps(self.side.dump(), default=repr, sort_keys=True), the_map,
                    sorted(os.listdir(self.marker_in(copy))))

        before = fingerprint()
        self.verify(copy, full=True)
        self.verify(copy)
        self.assertEqual(before, fingerprint())

    def test_a_marker_of_another_id_differs_and_makes_the_result_poor(self):
        copy = self.copy_of_pictures()
        library_id, _folder_id = self.library_line(copy)
        overwrite(os.path.join(self.marker_in(copy), ".tagpup"), ("%s %s\n" % (library_id, OTHER_FOLDER)).encode())
        found = self.verify(copy)
        self.assertEqual((LEAVES - 1, 1), (found["markers"]["match"], found["markers"]["differs"]))
        self.assertTrue(found["poor"])
        self.assertTrue(any("marker of a different folder" in why for why in found["poor_why"]), found["poor_why"])
        for text in found["poor_why"] + [found["markers"]["line"]]:
            self.assertNotIn(self.home.root, text, "a path in a sentence about markers")

    def test_a_marker_of_another_library_alone_differs(self):
        copy = self.copy_of_pictures()
        overwrite(os.path.join(self.marker_in(copy), ".tagpup"), ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode())
        self.assertEqual(1, self.verify(copy)["markers"]["differs"])

    def test_another_librarys_line_beside_ours_is_fine(self):
        copy = self.copy_of_pictures()
        path = os.path.join(self.marker_in(copy), ".tagpup")
        with open(path, "rb") as handle:
            ours = handle.read()
        overwrite(path, ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode() + ours)
        found = self.verify(copy)
        self.assertEqual((LEAVES, 0), (found["markers"]["match"], found["markers"]["differs"]))
        self.assertFalse(found["poor"], found["poor_why"])

    def test_a_hand_edited_marker_is_counted_and_never_a_reason_to_refuse(self):
        copy = self.copy_of_pictures()
        overwrite(os.path.join(self.marker_in(copy), ".tagpup"), b"my notes about this folder\n")
        found = self.verify(copy)
        marks = found["markers"]
        self.assertEqual((1, 0, LEAVES - 1), (marks["malformed"], marks["differs"], marks["match"]))
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertIn("1 with a marker that is not readable as one", marks["line"])
        self.assertIsNone(self.move(copy).refused)

    def test_a_folder_with_no_marker_is_not_marked_and_fine(self):
        copy = self.copy_of_pictures()
        os.remove(os.path.join(self.marker_in(copy), ".tagpup"))
        found = self.verify(copy)
        self.assertEqual((1, 0), (found["markers"]["unmarked"], found["markers"]["differs"]))
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertIn("1 not marked", found["markers"]["line"])

    def test_a_marked_folder_that_is_not_at_the_place_is_not_there_and_not_a_difference(self):
        copy = self.copy_of_pictures()
        shutil.rmtree(self.marker_in(copy))
        found = self.verify(copy)
        self.assertEqual((1, 0, LEAVES - 1), (found["markers"]["not_there"], found["markers"]["differs"],
                                              found["markers"]["match"]))
        self.assertFalse(any("marker" in why for why in found["poor_why"]))

    def test_a_marker_the_disk_refuses_is_unreadable(self):
        real = folder_marker.read
        refused = folder_marker.Marker(folder_marker.UNREADABLE, error="PermissionError")
        with mock.patch.object(folder_marker, "read", lambda folder: refused if "Harbour" in folder else real(folder)):
            found = self.verify()
        self.assertEqual((1, 0), (found["markers"]["unreadable"], found["markers"]["differs"]))
        self.assertFalse(found["poor"], found["poor_why"])

    def test_a_folder_stat_the_disk_refuses_is_unreadable_not_all_differ(self):
        with mock.patch.object(roots_verify.os, "stat", side_effect=PermissionError("denied")):
            marks = roots_verify._markers(self.library, "pictures", roots_verify.candidate_roots(
                self.library, "pictures", self.side.pictures, self.machine)[0], self.side.pictures, True,
                roots_verify._Tally(), lambda: False, lambda *a: None, None, 5)
        self.assertEqual((LEAVES, 0), (marks["unreadable"], marks["differs"]))

    def test_a_root_with_two_places_reads_only_the_candidate(self):
        copy = self.copy_of_pictures()
        self.machine.set_location("pictures", copy, must_exist=False)
        self.assertEqual(2, len(self.places()))
        overwrite(os.path.join(self.marker_in(copy), ".tagpup"), ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode())
        self.assertEqual(LEAVES, self.verify(self.side.pictures)["markers"]["match"], "the map's other place was read")
        self.assertEqual(1, self.verify(copy)["markers"]["differs"])

    def test_a_folder_that_is_a_copy_with_another_marker_in_it_differs(self):
        """A copy of a marked folder keeps its marker (it matches: the same id, however many places); a folder that
        holds the marker of another folder is the difference."""
        copy = self.copy_of_pictures()
        donor = self.marker_in(copy, 1)
        shutil.copy2(os.path.join(donor, ".tagpup"), os.path.join(self.marker_in(copy, 0), ".tagpup"))
        found = self.verify(copy)
        self.assertEqual((LEAVES - 1, 1), (found["markers"]["match"], found["markers"]["differs"]))


class ReachingTheLocation(MarkedCase):
    def test_a_place_that_cannot_be_reached_has_no_markers_and_is_not_all_differ(self):
        letter = next(each + ":" for each in "QRSTUVWXYZ" if not os.path.exists(each + ":\\"))
        found = self.verify(letter + "\\Pictures")
        self.assertFalse(found["reachable"])
        self.assertIsNone(found["markers"])
        self.assertFalse(any("marker" in why for why in found["poor_why"]))

    def test_a_share_that_stops_answering_while_markers_are_read_says_so(self):
        copy = self.copy_of_pictures()
        release = threading.Event()
        seen = []
        real = roots_verify._marker_state

        def flaky(folder, folder_id, library_id):
            seen.append(folder)
            if len(seen) > 1:
                release.wait(30)
            return real(folder, folder_id, library_id)

        roots_verify.forget_away()
        self.addCleanup(roots_verify.forget_away)
        self.addCleanup(release.set)
        with mock.patch.object(roots_verify, "_marker_state", flaky):
            found = self.verify(copy, seconds=0.3)
        self.assertEqual("unreachable", found["stopped"])
        self.assertEqual((1, 0), (found["markers"]["checked"], found["markers"]["differs"]))
        self.assertTrue(found["partial"])

    def test_a_cancel_part_way_keeps_what_was_counted(self):
        reads = []
        real = roots_verify._marker_state

        def counting(folder, folder_id, library_id):
            reads.append(folder)
            return real(folder, folder_id, library_id)

        with mock.patch.object(roots_verify, "_marker_state", counting):
            found = self.verify(full=True, cancel=lambda: len(reads) >= 1)
        self.assertEqual("cancelled", found["stopped"])
        self.assertEqual(1, found["markers"]["checked"])
        self.assertTrue(found["partial"])

    def test_cancel_before_the_markers_are_read_leaves_none(self):
        found = self.verify(full=True, cancel=lambda: True)
        self.assertEqual("cancelled", found["stopped"])
        self.assertIsNone(found["markers"])

    def test_a_sample_reads_at_most_its_bound_and_each_folder_once(self):
        reads = []
        real = folder_marker.read
        with mock.patch.object(roots_verify, "MARKER_SAMPLE", 2), \
                mock.patch.object(folder_marker, "read", lambda folder: reads.append(folder) or real(folder)):
            found = self.verify()
        self.assertEqual(2, found["markers"]["checked"])
        self.assertEqual(2, len(set(reads)), reads)
        self.assertEqual(LEAVES, found["markers"]["rows"])

    def test_a_full_run_reads_every_marked_folder_and_reports_progress(self):
        seen = []
        found = self.verify(full=True, progress=lambda checked, rows, folders: seen.append(folders))
        self.assertEqual(LEAVES, found["markers"]["checked"])
        self.assertEqual(sorted(seen), seen)

    def test_a_sample_that_is_out_of_time_says_what_it_has(self):
        with mock.patch.object(roots_verify, "MARKER_BUDGET", 0):
            found = self.verify(budget=100)
        self.assertEqual(0, found["markers"]["checked"])
        self.assertTrue(found["markers"]["partial"])
        self.assertIsNone(found["stopped"], "a sample's own bound is not a failed run")


class ChangingTheLocation(MarkedCase):
    def test_a_place_whose_markers_differ_is_refused_unless_the_owner_overrides(self):
        copy = self.copy_of_pictures()
        overwrite(os.path.join(self.marker_in(copy), ".tagpup"), ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode())
        refused = self.move(copy, apply=True)
        self.assertIn("marker of a different folder", refused.refused)
        self.assertEqual([self.side.pictures], self.places())
        allowed = self.move(copy, apply=True, override=True)
        self.assertIsNone(allowed.refused, allowed.refused)
        self.assertEqual([copy, self.side.pictures], self.places())

    def test_a_place_whose_markers_match_or_are_absent_proceeds_as_before(self):
        copy = self.copy_of_pictures()
        self.assertIsNone(self.move(copy).refused)
        for number in range(LEAVES):
            os.remove(os.path.join(self.marker_in(copy, number), ".tagpup"))
        result = self.move(copy, apply=True)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual([copy, self.side.pictures], self.places())

    def test_the_run_is_logged_with_the_marker_counts(self):
        copy = self.copy_of_pictures()
        answer = roots_location.run_verify(self.library, "pictures", copy, self.machine)
        last = roots_location.last_verify(self.library, "pictures")
        self.assertEqual((answer["markers"]["checked"], LEAVES, 0), (last["marked"], last["marks_match"], last["marks_differ"]))
        run = roots_location.job_runs.runs(self.side.db_path, roots_location.VERIFY_JOB % "pictures", 1)[0]
        self.assertIn(answer["markers"]["line"], run.changed["what"])


class NothingMarked(MarkedCase):
    MARK = False

    def test_a_library_that_marked_nothing_says_nothing_extra(self):
        found = self.verify()
        self.assertIsNone(found["markers"])
        self.assertNotIn("marked folders", found["summary"])
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertIsNone(self.move(self.copy_of_pictures()).refused)

    def test_a_library_behind_migration_26_has_no_tables_and_does_not_fail(self):
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS folder_ids")
            conn.execute("DROP TABLE IF EXISTS library_identity")
            conn.commit()
        finally:
            conn.close()
        found = self.verify(full=True)
        self.assertIsNone(found["markers"])
        self.assertFalse(found["poor"], found["poor_why"])

    def test_tables_that_hold_nothing_say_nothing(self):
        self.assertEqual((None, []), store.held(self.side.db_path))
        self.assertIsNone(self.verify()["markers"])


if __name__ == "__main__":
    unittest.main()

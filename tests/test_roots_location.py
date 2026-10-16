"""Moving a root on this machine: the map's writer (tagpup.config.set_location) and the
service that asks first (tagpup.services.roots_location; docs/ARCHITECTURE.md, "Roots and machines",
TagTuner's Roots).

The edit is the map's alone: never a row. A dry run shows what a sample of the new place holds; a poor
result is refused unless the request says override; a place that is not there, a drive that is not
connected, one over another root, are refused whatever is said; while anything of the library is running
or queued nothing moves; two tabs and a double click move it once; a map edited by hand while the owner
looked is not overwritten.

Lifted: test_roots_what_the_owner_is_told (the stage 3 review's #466-#474, each with the case that found it): what a
verify of a copy says before a move (#466, #469), what the refusal and the dialog say a move can and cannot see (#467),
what a write stopped half-way says it wrote (#468), a library behind the schema (#470), only the current and previous
place kept (#472), one owner of "a share is away" (#474). Its unplaced-root check results (#473) went to
test_roots_unplaced; its test that roots_verify holds no thread of its own read source text and is gone.
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
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
import share_waits  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.result import NotFound  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import activity, file_changes, roots_location, roots_verify, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import job_runs as store_job_runs  # noqa: E402

WINDOWS = os.name == "nt"


def machine():
    from tagpup.services import roots as roots_service
    return roots_service.Machine(config.machine_roots, config.add_machine_root, config.machine_roots_path,
                                 config.set_location)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheMapsWriter(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_set_")
        self.old = os.path.join(self.home.root, "Old", "Pictures")
        self.new = os.path.join(self.home.root, "New", "Pictures")
        for folder in (self.old, self.new):
            os.makedirs(folder)
        config.add_machine_root("pictures", self.old)
        self.file = config.machine_roots_path()

    def places(self):
        return list(config.machine_roots()["pictures"])

    def test_the_new_place_comes_first_and_the_old_is_kept_after_it(self):
        found = config.set_location("pictures", self.new)
        self.assertTrue(found["changed"])
        self.assertEqual([self.new, self.old], self.places())
        self.assertEqual([self.old], found["previous"])
        self.assertEqual([], [n for n in os.listdir(self.home.root) if n.endswith((".tmp", ".lock"))])

    def test_the_same_place_in_another_spelling_changes_nothing_and_writes_nothing(self):
        before = open(self.file, "rb").read()
        stamp = os.stat(self.file).st_mtime_ns
        for spelled in (self.old.upper(), self.old.replace("\\", "/"), self.old + "\\"):
            found = config.set_location("pictures", spelled)
            self.assertFalse(found["changed"], spelled)
        self.assertEqual(before, open(self.file, "rb").read())
        self.assertEqual(stamp, os.stat(self.file).st_mtime_ns)

    def test_a_place_spelled_with_forward_slashes_is_written_the_way_the_map_spells_places(self):
        config.set_location("pictures", self.new.replace("\\", "/"))
        self.assertEqual(self.new, self.places()[0])

    def test_what_cannot_be_a_place_is_refused_and_nothing_is_written(self):
        before = open(self.file, "rb").read()
        other = os.path.join(self.home.root, "Scans")
        os.makedirs(other)
        config.add_machine_root("scans", other)
        before = open(self.file, "rb").read()
        for bad, word in (("Pictures", "absolute"), ("\\\\?\\" + self.new, "prefix"),
                          (os.path.join(self.home.root, "Not there"), "exists"), (other, "scans"),
                          (os.path.join(self.old, "Inside"), "nest")):
            if word == "nest":
                os.makedirs(bad)
            with self.assertRaises(config.MachineMapError, msg=bad):
                config.set_location("pictures", bad)
        self.assertEqual(before, open(self.file, "rb").read())

    def test_a_map_changed_since_the_caller_looked_is_not_overwritten(self):
        """A hand edit, or the other tab, between the dry run and Confirm."""
        config.set_location("pictures", self.new)      # what the other tab did
        third = os.path.join(self.home.root, "Third")
        os.makedirs(third)
        with self.assertRaises(config.MachineMapError) as why:
            config.set_location("pictures", third, expected=self.old)
        self.assertIn("has changed since you looked", str(why.exception))
        self.assertEqual([self.new, self.old], self.places())

    def test_a_map_that_is_not_valid_is_not_written_over(self):
        with open(self.file, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        with self.assertRaises(config.MachineMapError):
            config.set_location("pictures", self.new)
        self.assertEqual("{ not json", open(self.file, encoding="utf-8").read())

    def test_two_editors_at_once_leave_one_valid_map_with_both_places_recognised(self):
        places = []
        for n in range(6):
            places.append(os.path.join(self.home.root, "P%d" % n))
            os.makedirs(places[-1])
        gate, failures = threading.Barrier(len(places)), []

        def move(place):
            gate.wait()
            try:
                config.set_location("pictures", place)
            except BaseException as problem:
                failures.append(problem)

        threads = [threading.Thread(target=move, args=(place,)) for place in places]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([], failures)
        listed = self.places()
        self.assertEqual(2, len(listed), "only the current and the previous place are kept (#472)")
        self.assertIn(listed[0], places + [self.old])
        json.loads(open(self.file, encoding="utf-8").read())


class RootsCase(unittest.TestCase):
    REAL = 6
    OUTSIDE = 2

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_move_")
        self.side = rl.Side(self.home, "photo_index", real=self.REAL, bulk=0, outside=self.OUTSIDE)
        self.assertIsNone(self.side.adopt().refused)
        self.machine = machine()
        self.library = self.side.library
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)

    def move(self, location=None, **kwargs):
        return roots_location.change_location(self.library, "pictures", location or self.copy, self.machine,
                                              **kwargs)

    def places(self):
        return list(config.machine_roots()["pictures"])

    def rows_dump(self):
        """Every row of every table but the run log (which records the change itself)."""
        return json.dumps(self.side.dump(leave_out=("job_runs",)), default=repr, sort_keys=True)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ChangingTheLocation(RootsCase):
    def test_a_dry_run_shows_what_the_new_place_holds_and_writes_nothing(self):
        before, map_before = self.rows_dump(), open(config.machine_roots_path(), "rb").read()
        result = self.move()
        self.assertIsNone(result.refused, result.refused)
        self.assertTrue(result.details["dry_run"])
        self.assertEqual(18, result.details["verify"]["matches"])
        self.assertEqual(0, result.changed)
        self.assertEqual(before, self.rows_dump())
        self.assertEqual(map_before, open(config.machine_roots_path(), "rb").read())
        self.assertEqual([], [run for run in store_job_runs.runs(self.side.db_path, None, 10)
                              if run.job.startswith("change location")], "a dry run is not a change to log")

    def test_applied_it_edits_the_map_only_and_no_row_changes(self):
        before = self.rows_dump()
        result = self.move(apply=True)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(1, result.changed)
        self.assertEqual([self.copy, self.side.pictures], self.places())
        self.assertEqual(before, self.rows_dump(), "a row changed: moving a root is an edit of the map")
        self.assertIn("written to files at %s" % self.copy, roots_location.overview(self.library, self.machine)[
            "roots"][0]["writes_to"])

    def test_it_is_logged_for_the_activity_page(self):
        self.move(apply=True)
        entries = [entry for entry in activity.timeline(self.library) if entry["kind"] == "job"]
        said = [entry["what"] for entry in entries]
        self.assertTrue(any(text.startswith("moved root pictures to %s" % self.copy) for text in said), said)
        self.assertEqual("done", [e for e in entries if e["what"].startswith("moved")][0]["outcome"])

    def test_a_refusal_is_not_logged_as_a_change(self):
        other = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(other)
        result = self.move(other, apply=True)
        self.assertTrue(result.refused)
        self.assertEqual([], [run for run in store_job_runs.runs(self.side.db_path, None, 10)
                              if run.job.startswith("change location")])

    def test_a_place_that_matches_few_rows_is_refused_unless_overridden(self):
        other = os.path.join(self.home.root, "Other", "Pictures")
        rl.make_jpeg(os.path.join(other, rl.FOLDERS[0], "IMG_1001.jpg"))
        before = self.rows_dump()
        refused = self.move(other, apply=True)
        self.assertIn("another folder", refused.refused)
        self.assertEqual([self.side.pictures], self.places())
        self.assertEqual(before, self.rows_dump())
        allowed = self.move(other, apply=True, override=True)
        self.assertIsNone(allowed.refused, allowed.refused)
        self.assertEqual([other, self.side.pictures], self.places())
        self.assertEqual(before, self.rows_dump())

    def test_a_drive_that_is_not_connected_is_refused_even_with_override(self):
        letter = next(l + ":" for l in "QRSTUVWXYZ" if not os.path.exists(l + ":\\"))
        result = self.move(letter + "\\Pictures", apply=True, override=True)
        self.assertIn("cannot be reached", result.refused)
        self.assertEqual([self.side.pictures], self.places())

    def test_a_share_that_does_not_answer_is_refused_unless_overridden_and_is_not_waited_for(self):
        release = threading.Event()
        roots_verify.forget_away()
        self.addCleanup(roots_verify.forget_away)
        self.addCleanup(release.set)
        share = "\\\\idziserver\\Pictures\\Pictures"
        with mock.patch.object(roots_verify, "_probe", lambda location: release.wait(30) or "ok"):
            started = time.monotonic()
            refused = self.move(share, apply=True, seconds=0.3)
            self.assertLess(time.monotonic() - started, 10)
            self.assertIn("cannot be reached", refused.refused)
            self.assertIn("Say override", refused.refused)
            self.assertEqual([self.side.pictures], self.places())
            allowed = self.move(share, apply=True, override=True, seconds=0.3)
        self.assertIsNone(allowed.refused, allowed.refused)
        self.assertEqual(share, self.places()[0])

    def test_a_place_over_or_inside_another_root_of_the_library_is_refused(self):
        """The same photos reachable under two roots: a path would be held by the one and looked up
        by the other."""
        from tagpup.store import roots as store_roots
        scans = os.path.join(self.home.root, "Scans")
        os.makedirs(os.path.join(scans, "Sub"))
        config.add_machine_root("scans", scans)
        conn = db.connect(self.side.db_path)
        try:
            store_roots.insert(conn, "scans", "")
            conn.commit()
        finally:
            conn.close()
        for place in (os.path.join(scans, "Sub"), self.home.root):
            result = self.move(place, apply=True, override=True)
            self.assertIn("two roots", result.refused or "", place)
        self.assertEqual([self.side.pictures], self.places())

    def test_the_same_place_again_is_not_a_change(self):
        self.move(apply=True)
        again = self.move(self.copy.upper().replace("\\", "/"), apply=True)
        self.assertIsNone(again.refused)
        self.assertTrue(again.details["unchanged"])
        self.assertEqual(0, again.changed)
        self.assertEqual([self.copy, self.side.pictures], self.places())
        moved = [r for r in store_job_runs.runs(self.side.db_path, None, 10) if r.job.startswith("change location")]
        self.assertEqual(1, len(moved), "one change is one entry on the Activity page")

    def test_a_double_click_of_confirm_moves_it_once(self):
        gate, results = threading.Barrier(2), []

        def confirm():
            gate.wait()
            results.append(self.move(apply=True, expected=self.side.pictures))

        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(r.changed for r in results), [r.refused or r.details.get("message") for r in results])
        self.assertEqual([self.copy, self.side.pictures], self.places())
        moved = [r for r in store_job_runs.runs(self.side.db_path, None, 10) if r.job.startswith("change location")]
        self.assertEqual(1, len([r for r in moved if r.outcome == "done" and r.changed.get("what", "").startswith("moved")]))

    def test_a_second_tab_that_looked_before_the_first_changed_it_is_told(self):
        """Two tabs saw the root at the old place and chose two new ones: one wins, the other is told
        what the map says now, and nothing it chose is written."""
        third = os.path.join(self.home.root, "Third", "Pictures")
        shutil.copytree(self.side.pictures, third, copy_function=shutil.copy2)
        gate, results = threading.Barrier(2), {}

        def choose(where):
            gate.wait()
            results[where] = self.move(where, apply=True, expected=self.side.pictures)

        threads = [threading.Thread(target=choose, args=(where,)) for where in (self.copy, third)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        winners = [where for where, r in results.items() if r.changed]
        losers = [where for where, r in results.items() if r.refused]
        self.assertEqual(1, len(winners), {w: (r.refused, r.changed) for w, r in results.items()})
        self.assertEqual(1, len(losers))
        self.assertEqual([winners[0], self.side.pictures], self.places())
        self.assertTrue(any(word in results[losers[0]].refused for word in ("changed since", "under way")))

    def test_the_map_edited_by_hand_while_the_owner_looked_is_not_overwritten(self):
        hand = os.path.join(self.home.root, "Hand", "Pictures")
        os.makedirs(hand)
        dry = self.move()
        self.assertIsNone(dry.refused)
        config.set_location("pictures", hand)         # edited by hand after the dry run
        before = open(config.machine_roots_path(), "rb").read()
        result = self.move(apply=True, expected=self.side.pictures)
        self.assertIn("changed since you looked", result.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())

    def test_nothing_moves_while_something_of_the_library_is_queued_or_running(self):
        before = open(config.machine_roots_path(), "rb").read()
        result = self.move(apply=True, busy=lambda: ["an index run is queued"])
        self.assertIn("an index run is queued", result.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())
        # A job's own run in the library, which any process may have begun.
        claim = store_job_runs.claim(self.side.db_path, "sync", self.library.name, time.time())
        self.assertTrue(claim)
        running = self.move(apply=True)
        self.assertIn("sync", running.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())
        store_job_runs.finish(self.side.db_path, claim.run_id, "done", time.time(), {})
        self.assertIsNone(self.move(apply=True).refused)

    def test_a_dry_run_is_allowed_while_something_runs(self):
        result = self.move(apply=False, busy=lambda: ["an index run is queued"])
        self.assertIsNone(result.refused)

    def test_a_run_whose_process_ended_does_not_block_a_change(self):
        claim = store_job_runs.claim(self.side.db_path, "sync", self.library.name, time.time())
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(
            "UPDATE job_runs SET owner = ? WHERE id = ?", ("%s:999999:1" % __import__("socket").gethostname(),
                                                           claim.run_id)))
        self.assertIsNone(self.move(apply=True).refused)

    def test_a_running_server_reads_the_new_place_without_a_restart(self):
        """A connection held open -- as the server's are -- converts by the map it holds;
        a moved map is found within a second, and the same row then names the file at the new place."""
        from tagpup.store import photos as store_photos
        from tagpup.store import roots as store_roots
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        self.addCleanup(conn.close)
        first = sorted(path for _id, path, _m, _s in store_photos.stamps(conn))
        self.assertTrue(all(path.startswith(self.side.pictures) for path in first[:18] if "Elsewhere" not in path))
        self.move(apply=True)
        time.sleep(store_roots.RECHECK_SECONDS + 0.2)
        again = sorted(path for _id, path, _m, _s in store_photos.stamps(conn))
        rooted = [path for path in again if "Elsewhere" not in path]
        self.assertTrue(rooted and all(path.startswith(self.copy) for path in rooted), rooted[:2])

    def test_a_unc_spelling_of_a_place_is_a_place_and_the_drive_letter_one_is_kept_after_it(self):
        """The same folder by its administrative share, as a server's share is reached: another
        spelling of the place is another place of the map, and the old is kept after it."""
        drive, rest = os.path.splitdrive(self.copy)
        unc = "\\\\localhost\\%s$%s" % (drive[0], rest)
        if not os.path.isdir(unc):
            self.skipTest("this machine has no administrative share for its drive")
        result = self.move(unc, apply=True)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(18, result.details["verify"]["matches"])
        self.assertEqual([unc, self.side.pictures], self.places())

    def test_the_generations_every_cache_of_paths_is_keyed_by_move_with_the_map(self):
        from tagpup.store import generations
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        self.addCleanup(conn.close)
        before = generations.values(conn)
        self.move(apply=True)
        time.sleep(1.2)
        after = generations.values(conn)
        self.assertNotEqual(before[:2], after[:2], "a cache of this machine's paths would not be built again")
        self.assertEqual(before[2], after[2], "the tag tree did not move")

    def test_the_count_of_a_roots_rows_is_one_range_of_the_path_index(self):
        from tagpup.store import roots as store_roots
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        self.addCleanup(conn.close)
        where, params = store_roots.sql_under(conn, "path", self.side.pictures)
        plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM photos WHERE " + where,
                                                       params))
        self.assertIn("SEARCH", plan)
        self.assertNotIn("SCAN", plan)

    def test_change_location_of_a_root_the_library_does_not_have(self):
        with self.assertRaises(NotFound):
            roots_location.change_location(self.library, "scans", self.copy, self.machine)

    def test_a_library_with_no_roots_answers_cleanly(self):
        plain = rl.Side(self.home, "plain", real=1, bulk=0, outside=0)
        found = roots_location.overview(plain.library, self.machine)
        self.assertEqual([], found["roots"])
        with self.assertRaises(NotFound):
            roots_location.change_location(plain.library, "pictures", self.copy, self.machine)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TwoLibrariesHoldingOneFolder(RootsCase):
    """The machine's map is one for every library of the home: two libraries over one folder, each
    with a root called pictures, move together."""

    def setUp(self):
        super().setUp()
        self.second = rl.Side(self.home, "second", real=2, bulk=0, outside=0, share=self.side)
        self.assertIsNone(self.second.adopt().refused)

    def test_each_says_who_else_uses_the_root(self):
        mine = roots_location.overview(self.library, self.machine, [self.second.library])["roots"][0]
        theirs = roots_location.overview(self.second.library, self.machine, [self.library])["roots"][0]
        self.assertEqual(["second"], mine["shared_with"])
        self.assertEqual([self.library.name], theirs["shared_with"])
        alone = roots_location.overview(self.library, self.machine)["roots"][0]
        self.assertEqual([], alone["shared_with"])

    def test_a_run_in_the_other_library_holds_the_move_up_and_is_named(self):
        claim = store_job_runs.claim(self.second.db_path, "sync", self.second.library.name, time.time())
        self.assertTrue(claim)
        before = open(config.machine_roots_path(), "rb").read()
        result = self.move(apply=True, others=[self.second.library])
        self.assertIn("in second", result.refused)
        self.assertIn("uses this root too", result.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())
        store_job_runs.finish(self.second.db_path, claim.run_id, "done", time.time(), {})
        self.assertIsNone(self.move(apply=True, others=[self.second.library]).refused)

    def test_the_move_reaches_both_and_changes_no_row_of_either(self):
        before = (self.rows_dump(), json.dumps(self.second.dump(leave_out=("job_runs",)), default=repr, sort_keys=True))
        result = self.move(apply=True, others=[self.second.library])
        self.assertEqual(["second"], result.details["shared_with"])
        self.assertEqual(self.copy, roots_location.overview(self.second.library, self.machine)["roots"][0]["active"])
        after = (self.rows_dump(), json.dumps(self.second.dump(leave_out=("job_runs",)), default=repr, sort_keys=True))
        self.assertEqual(before, after)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheOverview(RootsCase):
    def test_each_root_says_where_it_is_kept_and_where_writes_go(self):
        self.move(apply=True)
        found = roots_location.overview(self.library, self.machine)
        entry = found["roots"][0]
        self.assertEqual("pictures", entry["name"])
        self.assertEqual([self.copy, self.side.pictures], entry["places"])
        self.assertEqual(self.copy, entry["active"])
        self.assertEqual(self.side.pictures, entry["previous"])
        self.assertEqual("Tags and renames are written to files at %s." % self.copy, entry["writes_to"])
        self.assertEqual(18, entry["rows"])
        self.assertTrue(entry["mapped"])

    def test_a_root_this_machine_does_not_place_says_so(self):
        os.remove(config.machine_roots_path())
        found = roots_location.overview(self.library, self.machine)
        entry = found["roots"][0]
        self.assertFalse(entry["mapped"])
        self.assertIsNone(entry["active"])
        self.assertIsNone(entry["writes_to"])
        self.assertIsNone(entry["rows"])

    def test_a_map_that_cannot_be_read_is_a_sentence_not_a_traceback(self):
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        found = roots_location.overview(self.library, self.machine)
        self.assertIn("machine_roots.json", found["problem"])
        self.assertEqual("pictures", found["roots"][0]["name"])


class BeforeAMove(RootsCase):
    OUTSIDE = 0

    def verify(self, location, **kwargs):
        return roots_verify.verify(self.library, "pictures", location, self.machine, **kwargs)


class AStaleCopy(BeforeAMove):
    def make_stale(self):
        for folder, _dirs, files in os.walk(self.copy):
            for name in files:
                with open(os.path.join(folder, name), "ab") as handle:
                    handle.write(b"\x00" * 5)       # other size: an older or edited copy

    def test_a_copy_that_differs_in_most_rows_is_poor_and_says_what_sync_does(self):          # #466
        self.make_stale()
        found = self.verify(self.copy)
        self.assertEqual((0, 18, 0), (found["matches"], found["differs"], found["missing"]))
        self.assertTrue(found["poor"])
        said = " ".join(found["poor_why"])
        self.assertIn("18 of the 18 rows", said)
        self.assertIn("re-reads those rows from the files there", said)
        self.assertIn("tag newer in a row than in its file is replaced", found["summary"])
        self.assertIn("18 differ", found["summary"])

    def test_it_is_refused_unless_overridden_and_the_map_is_untouched(self):                  # #466
        self.make_stale()
        before = open(config.machine_roots_path(), "rb").read()
        refused = self.move(self.copy, apply=True)
        self.assertIn("older or edited copy", refused.refused)
        self.assertIn("Say override", refused.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())
        self.assertIsNone(self.move(self.copy, apply=True, override=True).refused)

    def test_a_few_differing_files_are_not_poor(self):                                        # #466
        with open(os.path.join(self.copy, rl.FOLDERS[0], "IMG_1001.jpg"), "ab") as handle:
            handle.write(b"\x00")
        found = self.verify(self.copy)
        self.assertEqual(1, found["differs"])
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertIn("Sync re-reads", found["summary"], "the dry run says what sync does with the one that differs")


class ARowTheIndexNeverRead(BeforeAMove):
    def test_it_is_counted_apart_from_differs(self):                                          # #469
        extra = os.path.join(self.side.pictures, "Suggest saw this.jpg")
        rl.make_jpeg(extra)
        shutil.copy2(extra, os.path.join(self.copy, "Suggest saw this.jpg"))
        conn = db.connect(self.side.db_path)
        try:
            photo_rows.add_unread(conn, extra)
            conn.commit()
        finally:
            conn.close()
        found = self.verify(self.copy)
        self.assertEqual((18, 0, 1, 0), (found["matches"], found["differs"], found["unread"], found["missing"]))
        self.assertIn("1 never read by the index (sync reads them)", found["summary"])
        self.assertIn("0 differ", found["summary"])
        self.assertFalse(found["poor"])


class WhatTheOwnerIsToldBeforeAMove(BeforeAMove):
    def test_the_refusal_names_what_tagtuner_cannot_see(self):                                # #467
        result = self.move(self.copy, apply=True, busy=lambda: ["an index run is running or queued"])
        self.assertIn("an index run is running or queued", result.refused)
        self.assertIn("TagTuner sees only its own runs", result.refused)
        self.assertIn("stop Suggest in TagPup", result.refused)
        self.assertIn("a sync", result.refused)

    def test_the_architecture_does_not_claim_more(self):                                      # #467
        # "Roots and machines" moved verbatim to docs/history/ when ARCHITECTURE.md became a map
        text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "history",
                                 "ARCHITECTURE_roots_and_markers.md"), encoding="utf-8").read()
        self.assertNotIn("refused **while anything of the library is running or queued**", text)
        self.assertIn("TagTuner's own runs", text)


class AWriteStoppedHalfWay(BeforeAMove):
    def test_it_says_what_was_written_and_recorded_before_it_stopped(self):                   # #468
        real = file_changes._carry
        calls = []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            result = tagging.change_tags(self.library, self.side.real[:3], ["Harbour"], [], rl.EXIFTOOL)
        self.assertIn(roots_service.STOPPED, result.refused)
        self.assertIn("Before it stopped 1 file(s) were written", result.refused)
        self.assertEqual(1, result.changed)
        self.assertIsNotNone(result.details["change"])
        self.assertEqual(1, len(result.details["written"]))
        from tagpup.files.exiftool_session import ExifToolSession
        with ExifToolSession(executable=rl.EXIFTOOL) as et:
            tagged = [p for p in self.side.real[:3] if "Harbour" in et.get_tags([p], tags=["XMP:Subject"])[0].get(
                "XMP:Subject", [])]
        self.assertEqual(1, len(tagged), "the file written is the one the Result says")


class ALibraryBehindTheSchema(BeforeAMove):
    def behind(self):
        return mock.patch.object(store_job_runs, "claim", return_value=store_job_runs.Claim(why="behind"))

    def test_a_move_says_so_in_words(self):                                                   # #470
        with self.behind():
            result = self.move(self.copy, apply=True)
        self.assertIn("open it with TagPup or the CLI once", result.refused)
        self.assertNotIn("under way", result.refused)
        self.assertNotIn("in a moment", result.refused)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class OnlyTheCurrentAndThePreviousPlace(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_keep_")
        self.places = []
        for name in "ABCD":
            self.places.append(os.path.join(self.home.root, name))
            os.makedirs(self.places[-1])
        config.add_machine_root("pictures", self.places[0])

    def test_a_move_drops_anything_older(self):                                               # #472
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[2])
        self.assertEqual([self.places[2], self.places[1]], list(config.machine_roots()["pictures"]))
        config.set_location("pictures", self.places[3])
        self.assertEqual([self.places[3], self.places[2]], list(config.machine_roots()["pictures"]))

    def test_going_to_the_previous_place_by_name_keeps_two(self):                             # #472
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[0])
        self.assertEqual([self.places[0], self.places[1]], list(config.machine_roots()["pictures"]))

    def test_a_place_older_than_that_is_no_longer_recognised(self):                           # #472
        from tagpup.core import paths
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[2])
        roots = config.roots_of({"pictures": ""})
        self.assertIsNone(roots.locate(os.path.join(self.places[0], "x.jpg")))
        self.assertIsNotNone(roots.locate(os.path.join(self.places[1], "x.jpg")))
        self.assertEqual("@pictures/x.jpg", paths.to_row(os.path.join(self.places[2], "x.jpg"), roots))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class OneOwnerOfAShareBeingAway(unittest.TestCase):
    SHARE = "\\\\idziserver\\Pictures\\Pictures"

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)

    def test_a_look_at_one_file_and_verify_use_the_same_cache(self):                          # #474
        release = threading.Event()
        self.addCleanup(release.set)
        state, _ = roots_verify._bounded(self.SHARE, lambda: release.wait(30), 0.2)
        self.assertEqual("away", state)
        waits, counting = share_waits.counted()
        with counting:
            self.assertIs(shares.UNANSWERED, shares.stamp_of(self.SHARE + "\\2024\\a.jpg"),
                          "a share Verify found away was asked for again by a look at one file")
        self.assertEqual([], waits, "and waited for")

    def test_and_the_other_way(self):                                                         # #474
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(shares, "_stat_state", lambda path: release.wait(30)), \
                mock.patch.object(shares, "SHARE_WAIT", 0.2):
            self.assertIs(shares.UNANSWERED, shares.stamp_of(self.SHARE + "\\2024\\a.jpg"))
        self.assertIn(shares.share_of(self.SHARE), shares._away)
        waits, counting = share_waits.counted()
        with counting:
            self.assertEqual("away", roots_verify._bounded(self.SHARE, lambda: "never asked", 5)[0])
        self.assertEqual([], waits, "a share a look at one file found away was waited for by Verify")


if __name__ == "__main__":
    unittest.main()

"""The explicit command that adopts a root, and what stands in its way (tagpup.services.roots,
tagpup.store.adoption; docs/ARCHITECTURE.md, "Roots and machines").

Opening a library never converts it; `roots adopt` does, when the owner says so. A dry run
unless applied; refused, with nothing written, for a wrong location, a root adopted already,
a row that would not convert back, two rows that would become one, a write lock held by another
process; one transaction, one journaled change that `undo` reverses; and a crash anywhere
before the commit leaves the library exactly as it was.
"""
import hashlib
import json
import os
import shutil
import subprocess
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
from tagpup.core import processes  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import adoption, db, journal, schema  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"

#: What a conversion moves, and what the journal and counters add on their own.
NOT_COMPARED = ("generations", "changes", "change_rows", "roots", "schema_version", "photo_people")


class Crash(BaseException):
    """The process stopping at a step: not an Exception, so nothing catches it."""


def crash_at(step):
    def reached(name):
        if name == step:
            raise Crash(step)
    return reached


class AdoptionCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_adopt_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=12, outside=2)
        self.map_file = config.machine_roots_path()

    def adopt(self, **kwargs):
        return self.side.adopt(**kwargs)

    def history(self):
        return journal.history(self.side.db_path, limit=50)

    def map(self):
        return config.machine_roots()

    def fingerprint(self):
        """The library's file content as rows: what 'nothing was written' means."""
        return json.dumps(self.side.dump(), default=repr, sort_keys=True)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheDryRun(AdoptionCase):
    def test_it_counts_each_table_and_writes_nothing(self):
        before = self.fingerprint()
        result = self.adopt(apply=False)
        self.assertIsNone(result.refused, result.refused)
        self.assertTrue(result.details["dry_run"])
        tables = result.details["rehearsal"]["tables"]
        # Three folders of 14 photos, two photos beside them under no root.
        self.assertEqual({"rows": 44, "convert": 42, "already": 0, "outside": 2, "respelled": 0, "irreversible": 0,
                          "json": 42}, tables["photos"])
        self.assertEqual({"rows": 2, "convert": 2}, {k: tables["damaged_files"][k] for k in ("rows", "convert")})
        self.assertEqual(1, tables["added_folders"]["convert"])
        self.assertEqual(1, tables["suggestions"]["json"])
        self.assertEqual(["library.roots", "library.ignored"], result.details["rehearsal"]["settings"])
        self.assertEqual(before, self.fingerprint(), "a dry run wrote to the library")
        self.assertEqual([], self.side.library_roots())
        self.assertFalse(os.path.exists(self.map_file), "a dry run wrote the machine's map")
        self.assertTrue(result.details["map"]["would_write"])

    def test_the_rows_under_no_root_are_reported_by_folder(self):
        report = self.adopt(apply=False).details["rehearsal"]
        self.assertEqual(2, report["outside_rows"])
        self.assertEqual([2], [group["count"] for group in report["outside"]])

    def test_no_backup_is_taken(self):
        self.adopt(apply=False)
        self.assertFalse(os.path.exists(os.path.join(self.home.data, "backups")))

    def test_a_library_behind_this_version_is_not_migrated_by_it(self):
        from test_migrations import at_version
        behind = self.home.library("behind.db")
        at_version(behind, schema.LATEST - 1)
        with open(behind, "rb") as handle:
            before = hashlib.sha256(handle.read()).hexdigest()
        from tagpup.core.library import Library
        result = roots_service.adopt(Library(behind), "pictures", rl.ADDRESS, self.side.pictures, rl.machine())
        with open(behind, "rb") as handle:
            self.assertEqual(before, hashlib.sha256(handle.read()).hexdigest(), "a dry run migrated the library")
        self.assertEqual(schema.LATEST - 1, len(schema.MIGRATIONS) - len(schema.pending(behind)))
        self.assertTrue(result.refused and "lies under" in result.refused, result.refused)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhatItRefuses(AdoptionCase):
    """Each refusal writes nothing, and says why."""

    def refused(self, why, **kwargs):
        before = self.fingerprint()
        result = self.adopt(**kwargs)
        self.assertTrue(result.refused, "it was not refused")
        self.assertIn(why, result.refused)
        self.assertEqual(before, self.fingerprint(), "a refused adoption wrote to the library")
        self.assertEqual([], self.side.library_roots())
        return result

    def test_a_root_the_library_already_has(self):
        self.assertTrue(self.adopt().ok)
        before = self.fingerprint()
        again = self.adopt()
        self.assertIn("already has a root named 'pictures'", again.refused)
        self.assertEqual(before, self.fingerprint())

    def test_a_location_that_is_not_there(self):
        self.refused("is not a folder on this machine", location=os.path.join(self.home.root, "nowhere"))

    def test_a_location_no_row_lies_under_would_put_every_row_outside_the_root(self):
        wrong = os.path.join(self.home.root, "Another place")
        os.makedirs(wrong)
        result = self.refused("no row of the library lies under the location", location=wrong)
        self.assertIn("all 44 photo row(s) are outside it", result.refused)
        self.assertFalse(os.path.exists(self.map_file), "a refused adoption wrote the machine's map")

    def test_a_location_that_is_a_parent_of_the_rows_is_fine_and_a_narrower_one_only_takes_its_own(self):
        narrower = os.path.join(self.side.pictures, "2024 Regatta")
        result = self.adopt(apply=False, location=narrower)
        self.assertIsNone(result.refused)
        self.assertEqual(14, result.details["rehearsal"]["tables"]["photos"]["convert"])
        parent = self.home.root
        self.assertEqual(44, self.adopt(apply=False, location=parent).details["rehearsal"]["tables"]["photos"]["convert"])

    def test_a_row_that_would_not_convert_back(self):
        # A colon under a root is an NTFS stream's name: to_row refuses it, so it cannot be read back.
        stream = os.path.join(self.side.pictures, "2024 Regatta", "IMG:1.jpg")
        self.side_execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')", (stream,))
        result = self.refused("do not convert back to the same file")
        self.assertIn("1 row(s)", result.refused)

    def side_execute(self, sql, params=()):
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(sql, params))

    def test_two_rows_that_would_become_one(self):
        a = os.path.join(self.side.pictures, "2024 Regatta", "Same File.jpg")
        b = os.path.join(self.side.pictures, "2024 Regatta", "SAME FILE.JPG")
        for path in (a, b):
            self.side_execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')",
                              (path,))
        self.refused("would become the same row as another")

    def test_rows_that_differ_only_in_the_roots_own_spelling_would_become_one_row(self):
        lower = os.path.join(self.side.pictures, "2024 Regatta", "IMG_1001.jpg").replace("Pictures", "pictures")
        self.side_execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')", (lower,))
        self.refused("two rows")

    def test_a_write_lock_another_process_holds(self):
        holder = processes.start([sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, %r)\n"
            "from tagpup.store import db\n"
            "conn = db.connect(%r)\n"
            "conn.execute('BEGIN IMMEDIATE')\n"
            "time.sleep(20)\n") % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self.side.db_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for_the_lock()
            with mock.patch.object(adoption, "LOCK_WAIT_MS", 300):
                result = self.adopt()
            self.assertIn("another process holds the library's write lock", result.refused)
        finally:
            processes.kill_tree(holder.pid)
            holder.wait()
        self.assertEqual([], self.side.library_roots())

    def wait_for_the_lock(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            conn = db.connect(self.side.db_path, timeout=0.1)
            try:
                conn.execute("PRAGMA busy_timeout=50")
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
            except Exception:
                return
            finally:
                conn.close()
            time.sleep(0.2)
        self.fail("the other process never held the lock")

    def test_a_change_of_photo_files_not_finished(self):
        self.side_execute("INSERT INTO changes (operation, status, schema_version, created, summary)"
                          " VALUES ('add to all selected', 'planned', %d, '2026-10-02 10:00:00', '{}')" % schema.LATEST)
        self.refused("change(s) of photo files are not finished")

    def test_a_map_that_places_the_root_elsewhere(self):
        other = os.path.join(self.home.root, "Another place")
        os.makedirs(other)
        config.add_machine_root("pictures", other)
        result = self.refused("already places pictures")
        self.assertIn(config.machine_roots_path(), result.refused)

    def test_a_map_that_cannot_be_read(self):
        with open(self.map_file, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        result = self.refused("machine_roots.json")
        self.assertIn("not json", result.refused.lower() + " not json")

    def test_a_name_the_rules_do_not_allow(self):
        result = roots_service.adopt(self.side.library, "Not Allowed!", rl.ADDRESS, self.side.pictures, rl.machine())
        self.assertIn("1 to 32 characters", result.refused)

    def test_a_location_that_is_not_absolute(self):
        result = roots_service.adopt(self.side.library, "pictures", rl.ADDRESS, "Pictures", rl.machine())
        self.assertIn("not an absolute folder", result.refused)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhatItDoes(AdoptionCase):
    def test_it_converts_every_table_in_one_change_and_keeps_every_count(self):
        counts = {table: len(rows) for table, rows in self.side.dump().items()}
        result = self.adopt()
        self.assertTrue(result.ok, result.message())
        after = self.side.dump()
        self.assertEqual(counts["roots"] + 1, len(after["roots"]))
        self.assertEqual({table: n for table, n in counts.items() if table not in ("roots", "changes")},
                         {table: len(rows) for table, rows in after.items() if table not in ("roots", "changes")})
        self.assertEqual(counts["changes"] + 1, len(after["changes"]))
        self.assertEqual(sum(c["convert"] for c in result.details["adopted"]["tables"].values()), result.changed)
        listed = [entry for entry in self.history() if entry["operation"].startswith("roots adopt")]
        self.assertEqual(1, len(listed))
        self.assertEqual(("roots adopt: pictures", "applied"), (listed[0]["operation"], listed[0]["status"]))
        self.assertEqual({"photos": 42, "suggestions": 0, "damaged_files": 2, "added_folders": 1, "change_files": 0},
                         listed[0]["summary"]["converted"])

    def test_the_backup_is_taken_first_and_a_recent_one_covers_the_next(self):
        first = self.adopt()
        self.assertTrue(first.details["backup"]["made"])
        self.assertTrue(os.path.exists(first.details["backup"]["file"]))
        copy = db.connect(db.readonly_uri(first.details["backup"]["file"]), uri=True)
        try:
            self.assertEqual(0, copy.execute("SELECT COUNT(*) FROM photos WHERE path LIKE '@%'").fetchone()[0],
                             "the backup was taken after the conversion")
            self.assertEqual(44, copy.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        finally:
            copy.close()
        undone = journal_service.undo(self.side.library, first.details["change"], apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        second = self.adopt()
        self.assertTrue(second.ok, second.message())
        self.assertFalse(second.details["backup"]["made"], "a copy from minutes ago covers this one")
        self.assertEqual(first.details["backup"]["file"], second.details["backup"]["file"])
        with mock.patch.object(adoption, "RECENT_BACKUP_SECONDS", -1):
            self.assertTrue(adoption.backup(self.side.db_path)[1], "a copy that is not recent is made again")

    def test_the_map_is_written_when_it_lacks_the_root_and_not_when_it_has_it(self):
        self.assertTrue(self.adopt().details["map"]["written"])
        self.assertEqual({"pictures": (self.side.pictures,)}, self.map())
        with open(self.map_file, encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertEqual({"version": 1, "roots": {"pictures": [self.side.pictures]}}, written)
        self.assertEqual([], [n for n in os.listdir(self.home.root) if n.endswith(".tmp")])
        undone = journal_service.undo(self.side.library, self.history()[0]["id"], apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        before = os.stat(self.map_file).st_mtime_ns
        again = self.adopt()
        self.assertNotIn("written", again.details["map"], "the map already had the root")
        self.assertEqual(before, os.stat(self.map_file).st_mtime_ns)

    def test_the_map_keeps_the_roots_other_libraries_use(self):
        config.add_machine_root("scans", os.path.join(self.home.root, "Scans"))
        self.assertTrue(self.adopt().ok)
        self.assertEqual({"pictures", "scans"}, set(self.map()))

    def test_the_map_is_replaced_whole_or_not_at_all(self):
        config.add_machine_root("scans", os.path.join(self.home.root, "Scans"))
        with open(self.map_file, "rb") as handle:
            before = handle.read()
        with mock.patch("os.replace", side_effect=PermissionError("held by a reader")):
            with mock.patch.object(config.time, "sleep"):
                with self.assertRaises(config.MachineMapError):
                    config.add_machine_root("pictures", self.side.pictures)
        with open(self.map_file, "rb") as handle:
            self.assertEqual(before, handle.read())
        self.assertEqual([], [n for n in os.listdir(self.home.root) if n.endswith(".tmp")])

    def test_a_map_that_cannot_be_written_stops_before_the_library_is_touched(self):
        before = self.fingerprint()
        with mock.patch.object(config, "add_machine_root", side_effect=config.MachineMapError("read-only folder")):
            result = roots_service.adopt(self.side.library, "pictures", rl.ADDRESS, self.side.pictures,
                                         roots_service.Machine(config.machine_roots, config.add_machine_root,
                                                               config.machine_roots_path), apply=True)
        self.assertIn("read-only folder", result.refused)
        self.assertEqual(before, self.fingerprint())

    def test_the_library_is_read_the_same_after(self):
        before = self.rows_as_read()
        self.assertTrue(self.adopt().ok)
        self.assertEqual(before, self.rows_as_read())

    def rows_as_read(self):
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            return sorted((path, mtime, size, tags, raw) for path, mtime, size, tags, _p, _c, raw, _y, _v
                          in store_photos.index_rows(conn, "m"))
        finally:
            conn.close()

    def test_a_second_library_over_the_same_folder_keeps_its_own_native_rows_until_adopted(self):
        other = rl.Side(self.home, "second", real=2, bulk=12, outside=2, share=self.side)
        self.assertTrue(self.adopt().ok)
        self.assertEqual([], other.library_roots())
        self.assertEqual([], [p for p in other.raw_paths() if p.startswith("@")])
        # Same map, same location: the second library's own adoption writes nothing to the map.
        before = os.stat(self.map_file).st_mtime_ns
        result = other.adopt()
        self.assertTrue(result.ok, result.message())
        self.assertNotIn("written", result.details["map"])
        self.assertEqual(before, os.stat(self.map_file).st_mtime_ns)
        self.assertEqual(sorted(other.raw_paths()), sorted(self.side.raw_paths()))

    def test_a_library_with_a_root_adopts_another_and_only_its_own_rows_move(self):
        second_place = os.path.join(self.home.root, "photo_index", "Elsewhere")
        first = self.adopt()
        self.assertTrue(first.ok)
        result = roots_service.adopt(self.side.library, "loose", "", second_place, rl.machine(), apply=True)
        self.assertTrue(result.ok, result.message())
        held = self.side.raw_paths()
        self.assertEqual(["@loose/Loose_001.jpg", "@loose/Loose_002.jpg"], [p for p in held if p.startswith("@loose")])
        self.assertEqual(42, len([p for p in held if p.startswith("@pictures/")]))
        undone = journal_service.undo(self.side.library, result.details["change"], apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual(42, len([p for p in self.side.raw_paths() if p.startswith("@pictures/")]))
        self.assertEqual(2, len([p for p in self.side.raw_paths() if p.startswith(self.home.root)]))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhenItIsInterrupted(AdoptionCase):
    def test_a_crash_at_any_step_leaves_the_library_exactly_as_it_was(self):
        before = self.fingerprint()
        for step in adoption.STEPS:
            with self.subTest(step=step):
                with mock.patch.object(adoption, "_reached", side_effect=crash_at(step)):
                    with self.assertRaises(Crash):
                        self.adopt()
                self.assertEqual(before, self.fingerprint(), "after a crash at %r" % step)
                self.assertEqual([], self.side.library_roots())
        self.assertTrue(self.adopt().ok, "and the owner can still adopt it")

    def test_a_failure_in_the_middle_of_a_table_rolls_back_the_tables_already_converted(self):
        before = self.fingerprint()
        with mock.patch.object(adoption, "_simple_pass", side_effect=OSError("disk full")):
            result_error = None
            try:
                self.adopt()
            except OSError as problem:
                result_error = problem
        self.assertIsNotNone(result_error)
        self.assertEqual(before, self.fingerprint())

    def test_a_verification_that_fails_keeps_nothing(self):
        before = self.fingerprint()
        with mock.patch.object(adoption, "verify", return_value=["1 row(s) name a root the library does not have"]):
            result = self.adopt()
        self.assertIn("verification failed", result.refused)
        self.assertEqual(before, self.fingerprint())

    def test_a_map_that_places_the_root_other_than_where_the_rows_were_converted_keeps_nothing(self):
        before = self.fingerprint()
        original = config.roots_of
        elsewhere = os.path.join(self.home.root, "Elsewhere")

        def lying(logical, path=None):
            from tagpup.core import paths
            return paths.Roots.of(logical, {"pictures": [elsewhere]}, path)

        from tagpup.core import machine
        machine.provide(lying)
        try:
            result = self.adopt()
        finally:
            machine.provide(original)
        self.assertIn("places the root at", result.refused)
        self.assertEqual(before, self.fingerprint())


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TwoAtOnce(AdoptionCase):
    def test_two_adoptions_of_one_library_make_one_change(self):
        results, gate = [], threading.Barrier(2)

        def run():
            gate.wait()
            try:
                results.append(self.adopt())
            except BaseException as problem:
                results.append(problem)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(2, len(results))
        self.assertEqual([], [r for r in results if isinstance(r, BaseException)])
        self.assertEqual([False, True], sorted(bool(r.ok and not r.refused) for r in results))
        refused = [r for r in results if r.refused][0]
        self.assertTrue("already has a root" in refused.refused or "write lock" in refused.refused, refused.refused)
        self.assertEqual(1, len([e for e in self.history() if e["operation"].startswith("roots adopt")]))
        self.assertEqual(1, len(self.side.library_roots()))
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual([], adoption.verify(conn, store_roots.roots_for(conn)))
        finally:
            conn.close()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class Undoing(AdoptionCase):
    def setUp(self):
        super().setUp()
        self.before = self.side.dump(leave_out=NOT_COMPARED)
        self.paths_before = self.side.raw_paths()
        self.change = self.adopt().details["change"]

    def undo(self, apply=True):
        return journal_service.undo(self.side.library, self.change, apply=apply)

    def test_history_lists_it_and_says_it_can_be_undone(self):
        entry = [e for e in self.history() if e["id"] == self.change][0]
        self.assertEqual("applied", entry["status"])
        refusals = journal_service.refusals(self.side.library, [{"id": self.change, "operation": entry["operation"]}])
        self.assertEqual({self.change: None}, refusals)

    def test_a_rehearsal_says_how_many_rows_and_writes_nothing(self):
        before = self.fingerprint()
        rehearsal = self.undo(apply=False)
        self.assertIsNone(rehearsal.refused, rehearsal.refused)
        self.assertEqual(self.history()[0]["summary"]["converted"], {"photos": 42, "suggestions": 0,
                                                                     "damaged_files": 2, "added_folders": 1,
                                                                     "change_files": 0})
        self.assertEqual(45, rehearsal.attempted)
        self.assertEqual(before, self.fingerprint())

    def test_the_undo_puts_every_row_back_as_it_was(self):
        result = self.undo()
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(45, result.changed)
        self.assertEqual(self.before, self.side.dump(leave_out=NOT_COMPARED))
        self.assertEqual([], self.side.library_roots())
        self.assertEqual("undone", [e for e in self.history() if e["id"] == self.change][0]["status"])
        self.assertTrue(self.undo().refused, "undone twice")

    def test_it_is_refused_while_a_later_change_wrote_a_path(self):
        photo_id = self.side.rows("SELECT id FROM photos WHERE path = ?", ("@pictures/2024 Regatta/IMG_1001.jpg",))[0][0]
        renamed = os.path.join(self.side.pictures, "2024 Regatta", "Another.jpg")
        later = journal.apply(self.side.db_path, "rename", [journal.update(
            "photos", (photo_id,), {"path": self.side.real[0]}, {"path": renamed})])
        before = self.fingerprint()
        refused = self.undo()
        self.assertIn("change %d (rename), made after it, wrote a path: undo it first" % later.change_id, refused.refused)
        self.assertEqual(before, self.fingerprint())
        reasons = journal_service.refusals(self.side.library, [{"id": self.change, "operation": "roots adopt: pictures"}])
        self.assertIn("undo it first", reasons[self.change])
        self.assertIsNone(journal_service.undo(self.side.library, later.change_id, apply=True).refused)
        self.assertIsNone(self.undo().refused)
        self.assertEqual(self.before, self.side.dump(leave_out=NOT_COMPARED))

    def test_a_later_change_that_wrote_no_path_does_not_stop_it(self):
        from tagpup.services import settings
        self.assertTrue(settings.change(self.side.library, {"renaming.format": "{grouping} - {index}"}).changed)
        self.assertIsNone(self.undo().refused)

    def test_it_needs_the_map_to_say_where_the_root_was(self):
        os.remove(self.map_file)
        before = self.fingerprint()
        result = self.undo()
        self.assertIn("machine_roots.json", result.refused)
        self.assertIn("pictures", result.refused)
        self.assertEqual(before, self.fingerprint())

    def test_what_the_undo_writes_is_what_the_map_says_now(self):
        moved = os.path.join(self.home.root, "moved")
        shutil.copytree(self.side.pictures, moved)
        with open(self.map_file, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [moved, self.side.pictures]}}, handle)
        self.assertIsNone(self.undo().refused)
        expected = sorted(os.path.join(moved, os.path.relpath(p, self.side.pictures)) for p in self.paths_before
                          if p.lower().startswith(self.side.pictures.lower() + os.sep))
        self.assertEqual(42, len(expected))
        self.assertEqual(expected, sorted(p for p in self.side.raw_paths() if p.startswith(moved)))

    def test_the_cli_undoes_it(self):
        from click.testing import CliRunner
        from tagpup_cli import cli

        runner = CliRunner()
        said = runner.invoke(cli, ["--db", self.side.db_path, "undo", str(self.change)]).output
        self.assertIn("Rehearsed: 45 row(s)", said)
        said = runner.invoke(cli, ["--db", self.side.db_path, "undo", str(self.change), "--apply"]).output
        self.assertIn("Undid change %d: 45 row(s) written back" % self.change, said)
        self.assertEqual(self.before, self.side.dump(leave_out=NOT_COMPARED))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ARestoredBackup(AdoptionCase):
    def test_a_copy_from_before_the_adoption_restored_after_it_is_a_native_library_again(self):
        before = self.side.dump(leave_out=("generations",))
        copy = db.backup(self.side.db_path, "by-hand")
        self.assertTrue(self.adopt().ok)
        self.assertEqual(1, len(self.side.library_roots()))
        for suffix in ("", "-wal", "-shm"):
            if suffix and os.path.exists(self.side.db_path + suffix):
                os.remove(self.side.db_path + suffix)
        shutil.copyfile(copy, self.side.db_path)
        schema._current.clear()
        schema.ensure(self.side.db_path)
        self.assertEqual([], self.side.library_roots())
        self.assertEqual(before, self.side.dump(leave_out=("generations",)))
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(self.side.real[0], store_photos.rows_of(conn, [self.side.real[0]])[
                store_photos.paths.key(self.side.real[0])][1])
        finally:
            conn.close()
        self.assertTrue(self.map(), "the map still names the root: nothing of the library's depends on it")
        self.assertTrue(self.adopt().ok, "and it can be adopted again")

    def test_a_copy_from_after_the_adoption_restored_is_a_rooted_library_that_reads_as_before(self):
        self.assertTrue(self.adopt().ok)
        copy = db.backup(self.side.db_path, "by-hand")
        undone = journal_service.undo(self.side.library, self.adoption_change(), apply=True)
        self.assertIsNone(undone.refused)
        shutil.copyfile(copy, self.side.db_path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.side.db_path + suffix):
                os.remove(self.side.db_path + suffix)
        schema._current.clear()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(self.side.real[0], store_photos.rows_of(conn, [self.side.real[0]])[
                store_photos.paths.key(self.side.real[0])][1])
        finally:
            conn.close()

    def adoption_change(self):
        return [e for e in self.history() if e["operation"].startswith("roots adopt")][0]["id"]


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WithoutTheMap(AdoptionCase):
    """A library that holds a root, opened on a machine that does not say where it keeps it,
    refuses its photos: it is never an empty library."""

    def setUp(self):
        super().setUp()
        self.assertTrue(self.adopt().ok)
        os.remove(self.map_file)

    def test_a_read_of_its_photos_says_what_to_write_and_where(self):
        from tagpup.core import paths
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            with self.assertRaises(paths.UnmappedRoot) as raised:
                store_photos.rows_under(conn, self.side.pictures)
            with self.assertRaises(paths.UnmappedRoot):
                store_photos.all_paths(conn)
            with self.assertRaises(paths.UnmappedRoot):
                store_roots.sql_equals(conn, "path", os.path.join(self.side.outside_folder, "x.jpg"))
        finally:
            conn.close()
        said = str(raised.exception)
        self.assertIn("pictures", said)
        self.assertIn("machine_roots.json", said)
        self.assertIn(self.home.root, said)
        self.assertIn('"roots"', said)

    def test_a_write_is_refused_too_so_no_row_is_written_that_cannot_be_read(self):
        from tagpup.core import paths
        with self.assertRaises(paths.UnmappedRoot):
            store_photos.move_rows(self.side.db_path, {self.side.real[0]: self.side.real[0] + ".x"})
        self.assertIn("@pictures/2024 Regatta/IMG_1001.jpg", self.side.raw_paths())

    def test_the_library_file_is_untouched_by_the_refusals(self):
        before = self.fingerprint()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            for ask in (lambda: store_photos.rows_under(conn, self.side.pictures), lambda: store_photos.all_paths(conn)):
                try:
                    ask()
                except Exception:
                    pass
        finally:
            conn.close()
        self.assertEqual(before, self.fingerprint())

    def test_the_check_and_the_listing_say_so_instead_of_raising(self):
        problems = roots_service.check(self.side.library)
        self.assertTrue(problems and "machine_roots.json" in problems[0], problems)
        found = roots_service.listing(self.side.library, rl.machine())
        self.assertEqual([False], [entry["mapped"] for entry in found["roots"]])

    def test_the_problem_at_open_names_the_file_and_the_line_to_add(self):
        said = roots_service.problem(self.side.library)
        self.assertIn("machine_roots.json", said)
        config.add_machine_root("pictures", self.side.pictures)
        self.assertIsNone(roots_service.problem(self.side.library))

    def test_the_doctor_still_reports_and_says_why_it_leaves_out_what_needs_the_paths(self):
        said = []
        broken = rl.doctor().report(self.side.db_path, show=1, out=said.append)
        text = "\n".join(said)
        self.assertIn("this machine does not say where the library's roots are", text)
        self.assertIn("machine_roots.json", text)
        self.assertIn("rooted rows that do not convert back", text)
        self.assertGreaterEqual(broken, 1)
        self.assertNotIn("rows whose file is not on disk", text, "it cannot be said without the paths")

    def test_the_command_line_warns_at_open(self):
        from click.testing import CliRunner
        from tagpup_cli import cli
        said = CliRunner().invoke(cli, ["--db", self.side.db_path, "history"]).output
        self.assertIn("Warning:", said)
        self.assertIn("machine_roots.json", said)

    def test_the_machine_that_places_it_reads_the_library_again(self):
        config.add_machine_root("pictures", self.side.pictures)
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(42 + 2, len(store_photos.all_paths(conn)))
        finally:
            conn.close()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheCommandLine(AdoptionCase):
    def run_cli(self, *args):
        from click.testing import CliRunner
        from tagpup_cli import cli
        return CliRunner().invoke(cli, ["--db", self.side.db_path, "roots", *args])

    def test_a_library_with_no_roots_says_so(self):
        said = self.run_cli().output
        self.assertIn("has no roots", said)

    def test_a_dry_run_reports_and_changes_nothing(self):
        before = self.fingerprint()
        said = self.run_cli("adopt", "--name", "pictures", "--address", rl.ADDRESS, "--location", self.side.pictures)
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Would adopt root pictures", said.output)
        self.assertIn("photos: 44 row(s), 42 to convert", said.output)
        self.assertIn("2 under no root", said.output)
        self.assertIn("Nothing changed. --apply writes", said.output)
        self.assertEqual(before, self.fingerprint())

    def test_apply_converts_and_the_listing_shows_where_the_machine_keeps_it(self):
        said = self.run_cli("adopt", "--name", "pictures", "--address", rl.ADDRESS, "--location", self.side.pictures,
                            "--apply")
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Adopted: 45 row(s) converted, change", said.output)
        self.assertIn("Backup:", said.output)
        listed = self.run_cli().output
        self.assertIn("pictures", listed)
        self.assertIn(self.side.pictures, listed)
        self.assertIn("The library's roots are in order.", self.run_cli("check").output)

    def test_a_refusal_exits_one_and_says_why(self):
        said = self.run_cli("adopt", "--name", "pictures", "--location", os.path.join(self.home.root, "nowhere"))
        self.assertEqual(1, said.exit_code)
        self.assertIn("Refused:", said.output)

    def test_a_library_that_is_not_placed_here_is_marked_in_the_listing(self):
        self.assertTrue(self.adopt().ok)
        os.remove(self.map_file)
        self.assertIn("NOT PLACED on this machine", self.run_cli().output)
        checked = self.run_cli("check")
        self.assertEqual(1, checked.exit_code)
        self.assertIn("machine_roots.json", checked.output)


if __name__ == "__main__":
    unittest.main()

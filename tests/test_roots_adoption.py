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

    def side_execute(self, sql, params=()):
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(sql, params))

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
                          "json": 42, "share_spelled": 0, "duplicates": 0}, tables["photos"])
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

    def test_a_new_backup_is_taken_first_every_time(self):
        first = self.adopt()
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
        # A row written between the two adoptions is in the second backup: a copy from minutes ago
        # would not hold it.
        newer = os.path.join(self.side.pictures, "2024 Regatta", "Written between.jpg")
        self.side_execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')", (newer,))
        time.sleep(1.1)
        second = self.adopt()
        self.assertTrue(second.ok, second.message())
        self.assertNotEqual(first.details["backup"]["file"], second.details["backup"]["file"])
        self.assertNotIn("made", second.details["backup"])
        copy = db.connect(db.readonly_uri(second.details["backup"]["file"]), uri=True)
        try:
            self.assertEqual(1, copy.execute("SELECT COUNT(*) FROM photos WHERE path = ?", (newer,)).fetchone()[0])
        finally:
            copy.close()

    def side_execute(self, sql, params=()):
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(sql, params))

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

    def test_a_later_change_that_wrote_a_path_does_not_stop_it_and_is_still_undoable_after(self):
        photo_id = self.side.rows("SELECT id FROM photos WHERE path = ?", ("@pictures/2024 Regatta/IMG_1001.jpg",))[0][0]
        renamed = os.path.join(self.side.pictures, "2024 Regatta", "Another.jpg")
        later = journal.apply(self.side.db_path, "rename", [journal.update(
            "photos", (photo_id,), {"path": self.side.real[0]}, {"path": renamed})])
        recorded = self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'path'",
                                  (later.change_id,))
        self.assertEqual([("@pictures/2024 Regatta/IMG_1001.jpg", "@pictures/2024 Regatta/Another.jpg")], recorded)
        reasons = journal_service.refusals(self.side.library, [{"id": self.change, "operation": "roots adopt: pictures"}])
        self.assertEqual({self.change: None}, reasons)
        rehearsal = self.undo(apply=False)
        self.assertIsNone(rehearsal.refused, rehearsal.refused)
        said = " ".join(rehearsal.details["rehearsal"]["notes"])
        self.assertIn("returns 45 path(s) in the library's tables", said)
        self.assertIn("2 path(s) recorded in later changes' values", said)
        self.assertIn("can still be undone afterwards", said)
        self.assertIsNone(self.undo().refused)
        self.assertEqual([(self.side.real[0], renamed)],
                         self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'path'",
                                        (later.change_id,)), "the later change's values are native again")
        self.assertIsNone(journal_service.undo(self.side.library, later.change_id, apply=True).refused)
        self.assertEqual(self.before, self.side.dump(leave_out=NOT_COMPARED))

    def test_a_later_change_whose_path_cannot_be_converted_back_is_named(self):
        self.side_execute("INSERT INTO changes (operation, status, schema_version, created, applied, summary)"
                          " VALUES ('rename', 'applied', %d, '2026-10-02 10:00:00', '2026-10-02 10:00:00', '{}')"
                          % schema.LATEST)
        later = self.side.rows("SELECT MAX(id) FROM changes")[0][0]
        self.side_execute("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                          " VALUES (?, 'update', 'photos', '[1]', 'path', '@pictures/a:b.jpg', '@pictures/c.jpg')",
                          (later,))
        before = self.fingerprint()
        refused = self.undo()
        self.assertIn("change(s) %d recorded a path of the root 'pictures' that cannot be converted back" % later,
                      refused.refused)
        self.assertEqual(before, self.fingerprint())

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
class AnotherRootNotPlaced(AdoptionCase):
    """A second root this machine does not place: undoing a change recorded before the
    adoptions, whose paths are native and must be spelled, is a refusal naming the map file,
    never an UnmappedRoot."""

    def test_an_older_change_is_refused_not_raised(self):
        from tagpup.core import paths
        photo_id = self.side.rows("SELECT id FROM photos WHERE path = ?", (self.side.outside[0],))[0][0]
        renamed = os.path.join(self.side.outside_folder, "Another.jpg")
        earlier = journal.apply(self.side.db_path, "rename", [journal.update(
            "photos", (photo_id,), {"path": self.side.outside[0]}, {"path": renamed})])
        self.assertTrue(self.adopt().ok)
        second = roots_service.adopt(self.side.library, "loose", "", os.path.dirname(self.side.outside[0]),
                                     rl.machine(), apply=True)
        self.assertTrue(second.ok, second.message())
        with open(self.map_file, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [self.side.pictures]}}, handle)
        before = self.fingerprint()
        result = journal_service.undo(self.side.library, earlier.change_id, apply=True)
        self.assertIn("machine_roots.json", result.refused)
        rehearsal = journal_service.rehearse(self.side.library, earlier.change_id)
        self.assertIn("machine_roots.json", rehearsal.refused)
        self.assertEqual(before, self.fingerprint())
        with self.assertRaises(paths.UnmappedRoot):
            conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
            try:
                store_photos.all_paths(conn)
            finally:
                conn.close()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class RowsAsRealLibrariesSpellThem(AdoptionCase):
    """Rows seeded as other spellings, other places and other shapes than the indexer writes today
    -- what a library a year old holds."""

    def seed(self, *paths_):
        for path in paths_:
            self.side_execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')",
                              (path,))

    def side_execute(self, sql, params=()):
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(sql, params))

    def photos_report(self, **kwargs):
        return self.adopt(apply=False, **kwargs).details["rehearsal"]["tables"]["photos"]

    def test_a_row_with_forward_slashes_converts_and_comes_back_as_the_same_file(self):
        slashed = os.path.join(self.side.pictures, "2024 Regatta", "Slashed.jpg").replace(os.sep, "/")
        self.seed(slashed)
        report = self.photos_report()
        self.assertEqual((43, 1, 0), (report["convert"], report["respelled"], report["irreversible"]))
        self.assertTrue(self.adopt().ok)
        self.assertIn("@pictures/2024 Regatta/Slashed.jpg", self.side.raw_paths())
        change = [e for e in journal.history(self.side.db_path) if e["operation"].startswith("roots adopt")][0]["id"]
        self.assertIsNone(journal_service.undo(self.side.library, change, apply=True).refused)
        self.assertIn(os.path.join(self.side.pictures, "2024 Regatta", "Slashed.jpg"), self.side.raw_paths())

    def test_a_row_with_a_lower_case_prefix_converts_with_its_own_case_kept_below_the_root(self):
        lower = os.path.join(self.side.pictures, "2024 Regatta", "Lower Case.JPG").lower()
        self.seed(lower)
        report = self.photos_report()
        self.assertEqual((43, 1, 0), (report["convert"], report["respelled"], report["irreversible"]))
        self.assertTrue(self.adopt().ok)
        self.assertIn("@pictures/2024 regatta/lower case.jpg", self.side.raw_paths())

    def test_every_place_the_map_lists_is_equivalent(self):
        second = os.path.join(self.home.root, "Second copy")
        os.makedirs(second)
        with open(self.map_file, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [self.side.pictures, second]}}, handle)
        self.seed(os.path.join(second, "2024 Regatta", "At the second place.jpg"))
        report = self.photos_report()
        self.assertEqual((43, 1, 0), (report["convert"], report["respelled"], report["irreversible"]))
        self.assertTrue(self.adopt().ok)
        self.assertIn("@pictures/2024 Regatta/At the second place.jpg", self.side.raw_paths())
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(os.path.join(self.side.pictures, "2024 Regatta", "At the second place.jpg"),
                             store_photos.rows_of(conn, [os.path.join(second, "2024 Regatta", "At the second place.jpg")])[
                                 store_photos.paths.key(os.path.join(second, "2024 Regatta", "At the second place.jpg"))][1],
                             "a path at either place finds the row")
        finally:
            conn.close()

    def test_a_row_that_is_not_an_absolute_path_is_refused_whatever_the_working_folder(self):
        self.seed(os.path.join("2024 Regatta", "Relative.jpg"))
        here = self.photos_report()
        cwd = os.getcwd()
        os.chdir(self.side.pictures)
        try:
            inside = self.photos_report()
            result = self.adopt(apply=False)
        finally:
            os.chdir(cwd)
        self.assertEqual(here, inside, "the dry run depends on where it was started")
        self.assertEqual(1, here["irreversible"])
        self.assertIn("not an absolute path", result.refused)

    def test_rows_spelled_by_the_shares_address_are_named_as_their_own_count_and_folder_list(self):
        shared = os.path.join(rl.ADDRESS, "2024 Regatta", "Spelled by the share.jpg")
        self.seed(shared, os.path.join(rl.ADDRESS, "Trips", "Another.jpg"))
        result = self.adopt(apply=False)
        report = result.details["rehearsal"]
        self.assertEqual(2, report["share_spelled"]["rows"])
        self.assertEqual(2, report["tables"]["photos"]["share_spelled"])
        self.assertEqual(0, report["tables"]["photos"]["irreversible"])
        self.assertEqual([2], [group["count"] for group in report["share_spelled"]["folders"]])
        self.assertIn("retarget", result.refused)
        self.assertIn("from the master, the share, to this machine's copy", result.refused)
        self.assertIn("adopt a root whose location is the share itself", result.refused)
        before = self.fingerprint()
        self.assertTrue(self.adopt().refused)
        self.assertEqual(before, self.fingerprint())

    def test_two_rows_of_one_file_outside_the_root_are_reported_and_do_not_block(self):
        a = os.path.join(self.side.outside_folder, "Twice.jpg")
        self.seed(a, os.path.join(self.side.outside_folder, "TWICE.JPG"))
        result = self.adopt(apply=False)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(1, result.details["rehearsal"]["duplicates"])
        self.assertEqual(0, result.details["rehearsal"]["collisions"])
        self.assertTrue(self.adopt().ok)

    def test_two_rows_that_become_one_are_blamed_on_the_conversion_by_name(self):
        base = os.path.join(self.side.pictures, "2024 Regatta")
        self.seed(os.path.join(base, "Pair.jpg"), os.path.join(base, "PAIR.JPG"))
        result = self.adopt(apply=False)
        self.assertEqual(1, result.details["rehearsal"]["collisions"])
        self.assertIn("checks.one_file_two_rows", result.refused)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class UndoLeavesWhatIsNotAPath(AdoptionCase):
    """An undo converts only the values that hold a path by their structure, and only when the
    value is a row of the root undone. A text with an "@" in it -- an address, a title, a note --
    is not searched, and is left as the byte string it was."""

    def second_root(self):
        result = roots_service.adopt(self.side.library, "loose", "", self.side.outside_folder, rl.machine(), apply=True)
        self.assertTrue(result.ok, result.message())
        return result.details["change"]

    def test_a_change_made_before_any_adoption_whose_metadata_holds_an_at_sign_is_left_alone(self):
        photo = self.side.outside[0]
        photo_id = self.side.rows("SELECT id FROM photos WHERE path = ?", (photo,))[0][0]
        old = self.side.rows("SELECT raw_metadata FROM photos WHERE id = ?", (photo_id,))[0][0]
        new = json.dumps(dict(json.loads(old), **{"XMP:Creator": "Kit Marlowe <kit@pictures.example>",
                                                  "XMP:Description": "see @pictures/notes and @loose/x"}))
        change = journal.apply(self.side.db_path, "edit metadata", [journal.update(
            "photos", (photo_id,), {"raw_metadata": old}, {"raw_metadata": new})]).change_id

        def recorded():
            return self.side.rows("SELECT old, new FROM change_rows WHERE change_id = ? AND column_name = 'raw_metadata'",
                                  (change,))
        before = recorded()
        self.assertEqual([(old, new)], before)
        adopted = self.adopt()
        self.assertTrue(adopted.ok)
        loose = self.second_root()
        self.assertIsNone(journal_service.undo(self.side.library, adopted.details["change"], apply=True).refused)
        self.assertEqual(before, recorded(), "undoing a different root rewrote a change that has nothing to do with it")
        self.assertIsNone(journal_service.undo(self.side.library, loose, apply=True).refused)
        self.assertEqual(before, recorded(), "and neither did undoing the root its photo is under")

    def test_a_value_is_converted_by_what_it_is_and_nothing_outside_the_path_fields_changes(self):
        import random
        from tagpup.core import machine, paths
        folder = os.path.join(self.home.root, "Another place")
        os.makedirs(folder)
        config.add_machine_root("pictures", self.side.pictures)
        config.add_machine_root("loose", folder)
        roots = machine.roots_of({"pictures": "", "loose": ""})
        rnd = random.Random(20261002)
        pieces = ["@", "@pictures", "@pictures/", "@loose/x", "kit@mail.example", "a", " ", "\u00e9", '"', "\\", "@@"]

        def text():
            return "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 6)))

        def of_pictures(value):
            return isinstance(value, str) and value.startswith("@") and value[1:].partition("/")[0].lower() == "pictures"

        def native(value):
            return paths.from_row(value, roots)

        for _ in range(400):
            source = rnd.choice([None, self.side.outside[0].replace(os.sep, "/"), "@loose/a.jpg", "@pictures/a/b.jpg",
                                 "@PICTURES/c.jpg", "@picturesx/d.jpg", "@pictures", "free @pictures/e", "x" + text()])
            meta = {"XMP:Title": text(), "XMP:Creator": text(), "Subject": [text(), text()]}
            if source is not None:
                meta["SourceFile"] = source
            value = json.dumps(meta)
            out = adoption.back_value("photos", "raw_metadata", value, roots, "pictures")
            expected = dict(meta)
            if of_pictures(source):
                expected["SourceFile"] = paths.exiftool_spelling(native(source))
            self.assertEqual(expected, json.loads(out), value)
            if expected == meta:
                self.assertEqual(value, out, "a value with no path of the root is the same bytes")

            suggestion = {"path": rnd.choice([source or "x", "x" + text()]), "title": text(),
                          "suggested_tags": [{"tag": text()}], "nearest_neighbors": [
                              {"path": rnd.choice(["@pictures/n.jpg", "@loose/n.jpg", "x" + text()]), "similarity": 0.5}]}
            out = json.loads(adoption.back_value("suggestions", "raw", json.dumps(suggestion), roots, "pictures"))
            want = json.loads(json.dumps(suggestion))
            for holder in (want, want["nearest_neighbors"][0]):
                if of_pictures(holder["path"]):
                    holder["path"] = native(holder["path"])
            self.assertEqual(want, out, suggestion)

            lines = [rnd.choice([self.side.pictures, "@pictures/x", "@loose/y", "free @pictures text", "", "x" + text()])
                     for _ in range(3)]
            joined = "\n".join(lines)
            moved = adoption.back_value("settings", "value", joined, roots, "pictures").split("\n")
            self.assertEqual([native(line.strip()) if of_pictures(line.strip()) else line for line in lines], moved)

            each = rnd.choice(["@pictures/a", "@loosex/a", "foo@pictures/x", "@PICTURES/b", self.side.pictures, "x" + text()])
            self.assertEqual(native(each) if of_pictures(each) else each,
                             adoption.back_value("photos", "path", each, roots, "pictures"))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AnAdoptionsSummaryHoldsCounts(AdoptionCase):
    """The journal's rule: a summary holds counts and never names. The MCP history tool returns
    summaries without reveal."""

    def test_no_summary_and_no_listing_holds_a_path_or_a_file_name(self):
        result = self.adopt()
        self.assertTrue(result.ok)
        change = result.details["change"]
        listings = [journal.history(self.side.db_path, limit=50), journal.history(self.side.db_path, change_id=change),
                    journal_service.history(self.side.library)["changes"],
                    journal_service.history(self.side.library, change_id=change)["changes"]]
        text = json.dumps(listings, default=repr).lower()
        for name in ("img_1001", "earlier_", "cut short", "added later", "loose_", "photo_index", "pictures\\",
                     os.path.basename(self.home.root).lower(), self.side.base.lower().replace("\\", "\\\\")):
            self.assertNotIn(name, text)
        summary = [e for e in listings[1] if e["id"] == change][0]["summary"]
        self.assertEqual({"converted", "json", "settings", "outside_rows", "respelled", "root", "rows"}, set(summary))
        self.assertEqual("pictures", summary["root"])


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheNoteSaysWhatIsTrue(AdoptionCase):
    def test_it_names_the_roots_that_remain_and_where_the_rows_go(self):
        first = self.adopt().details["change"]
        loose = roots_service.adopt(self.side.library, "loose", "", self.side.outside_folder, rl.machine(), apply=True)
        self.assertTrue(loose.ok)
        said = " ".join(journal_service.undo(self.side.library, loose.details["change"]).details["rehearsal"]["notes"])
        self.assertIn("Undoing the adoption of root loose returns 2 path(s)", said)
        self.assertIn("from loose's row form to the native path this machine's map gives them", said)
        self.assertIn("No row moves to another root", said)
        self.assertIn("the library keeps root pictures, whose rows are not touched", said)
        self.assertNotIn("holds no root", said)
        self.assertIsNone(journal_service.undo(self.side.library, loose.details["change"], apply=True).refused)
        said = " ".join(journal_service.undo(self.side.library, first).details["rehearsal"]["notes"])
        self.assertIn("when it is done the library holds no root", said)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheBackupHoldsTheLock(AdoptionCase):
    def test_the_dry_run_says_how_long_and_to_stop_the_apps(self):
        from click.testing import CliRunner
        from tagpup_cli import cli
        estimate = self.adopt(apply=False).details["rehearsal"]["backup"]
        self.assertGreater(estimate["bytes"], 0)
        self.assertGreaterEqual(estimate["seconds"], 1)
        said = CliRunner().invoke(cli, ["--db", self.side.db_path, "roots", "adopt", "--name", "pictures",
                                         "--location", self.side.pictures]).output
        self.assertIn("Run this with TagPup and TagTuner stopped: the backup holds the write lock for the length of "
                      "the copy (about", " ".join(said.split()))

    def test_the_estimate_uses_the_speed_the_last_backup_ran_at(self):
        self.assertTrue(self.adopt().ok)
        with open(os.path.join(self.home.data, "backups", adoption.RATE_FILE), encoding="utf-8") as handle:
            rate = json.load(handle)["bytes_per_second"]
        self.assertGreater(rate, 0)
        size = adoption.backup_estimate(self.side.db_path)["bytes"]
        self.assertEqual(max(1, int(round(size / rate))), adoption.backup_estimate(self.side.db_path)["seconds"])

    def test_a_write_by_another_process_during_the_copy_is_told_why(self):
        started, release, outcome = threading.Event(), threading.Event(), []
        real = adoption.backup

        def slow(db_path):
            started.set()
            release.wait(60)
            return real(db_path)

        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from tagpup.store import db\n"
            "db.BUSY_TIMEOUT_MS = 300\n"
            "try:\n"
            "    db.write_with_connection(%r, lambda conn: conn.execute(\"UPDATE settings SET value = value\"))\n"
            "    print('written')\n"
            "except Exception as problem:\n"
            "    print(type(problem).__name__, problem)\n"
        ) % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self.side.db_path)
        with mock.patch.object(adoption, "backup", slow):
            thread = threading.Thread(target=lambda: outcome.append(self.adopt()))
            thread.start()
            self.assertTrue(started.wait(60), "the adoption never reached its backup")
            self.assertTrue(os.path.exists(self.side.db_path + ".busy"))
            done = processes.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                                 stdin=subprocess.DEVNULL)
            release.set()
            thread.join(120)
        said = done.stdout.strip()
        self.assertIn("OperationalError", said, done.stderr[-1000:])
        self.assertIn("database is locked", said)
        self.assertIn("being adopted by a root (roots adopt)", said)
        self.assertIn("backup copy holds the write lock for the length of the copy, about", said)
        self.assertTrue(outcome and outcome[0].ok, outcome)
        self.assertFalse(os.path.exists(self.side.db_path + ".busy"), "the note outlived the copy")

    def test_a_note_that_is_old_is_not_believed_and_a_lock_with_no_note_is_a_bare_lock(self):
        db.mark_busy(self.side.db_path, "a reason")
        self.assertEqual("a reason", db.busy_note(self.side.db_path))
        old = time.time() - db.BUSY_NOTE_SECONDS - 5
        os.utime(self.side.db_path + ".busy", (old, old))
        with open(self.side.db_path + ".busy", encoding="utf-8") as handle:
            found = json.load(handle)
        found["since"] = old
        with open(self.side.db_path + ".busy", "w", encoding="utf-8") as handle:
            json.dump(found, handle)
        self.assertIsNone(db.busy_note(self.side.db_path))
        db.clear_busy(self.side.db_path)
        self.assertIsNone(db.busy_note(self.side.db_path))


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

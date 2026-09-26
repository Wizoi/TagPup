"""Snapshots of each library, as its backup (docs/ARCHITECTURE.md, phase 8): three
dailies, one weekly and one monthly, the weekly and monthly from the daily's one read;
taken with the library's writers held back, checked before they are renamed into place,
and an old one removed only after its replacement passed; listed with the journal's
changes since, and restored -- a dry run first, the library as it was snapshotted before
a restore is applied.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import NotFound  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import snapshots as snapshot_service  # noqa: E402
from tagpup.store import db, journal, snapshots  # noqa: E402

#: Noon on a day without a change of clocks near it.
NOON = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
DAY = snapshots.DAY


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="snapshots_")
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.photo = os.path.join(self.home.root, "Harbour", "IMG_0001.jpg")
        conn = db.connect(self.library.path)
        try:
            self.photo_id = photo_rows.add_read(conn, self.photo, {"XMP:Subject": ["Beach"]})
            conn.commit()
        finally:
            conn.close()

    def tags(self):
        return self.query("SELECT tags FROM photos WHERE id = ?", (self.photo_id,))[0][0]

    def query(self, sql, args=(), path=None):
        conn = db.connect(db.readonly_uri(path or self.library.path), uri=True)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def names(self, kind=None):
        return [s.name for s in snapshots.listed(self.library.path, kind)]

    def files(self):
        found = []
        for kind in snapshots.KINDS:
            where = os.path.join(self.library.snapshots, kind)
            if os.path.isdir(where):
                found += sorted("%s/%s" % (kind, name) for name in os.listdir(where))
        return found

    def change_tags(self, tags):
        return journal.apply(self.library.path, "test tags", [
            journal.update("photos", (self.photo_id,), {"tags": self.tags()}, {"tags": tags})])


class Taking(Base):
    def test_the_first_is_a_daily_a_weekly_and_a_monthly_from_one_read(self):
        with mock.patch.object(snapshots, "copy_library", wraps=snapshots.copy_library) as copied:
            done = snapshots.take(self.library.path, now=NOON)
        self.assertEqual(1, copied.call_count, "the library is read once")
        self.assertEqual(["daily", "weekly", "monthly"], [s.kind for s in done["taken"]])
        self.assertEqual(["daily/20260901_120000", "weekly/20260901_120000", "monthly/20260901_120000"], self.names())
        # Each a file of its own, checked, holding the library's rows.
        self.assertEqual(["daily/harbour-20260901_120000.db", "weekly/harbour-20260901_120000.db",
                          "monthly/harbour-20260901_120000.db"], self.files())
        for snapshot in snapshots.listed(self.library.path):
            snapshots.check(snapshot.path)
            self.assertEqual([(self.photo_id,)], self.query("SELECT id FROM photos", path=snapshot.path))
        self.assertEqual(os.path.join(self.home.data, "backups", "harbour"), self.library.snapshots)

    def test_three_dailies_one_weekly_and_one_monthly_are_kept(self):
        for day in range(40):
            snapshots.take(self.library.path, now=NOON + day * DAY)
        self.assertEqual(["daily/20261010_120000", "daily/20261009_120000", "daily/20261008_120000"],
                         self.names("daily"))
        # Refreshed at days 7, 14, 21, 28 and 35; the monthly at day 30.
        self.assertEqual(["weekly/20261006_120000"], self.names("weekly"))
        self.assertEqual(["monthly/20261001_120000"], self.names("monthly"))
        self.assertEqual(5, len(self.files()), "nothing else is left beside them")

    def test_a_daily_is_taken_only_when_the_newest_is_a_day_old(self):
        snapshots.take(self.library.path, now=NOON)
        self.assertEqual([], snapshots.take(self.library.path, now=NOON + 6 * 3600)["taken"])
        # The runner looks every few minutes: a daily a few minutes short of a day is old enough.
        later = snapshots.take(self.library.path, now=NOON + DAY - 600)["taken"]
        self.assertEqual(["daily"], [s.kind for s in later])
        forced = snapshots.take(self.library.path, now=NOON + DAY, force=True)["taken"]
        self.assertEqual(["daily"], [s.kind for s in forced])

    def test_this_processs_writers_are_not_held_back_during_the_copy(self):
        # One step of the backup API in one read transaction is consistent whoever writes
        # meanwhile; holding db.lock_for only stalled every save in the web server for the
        # whole copy, 6 s for photo_index (review of phase 8a, 3).
        held = []
        real = db.connect

        class Source:
            def __init__(self, conn):
                self.conn = conn

            def backup(self, destination):
                lock = db.lock_for(self_library)
                def attempt_to_write():
                    got = lock.acquire(blocking=False)
                    held.append(not got)
                    if got:
                        lock.release()

                attempt = threading.Thread(target=attempt_to_write)
                attempt.start()
                attempt.join(10)
                return self.conn.backup(destination)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        self_library = self.library.path

        def connect(target, *args, **kwargs):
            conn = real(target, *args, **kwargs)
            return Source(conn) if kwargs.get("uri") else conn

        with mock.patch.object(snapshots.db, "connect", side_effect=connect):
            snapshots.take(self.library.path, now=NOON)
        self.assertEqual([False], held, "a writer of this process waited for the copy")


class ALibraryByAnySpelling(unittest.TestCase):
    def test_has_one_set_of_snapshots(self):
        home = own_home.for_test(self, prefix="snapshots_")
        library_actions.create(home.library("Harbour.db"))
        lower, upper = home.library("harbour.db"), home.library("Harbour.db")
        snapshots.take(lower, now=NOON)
        self.assertEqual(["daily/20260901_120000", "weekly/20260901_120000", "monthly/20260901_120000"],
                         [s.name for s in snapshots.listed(upper)])
        self.assertEqual([], snapshots.take(upper, now=NOON + 3600)["taken"])
        # Named as the file on disk is.
        self.assertTrue(os.path.basename(snapshots.listed(lower)[0].path).startswith("Harbour-"))


class AFailedNight(Base):
    def setUp(self):
        super().setUp()
        for day in range(3):
            snapshots.take(self.library.path, now=NOON + day * DAY)
        self.before = self.files()

    def test_a_snapshot_stopped_before_its_rename_leaves_the_old_set_intact(self):
        class Killed(BaseException):
            pass

        with mock.patch.object(snapshots.os, "replace", side_effect=Killed):
            with self.assertRaises(Killed):
                snapshots.take(self.library.path, now=NOON + 3 * DAY)
        # Every snapshot there was is there, and the copy it stopped under its partial name.
        self.assertEqual(sorted(self.before + ["daily/harbour-20260904_120000.db" + snapshots.PARTIAL]),
                         sorted(self.files()))
        self.assertEqual(3, len(self.names("daily")))
        # The next takes the snapshot and deletes what the stopped one left.
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY + 60)
        self.assertEqual(1, done["partials"])
        self.assertEqual(["daily/20260904_120100", "daily/20260903_120000", "daily/20260902_120000"],
                         self.names("daily"))

    def test_a_partial_a_crash_left_is_deleted_by_the_next(self):
        left = os.path.join(self.library.snapshots, "daily", "harbour-20260904_120000.db" + snapshots.PARTIAL)
        with open(left, "wb") as handle:
            handle.write(b"half a library")
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY)
        self.assertEqual(1, done["partials"])
        self.assertFalse(os.path.exists(left))

    def test_a_copy_that_fails_its_check_removes_nothing(self):
        with mock.patch.object(snapshots, "check", side_effect=snapshots.SnapshotFailed("a page is torn")):
            with self.assertRaises(snapshots.SnapshotFailed):
                snapshots.take(self.library.path, now=NOON + 3 * DAY)
        self.assertEqual(self.before, self.files())


class Restoring(Base):
    def setUp(self):
        super().setUp()
        self.was = self.tags()
        snapshots.take(self.library.path, now=NOON)
        self.changed = self.change_tags('["Beach", "Harbour"]')
        self.now = self.tags()

    def test_a_dry_run_says_what_would_be_lost_and_changes_nothing(self):
        listed = snapshot_service.listing(self.library)
        self.assertEqual([1, 1, 1], [s["changes_since"] for s in listed["snapshots"]])
        result = snapshot_service.restore(self.library, "daily/20260901_120000")
        self.assertTrue(result.details["dry_run"])
        self.assertEqual([(self.changed.change_id, "test tags")],
                         [(c["id"], c["operation"]) for c in result.details["lost"]])
        self.assertEqual((1, 0), (result.attempted, result.changed))
        self.assertEqual(self.now, self.tags())
        self.assertEqual([], self.names(snapshots.BEFORE_RESTORE))

    def test_applied_it_puts_the_rows_back_and_can_itself_be_undone(self):
        generations = dict(self.query("SELECT name, value FROM generations"))
        result = snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY)
        self.assertEqual(1, result.changed)
        self.assertEqual(self.was, self.tags())
        self.assertEqual([], self.query("SELECT id FROM changes WHERE operation = 'test tags'"))
        # A cache another process keyed by the generations is not taken for current.
        after = dict(self.query("SELECT name, value FROM generations"))
        self.assertTrue(all(after[name] > value for name, value in generations.items()), (generations, after))
        self.assertEqual(["wal"], [row[0] for row in self.query("PRAGMA journal_mode")])

        before = result.details["before_restore"]
        self.assertEqual("before-restore/20260902_120000", before)
        undone = snapshot_service.restore(self.library, before, apply=True, now=NOON + DAY + 60)
        self.assertEqual(1, undone.changed)
        self.assertEqual(self.now, self.tags())
        self.assertEqual(1, len(self.query("SELECT id FROM changes WHERE operation = 'test tags'")))

    def test_the_before_restore_copy_restored_is_not_pushed_out_by_the_next(self):
        first = snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY)
        snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY + 60)
        # Two before-restore copies are kept; restoring the older makes a third first.
        restored = snapshot_service.restore(self.library, first.details["before_restore"], apply=True,
                                            now=NOON + DAY + 120)
        self.assertEqual(1, restored.changed)
        self.assertEqual(self.now, self.tags())
        self.assertEqual(2, len(self.names(snapshots.BEFORE_RESTORE)))

    def test_a_disk_without_room_for_it_refuses_it_before_anything_is_written(self):
        # Restoring takes the library as it is (a snapshot) and the copy back through the
        # WAL: about three times the library (review of phase 8a, 4).
        with mock.patch.object(snapshots.shutil, "disk_usage", return_value=(10 ** 12, 10 ** 12, 1000)):
            result = snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY)
        self.assertIn("free", result.refused or "")
        self.assertEqual((0, self.now), (result.changed, self.tags()))
        self.assertEqual([], self.names(snapshots.BEFORE_RESTORE))
        self.assertEqual(3 * os.path.getsize(self.library.path), result.details["needs"])

    def test_the_checkpoint_after_it_is_read_and_reported(self):
        result = snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY)
        checkpoint = result.details["checkpoint"]
        self.assertEqual(0, checkpoint["busy"])
        self.assertEqual(checkpoint["log"], checkpoint["checkpointed"])

    def test_a_checkpoint_another_connection_holds_up_is_logged(self):
        reader = db.connect(db.readonly_uri(self.library.path), uri=True)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM photos").fetchone()
        with self.assertLogs("tagpup.store.snapshots", level="WARNING") as logged:
            result = snapshot_service.restore(self.library, "daily/20260901_120000", apply=True, now=NOON + DAY)
        reader.rollback()
        self.assertEqual(1, result.changed)
        self.assertTrue(any("checkpoint" in line for line in logged.output), logged.output)

    def test_a_snapshot_it_has_not_is_not_found(self):
        with self.assertRaises(NotFound):
            snapshot_service.restore(self.library, "daily/20200101_000000")

    def test_a_snapshot_a_newer_version_made_is_refused(self):
        daily = snapshots.listed(self.library.path, "daily")[0]
        conn = db.connect(daily.path)
        try:
            conn.execute("UPDATE schema_version SET version = 99 WHERE version = (SELECT MAX(version) FROM schema_version)")
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        result = snapshot_service.restore(self.library, daily.name, apply=True)
        self.assertTrue(result.refused)
        self.assertEqual((0, self.now), (result.changed, self.tags()))


if __name__ == "__main__":
    unittest.main()

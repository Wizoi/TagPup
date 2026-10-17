"""Snapshots of each library, as its backup (docs/ARCHITECTURE.md, phase 8): three
dailies, one weekly and one monthly, the weekly and monthly from the daily's one read;
taken with the library's writers held back, checked before they are renamed into place,
and an old one removed only after its replacement passed; listed with the journal's
changes since, and restored -- a dry run first, the library as it was snapshotted before
a restore is applied. A copy of a library -- a snapshot, a backup before a bulk write -- is a file of its own,
written without a WAL (the backup then wrote the library into the -wal and checkpointed it, 2.5 GB twice for
photo_index: review of phase 8a, 2). The recurring job takes them and records what it took; `snapshots list` and
`snapshots restore` are its commands.

A snapshot below the baseline (schema 32) is listed as too old and its restore is refused before anything is copied
(owner, 2026-10-15: the snapshots and backups below 32 can no longer be restored).

Lifted: test_snapshot_commands, test_a_copy_is_not_written_through_a_wal and the roots tests of "adopted while open /
prepared / pinned" (test_roots_store): nothing in the program adopts a library any more, and the real action that
changes a library's roots under a running process is a restore of a snapshot from before roots, here a monthly at the
baseline taken before the library had any, restored into a library that a later step has migrated. Break that fails
the kept test: tagpup.store.snapshots.restore not moving the generations past where they stood
(`if before and` -> `if False and`) fails test_the_monthly_from_before_roots_is_restored_under_a_running_index.
Findings: review of phase 8a (1, 2, 3, 4, 8), none numbered in findings.md.
"""
import hashlib
import os
import sys
import threading
import time
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import migration_names  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
import tagpup_cli  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import NotFound  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import snapshots as snapshot_service  # noqa: E402
from tagpup.store import checks, db, derived, job_runs, journal, schema, snapshots  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

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
        # The next, once the partial is old enough to be no copy under way, takes the
        # snapshot and deletes what the stopped one left.
        self.age(os.path.join(self.library.snapshots, "daily", "harbour-20260904_120000.db" + snapshots.PARTIAL))
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY + 60)
        self.assertEqual(1, done["partials"])
        self.assertEqual(["daily/20260904_120100", "daily/20260903_120000", "daily/20260902_120000"],
                         self.names("daily"))

    def partial(self, kind, age=0):
        left = os.path.join(self.library.snapshots, kind, "harbour-20260904_110000.db" + snapshots.PARTIAL)
        os.makedirs(os.path.dirname(left), exist_ok=True)
        with open(left, "wb") as handle:
            handle.write(b"half a library")
        if age:
            self.age(left, age)
        return left

    def age(self, path, seconds=3600):
        then = time.time() - seconds
        os.utime(path, (then, then))

    def test_a_partial_a_crash_left_is_deleted_by_the_next(self):
        left = self.partial("daily", age=3600)
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY)
        self.assertEqual(1, done["partials"])
        self.assertFalse(os.path.exists(left))

    def test_a_partial_being_written_now_is_left_alone(self):
        # Another copy under way -- a restore's before-restore, say -- is not a crash's
        # leftover (review of phase 8a, 8).
        young = self.partial("daily")
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY)
        self.assertEqual(0, done["partials"])
        self.assertTrue(os.path.exists(young))

    def test_only_the_kind_being_taken_is_swept(self):
        other = self.partial(snapshots.BEFORE_RESTORE, age=3600)
        done = snapshots.take(self.library.path, now=NOON + 3 * DAY)
        self.assertEqual(["daily"], [s.kind for s in done["taken"]])
        self.assertTrue(os.path.exists(other))

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


class TheJobAndTheCommands(Base):
    def cli(self, *args):
        with mock.patch.object(tagpup_cli, "console", tagpup_cli.Console(width=200)):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path] + list(args))

    def test_the_job_takes_a_snapshot_and_records_its_size(self):
        runner = recurring.Runner(lambda: [self.library], clock=lambda: NOON)
        ran = {o.job: o for o in runner.run_due() if o.ran}
        # Sync runs too, and fails here: this runner was given no Runtime.
        self.assertEqual({"snapshots", "sync"}, set(ran))
        self.assertEqual(3, ran["snapshots"].result.changed)
        run = [r for r in job_runs.runs(self.library.path) if r.job == "snapshots"][0]
        self.assertEqual(("done", 3, snapshots.disk(self.library.path)),
                         (run.outcome, run.changed["changed"], run.changed["bytes"]))

    def test_no_snapshot_holds_a_run_still_running_and_a_restore_leaves_none(self):
        # The snapshots job's own run is `running` while it copies the library, and was in the copy: restored, the job
        # was held "running" by it until the process ended (review of phase 8a, 1). A snapshot copied by hand can still hold one.
        runner = recurring.Runner(lambda: [self.library], clock=lambda: NOON)
        self.assertTrue(runner.run("snapshots", self.library, force=True)[0].ran)
        for snapshot in snapshots.listed(self.library.path):
            self.assertEqual([], self.query("SELECT id FROM job_runs WHERE outcome = 'running'", path=snapshot.path),
                             snapshot.name)
        daily = snapshots.listed(self.library.path, "daily")[0]
        conn = db.connect(daily.path)
        try:
            conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES"
                         " ('snapshots', 'harbour', '2026-09-01 12:00:00', 'running', ?)", (job_runs.file_journal.owner(),))
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        restored = snapshot_service.restore(self.library, daily.name, apply=True, now=NOON + 60)
        self.assertEqual(1, restored.changed)
        self.assertEqual([], self.query("SELECT id FROM job_runs WHERE outcome = 'running'"))
        self.assertIn(("abandoned",), self.query("SELECT outcome FROM job_runs"))
        again = runner.run("snapshots", self.library, force=True)[0]
        self.assertEqual((True, None), (again.ran, again.why))

    def test_the_commands_list_and_restore(self):
        snapshots.take(self.library.path, now=NOON)
        self.change_tags('["Beach", "Harbour"]')
        name = snapshots.listed(self.library.path, "daily")[0].name
        shown = self.cli("snapshots", "list")
        self.assertEqual(0, shown.exit_code, shown.output)
        self.assertIn(name, shown.output)
        dry = self.cli("snapshots", "restore", name)
        self.assertEqual(0, dry.exit_code, dry.output)
        self.assertIn("would lose 1 change(s)", dry.output)
        # What it costs: the disk it needs, and what the disk has.
        self.assertIn("needs about", dry.output)
        self.assertIn("MB free", dry.output)
        self.assertIn("Nothing changed", dry.output)
        applied = self.cli("snapshots", "restore", name, "--apply")
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertIn("before-restore/", applied.output)
        self.assertNotIn("Harbour", self.tags())


class ACopyIsAFileOfItsOwn(unittest.TestCase):
    def test_a_backup_and_a_snapshot_are_made_by_one_helper_out_of_wal_before_the_copy_is_written(self):
        home = own_home.for_test(self, prefix="copy_")
        path = home.library("harbour.db")
        library_actions.create(path)
        modes, real = [], db.sqlite3.Connection

        class Watched:
            def __init__(self, conn):
                self.conn = conn

            def backup(self, destination, *args, **kwargs):
                modes.append(destination.execute("PRAGMA journal_mode").fetchone()[0])
                return self.conn.backup(destination, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        connect = db.connect

        def watched(target, *args, **kwargs):
            conn = connect(target, *args, **kwargs)
            return Watched(conn) if isinstance(conn, real) and kwargs.get("uri") else conn

        copy = os.path.join(home.root, "copy.db")
        with mock.patch.object(db, "copy_database", wraps=db.copy_database) as copied, \
                mock.patch.object(db, "connect", side_effect=watched):
            backup = db.backup(path, "test")
            snapshots.copy_library(path, copy)
        self.assertEqual((["delete", "delete"], 2), (modes, copied.call_count))
        for each in (backup, copy):
            conn = db.connect(db.readonly_uri(each), uri=True)
            try:
                self.assertEqual("delete", conn.execute("PRAGMA journal_mode").fetchone()[0])
            finally:
                conn.close()
        self.assertEqual([os.path.basename(backup)], [name for name in os.listdir(os.path.dirname(backup))
                                                       if name.startswith(os.path.basename(backup))], "no -wal beside it")


@unittest.skipUnless(os.name == "nt", "the roots below are Windows paths")
class TheMonthlyFromBeforeRoots(unittest.TestCase):
    """The library took its roots after the monthly was taken (at the baseline, with a later step still to come): the owner
    restores it while the index is running and a run holds the library's roots."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="snapshots_old_")
        self.library = Library(self.home.library("harbour.db"))
        schema.ensure(self.library.path)
        conn = db.connect(self.library.path)
        try:
            for name in ("a.jpg", "b.jpg"):
                photo_rows.add_read(conn, "D:\\Harbour\\" + name, {})
            conn.commit()
        finally:
            conn.close()
        snapshots.take(self.library.path, now=NOON)
        self.monthly = snapshots.listed(self.library.path, "monthly")[0]
        self.assertEqual(schema.BASELINE, schema.version_of(self.monthly.path))
        self.enterContext(migration_names.ahead())   # a later step, run on opening the library
        schema.ensure(self.library.path)
        rl.convert_to_root(self.library.path, "D:\\Harbour")   # the owner's roots, arrived since

    def held(self):
        return [row[0] for row in self.query("SELECT path FROM photos ORDER BY id")]

    def query(self, sql):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def restore(self, name, **kwargs):
        result = snapshot_service.restore(self.library, name, apply=True, **kwargs)
        self.assertEqual(1, result.changed, result.refused)
        return result

    def test_the_monthly_from_before_roots_is_restored_under_a_running_index(self):
        from tagpup.services.search import PhotoIndex
        index = PhotoIndex(self.library.path, model="m")
        self.addCleanup(index.close)
        index.load()

        def batch(name):
            path = "D:\\Harbour\\" + name
            index.build_or_update([[0.0] * 4], [{"path": path, "mtime": 1.0, "size": 2, "tags": [], "captions": [],
                                                  "raw_metadata": {"SourceFile": path.replace(os.sep, "/")}}], dim=4)

        batch("Before.jpg")
        rooted = self.held()
        self.assertTrue(all(path.startswith("@pictures/") for path in rooted), rooted)
        generations = dict(self.query("SELECT name, value FROM generations"))
        with store_roots.pinned(self.library.path):
            conn = db.connect(self.library.path)
            try:
                self.assertFalse(store_roots.roots_for(conn).identity, "the library holds roots")
                restored = self.restore(self.monthly.name, now=NOON + DAY)
                with self.assertRaises(store_roots.RootsChanged):
                    store_roots.roots_for(conn)    # the pinned run stops: its roots are not the library's now
            finally:
                conn.close()
        # Migrated on open, to the schema of the code (the later step), and the library holds the monthly's rows with no roots.
        conn = db.connect(self.library.path)
        self.addCleanup(conn.close)
        self.assertEqual(schema.LATEST, schema.version(conn))
        self.assertEqual({}, store_roots.logical(conn))
        self.assertEqual(["D:/Harbour/a.jpg", "D:/Harbour/b.jpg"], sorted(p.replace("\\", "/") for p in self.held()))
        # The derived rows are what a rebuild makes of the photos, and the doctor finds nothing wrong.
        ids = [row[0] for row in conn.execute("SELECT id FROM photos")]
        nodes = [row[0] for row in conn.execute("SELECT id FROM tag_taxonomy")]
        was = derived.listing(conn, ids, nodes)
        derived.rebuild_all(conn)
        self.assertEqual(was, derived.listing(conn, ids, nodes))
        conn.rollback()
        self.assertEqual({}, {check.name: check.count for check in checks.run(conn) if check.count})
        # Every cache of paths keyed by the generations is told: each moved past where it stood.
        after = dict(self.query("SELECT name, value FROM generations"))
        self.assertTrue(all(after[name] > value for name, value in generations.items()), (generations, after))
        # The index, still running, writes its next batch with the roots the library has now.
        batch("After.jpg")
        self.assertIn("D:\\Harbour\\After.jpg", self.held())
        # The restore, undone from the copy it made of the library as it stood.
        self.restore(restored.details["before_restore"], now=NOON + DAY + 60)
        self.assertEqual(rooted, self.held())
        batch("Later.jpg")
        self.assertEqual("@pictures/Later.jpg", self.held()[-1])

    def test_a_write_prepared_before_the_restore_is_run_again_after_it(self):
        path = "D:\\Harbour\\Caught between.jpg"
        calls = []

        def operation(conn):
            calls.append(1)
            row = store_roots.to_row(conn, path)
            if len(calls) == 1:
                # The owner restores the library after this write converted its path and before it writes it.
                self.restore(self.monthly.name, now=NOON + DAY)
            conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')", (row,))

        db.write_with_connection(self.library.path, operation)
        self.assertEqual(2, len(calls))
        self.assertIn(path, self.held())
        self.assertNotIn("@pictures/Caught between.jpg", self.held())


class ASnapshotBelowTheBaseline(Base):
    """A snapshot made before schema 32 is still a file in the library's folder: it is listed, as too old, and restoring it is
    refused with a sentence before the library is touched or snapshotted (owner, 2026-10-15: nothing could bring it forward)."""

    def setUp(self):
        super().setUp()
        snapshots.take(self.library.path, now=NOON)
        self.daily = snapshots.listed(self.library.path, "daily")[0]
        conn = db.connect(self.daily.path)
        try:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (31, 'an old step', '2026-09-01 00:00:00')")
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()

    def cli(self, *args):
        with mock.patch.object(tagpup_cli, "console", tagpup_cli.Console(width=200)):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path] + list(args))

    def digest(self):
        schema.forget()
        conn = db.connect(self.library.path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        with open(self.library.path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_it_is_listed_as_too_old_and_listing_does_not_fail(self):
        [described] = [s for s in snapshot_service.listing(self.library)["snapshots"] if s["name"] == self.daily.name]
        self.assertEqual((31, True), (described["schema"], described["too_old"]))
        shown = self.cli("snapshots", "list")
        self.assertEqual(0, shown.exit_code, shown.output)
        self.assertIn("31 (too old to restore)", shown.output)
        self.assertIn("32", shown.output, "the weekly and monthly are copies of the same file, so also marked")

    def test_the_cli_refuses_a_snapshot_from_a_newer_tagpup_in_a_sentence_too(self):
        # The refusal comes before the disk is asked, so the command has no "needs" to print: it was a KeyError (a traceback).
        conn = db.connect(self.daily.path)
        try:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (99, 'a later step', '2026-10-09 00:00:00')")
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        said = self.cli("snapshots", "restore", self.daily.name, "--apply")
        self.assertEqual(1, said.exit_code, said.output)
        self.assertIn("made by a newer version of TagPup", " ".join(said.output.split()))
        self.assertNotIn("Traceback", said.output)
        self.assertIsNone(said.exception if isinstance(said.exception, KeyError) else None)

    def test_a_restore_of_it_is_refused_with_a_sentence_and_the_library_is_untouched(self):
        before, kept = self.digest(), self.names()
        for apply in (False, True):
            result = snapshot_service.restore(self.library, self.daily.name, apply=apply, now=NOON + 60)
            self.assertIn("older than TagPup can bring forward", result.refused)
            self.assertIn("restore a newer snapshot", result.refused)
            self.assertIn("The snapshot " + self.daily.name, result.refused)
            self.assertEqual(0, result.changed)
            self.assertNotIn("before_restore", result.details)
        self.assertEqual((before, kept), (self.digest(), self.names()), "nothing copied over it, and no snapshot of it taken")
        said = self.cli("snapshots", "restore", self.daily.name, "--apply")
        self.assertEqual(1, said.exit_code, said.output)
        self.assertIn("Refused", said.output)
        self.assertNotIn("Traceback", said.output)
        self.assertEqual((before, kept), (self.digest(), self.names()))


if __name__ == "__main__":
    unittest.main()

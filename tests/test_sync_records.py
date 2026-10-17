"""A library's record of its syncs is rows of `job_runs`, bounded, and kept apart from the other jobs (tagpup.store.job_runs,
tagpup.services.sync; migration 32 folded the table `sync_runs` into it).

A sync of the whole library is a run of the job "sync-whole", a sync of one folder of "sync-folder": the watcher syncs a folder
each time it settles, so these must not push out the whole library's (the table before kept its newest 500 and the newest whole
sync and in step, and held two of the whole library's in 500 on photo_index), must not push out another job's runs
(snapshots) nor be pushed out by them, and must not make the recurring job "sync", the daily catch-up, not due. "Last in step"
and the last whole sync outlive the pruning: the pages show them.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from test_sync import SyncTestCase  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import activity, sync  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, job_runs  # noqa: E402


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class TheRecordIsBounded(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="sync_records_")
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)

    def sync(self, minute, whole=False, in_step=True, found=None, changed=None):
        return job_runs.record_sync(self.path, "2026-09-26 09:%02d:00" % minute, whole=whole, in_step=in_step,
                                    found=found or {}, changed=changed or {})

    def last(self):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return job_runs.last_sync(conn), job_runs.newest_sync(conn, whole=True)
        finally:
            conn.close()

    def test_a_folders_syncs_do_not_push_out_the_whole_librarys(self):
        with mock.patch.object(job_runs, "SYNC_KEEP", 5):
            whole = self.sync(0, whole=True)
            for minute in range(1, 13):
                self.sync(minute, found={"new": 1})
        self.assertEqual(5, look(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = ?", (job_runs.SYNC_FOLDER,))[0][0])
        last, newest_whole = self.last()
        self.assertEqual(whole, newest_whole["id"])
        self.assertEqual("2026-09-26 09:12:00", last["last_run"]["started"], "the newest, of either")
        self.assertFalse(last["last_run"]["whole"])
        self.assertIsNotNone(last["last_in_step"])

    def test_the_default_keeps_hundreds(self):
        self.assertGreaterEqual(job_runs.SYNC_KEEP, 100)

    def test_last_in_step_is_found_past_syncs_that_were_not(self):
        job_runs.record_sync(self.path, "2026-09-20 08:00:00", True, True, {}, {})
        for day in range(21, 30):
            job_runs.record_sync(self.path, "2026-09-%d 08:00:00" % day, True, False, {"new": 1}, {})
        last, newest_whole = self.last()
        self.assertIsNotNone(last["last_in_step"])
        self.assertFalse(last["last_run"]["in_step"])
        self.assertFalse(newest_whole["in_step"])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(look(self.path, "SELECT finished FROM job_runs WHERE started = '2026-09-20 08:00:00'")[0][0],
                             job_runs.last_in_step(conn))
        finally:
            conn.close()

    def test_the_syncs_and_the_other_jobs_do_not_push_each_other_out(self):
        for n in range(job_runs.KEEP_RUNS):
            claim = job_runs.claim(self.path, "snapshots", "harbour", 1_700_000_000 + n * 86400)
            job_runs.finish(self.path, claim.run_id, "done", 1_700_000_100 + n * 86400, {"taken": 1})
        for minute in range(60):
            self.sync(minute, whole=bool(minute % 2))
        self.assertEqual(job_runs.KEEP_RUNS, look(self.path, "SELECT COUNT(*) FROM job_runs WHERE job = 'snapshots'")[0][0])
        self.assertEqual(60, look(self.path, "SELECT COUNT(*) FROM job_runs WHERE job IN (?, ?)", job_runs.SYNC_JOBS)[0][0])
        # And a job ending prunes its own: 60 more snapshot runs leave the syncs where they were.
        for n in range(60):
            claim = job_runs.claim(self.path, "snapshots", "harbour", 1_800_000_000 + n * 86400)
            job_runs.finish(self.path, claim.run_id, "done", 1_800_000_100 + n * 86400, {"taken": 1})
        self.assertEqual(60, look(self.path, "SELECT COUNT(*) FROM job_runs WHERE job IN (?, ?)", job_runs.SYNC_JOBS)[0][0])

    def test_a_sync_does_not_make_the_daily_catch_up_not_due(self):
        """The recurring job "sync" is claimed and ended as any job; a sync a person or the watcher ran is not one of its runs."""
        from tagpup.jobs import recurring
        self.sync(0, whole=True)
        self.assertEqual({}, {key: value for key, value in job_runs.latest(self.path).items() if key[0] == "sync"})
        job = recurring.JOBS.get("sync")
        self.assertTrue(recurring.is_due(job, None, 1_700_000_000))
        library = Library(self.path)
        listed = {entry["name"]: entry for entry in recurring.status(library)}
        self.assertIsNone(listed["sync"]["last"])

    def test_the_timeline_lists_a_sync_once_as_a_sync_and_not_as_a_job(self):
        self.sync(0, whole=True, found={"new": 0, "missing": 2}, changed={"rows": 1})
        entries = [entry for entry in activity.timeline(Library(self.path)) if entry["kind"] != "change"]
        self.assertEqual(["sync"], [entry["kind"] for entry in entries])
        self.assertEqual(2, entries[0]["counts"]["found_missing"])
        self.assertEqual(1, entries[0]["counts"]["rows"])
        self.assertEqual("in step", entries[0]["outcome"])
        self.assertEqual("sync:harbour:20260926T090000", entries[0]["run"])

    def test_recorded_syncs_never_push_the_jobs_out_of_the_timeline(self):
        for n in range(3):
            claim = job_runs.claim(self.path, "snapshots", "harbour", 1_700_000_000 + n * 86400)
            job_runs.finish(self.path, claim.run_id, "done", 1_700_000_100 + n * 86400, {"taken": 1})
        for minute in range(20):   # recorded after the jobs, and older by their times
            job_runs.record_sync(self.path, "2020-01-01 00:%02d:00" % minute, bool(minute % 2), True, {}, {})
        everything = activity.timeline(Library(self.path), limit=500)
        wanted = len([e for e in everything if e["kind"] == "change"]) + 3
        shown = activity.timeline(Library(self.path), limit=wanted)
        self.assertEqual(3, len([e for e in shown if e["kind"] == "job"]), "the 20 syncs took none of their places")


class WhichFolderSyncsAreRecorded(SyncTestCase):
    """The folder watcher syncs a folder each time it settles; a sync of one folder is recorded when it found or changed
    something, or left the library not in step, or ends a stretch of not being in step (worth_recording)."""

    def setUp(self):
        super().setUp()
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))

    def recorded(self):
        return look(self.db_path, "SELECT job FROM job_runs WHERE job IN (?, ?) ORDER BY id", job_runs.SYNC_JOBS)

    def test_a_folder_that_settled_on_nothing_is_not_recorded_and_the_whole_library_always_is(self):
        result = self.run_sync(apply=True, folder=self.meet)
        self.assertTrue(result.ok, result.message())
        self.assertTrue(result.details["in_step"])
        self.assertEqual([], self.recorded())
        self.assertNotIn("record", result.details)
        self.run_sync(apply=True)
        self.assertEqual([(job_runs.SYNC_WHOLE,)], self.recorded())
        self.run_sync(apply=True, folder=self.meet)
        self.assertEqual([(job_runs.SYNC_WHOLE,)], self.recorded(), "still nothing to say of the folder")

    def test_a_folder_that_found_something_is_recorded(self):
        self.photo(self.meet, "IMG_0009.jpg")   # new
        result = self.run_sync(apply=True, folder=self.meet)
        self.assertFalse(result.details["in_step"])
        self.assertEqual([(job_runs.SYNC_FOLDER,)], self.recorded())
        self.assertEqual(1, sync.last(self.library)["last_run"]["found"]["new"])

    def test_a_folder_that_changed_a_row_is_recorded_though_it_left_the_library_in_step(self):
        self.photo(self.meet, "IMG_0001.jpg", body=b"jpeg, edited", **{"XMP:Subject": ["Weather/Rain"]})
        result = self.run_sync(apply=True, folder=self.meet)
        self.assertTrue(result.details["in_step"], result.details)
        self.assertEqual(1, result.changed)
        self.assertEqual([(job_runs.SYNC_FOLDER,)], self.recorded())

    def test_a_folder_in_step_again_is_recorded_when_the_newest_record_says_it_was_not(self):
        """Otherwise the pages' 'in step' would stand on an older record: the last one said not in step, and nothing says it is."""
        self.photo(self.meet, "IMG_0009.jpg")   # new: not in step
        self.run_sync(apply=True, folder=self.meet)
        self.assertFalse(sync.last(self.library)["last_run"]["in_step"])
        os.remove(os.path.join(self.meet, "IMG_0009.jpg"))   # settled on nothing but what was
        result = self.run_sync(apply=True, folder=self.meet)
        self.assertTrue(result.details["in_step"])
        self.assertTrue(sync.last(self.library)["last_run"]["in_step"], "the record says so")
        self.assertEqual([(job_runs.SYNC_FOLDER,), (job_runs.SYNC_FOLDER,)], self.recorded())
        self.run_sync(apply=True, folder=self.meet)
        self.assertEqual(2, len(self.recorded()), "and then it settles on nothing again")

    def test_a_record_that_cannot_be_read_just_now_is_written(self):
        with mock.patch.object(sync.db, "readonly_uri", side_effect=OSError("locked")):
            self.assertTrue(sync.worth_recording(self.library, False, True, {}, 0, 0))

    def test_a_whole_sync_is_recent_by_its_record_and_a_folders_is_not(self):
        self.assertFalse(sync.synced_whole_within(self.library, 3600))
        job_runs.record_sync(self.db_path, job_runs.stamp(1_700_000_000), False, True, {}, {})
        self.assertFalse(sync.synced_whole_within(self.library, 10 ** 12), "a folder's sync is not the whole")
        self.run_sync(apply=True)
        self.assertTrue(sync.synced_whole_within(self.library, 3600))


if __name__ == "__main__":
    unittest.main()

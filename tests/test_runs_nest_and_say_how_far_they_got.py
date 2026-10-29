"""A run nests under the run that queued it and says how far it got (D9; DECISIONS 2026-10-28, "ACTIVITY REWORK";
docs/PLAN_ACTIVITY.md, B2; tagpup.services.job_runs, tagpup.store.job_runs, tagpup.core.runs.parent_run).

A run claimed while a run of the same library is under way on the thread is its child (`job_runs.parent`); a run of
another library, or a sync, which names no row while it runs, is not a parent. A library at 35, which the owner's server
has not yet opened, is never written by a claim: it is behind, and the claim says so. Libraries are made in homes of their
own; the names are fictional.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402

from tagpup.core import runs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.store import db, job_runs, schema  # noqa: E402

NOW = 1790000000.0


def rows(db_path, sql, params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class ARunNestsUnderTheRunThatQueuedIt(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.library = Library(self.home.library("harbour.db"))
        self.other = Library(self.home.library("lighthouse.db"))
        for each in (self.library, self.other):
            schema.ensure(each.path)

    def parent_of(self, run_id):
        return rows(self.library.path, "SELECT parent FROM job_runs WHERE id = ?", (run_id,))[0][0]

    def test_a_run_claimed_under_a_run_of_its_library_is_its_child_and_the_innermost_wins(self):
        sync = runs_service.claim(self.library, "sync", self.library.name, NOW)
        with runs.running(runs.job_tag(self.library.name, sync.run_id)):
            index = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
            with runs.running(runs.job_tag(self.library.name, index.run_id)):
                bake = runs_service.claim(self.library, "bake", self.library.name, NOW + 2)
        after = runs_service.claim(self.library, "snapshots", self.library.name, NOW + 3)
        self.assertEqual((None, sync.run_id, index.run_id, None),
                         tuple(self.parent_of(each.run_id) for each in (sync, index, bake, after)))

    def test_a_run_of_another_library_or_a_sync_is_not_a_parent(self):
        theirs = runs_service.claim(self.other, "sync", self.other.name, NOW)
        with runs.running(runs.job_tag(self.other.name, theirs.run_id),
                          runs.sync_tag(self.library.name, job_runs.stamp(NOW))):
            mine = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        self.assertIsNone(self.parent_of(mine.run_id))

    def test_a_process_started_for_a_run_claims_under_it(self):
        """The CLI's index, started by the queue, is told its runs in its environment (runs.ENV)."""
        sync = runs_service.claim(self.library, "sync", self.library.name, NOW)
        told = runs.job_tag(self.library.name, sync.run_id)
        previous = os.environ.get(runs.ENV)
        os.environ[runs.ENV] = told
        try:
            index = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        finally:
            if previous is None:
                os.environ.pop(runs.ENV)
            else:
                os.environ[runs.ENV] = previous
        self.assertEqual(sync.run_id, self.parent_of(index.run_id))

    def test_two_runs_of_one_job_the_second_is_refused_while_the_first_runs(self):
        first = runs_service.claim(self.library, "index", self.library.name, NOW)
        second = runs_service.claim(self.library, "index", self.library.name, NOW + 1)
        self.assertTrue(first)
        self.assertEqual("running", second.why)
        self.assertEqual([(1,)], rows(self.library.path, "SELECT COUNT(*) FROM job_runs"))

    def test_a_library_at_35_is_not_written_by_a_claim(self):
        """No writer reaches a library the owner's server has not migrated: a claim finds it behind and writes nothing."""
        at_35 = Library(self.home.library("breakwater.db"))
        with migration_names.up_to(35):
            schema.ensure(at_35.path)
        schema.forget()
        claim = runs_service.claim(at_35, "index", at_35.name, NOW)
        self.assertEqual(("behind", None), (claim.why, claim.run_id))
        self.assertEqual([(35, 0)], rows(at_35.path, "SELECT (SELECT MAX(version) FROM schema_version),"
                                                     " (SELECT COUNT(*) FROM job_runs)"))


if __name__ == "__main__":
    unittest.main()

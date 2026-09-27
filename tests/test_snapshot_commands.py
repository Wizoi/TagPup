"""The snapshots job and the commands (docs/ARCHITECTURE.md, phase 8): the recurring job
takes a library's snapshots and records what it took; `jobs`, `jobs run`, `snapshots
list` and `snapshots restore` list, run and restore them.
"""
import os
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_snapshots import NOON, Base  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import snapshots as snapshot_service  # noqa: E402
from tagpup.store import db, job_runs, snapshots  # noqa: E402


class TheJobAndTheCommands(Base):
    def cli(self, *args):
        with mock.patch.object(tagpup_cli, "console", tagpup_cli.Console(width=200)):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path] + list(args))

    def test_the_job_takes_a_snapshot_and_records_its_size(self):
        runner = recurring.Runner(lambda: [self.library], clock=lambda: NOON)
        ran = {o.job: o for o in runner.run_due() if o.ran}
        # Sync runs too, and fails here: this runner was given no Runtime.
        self.assertEqual({"snapshots", "prune-journal", "sync"}, set(ran))
        self.assertEqual(3, ran["snapshots"].result.changed)
        run = [r for r in job_runs.runs(self.library.path) if r.job == "snapshots"][0]
        self.assertEqual(("done", 3, snapshots.disk(self.library.path)),
                         (run.outcome, run.changed["changed"], run.changed["bytes"]))

    def test_a_snapshot_holds_no_run_still_running(self):
        # The snapshots job's own run is `running` while it copies the library, and was in
        # the copy: restored while this process lived, the job was held "running" by it
        # until the process ended (review of phase 8a, 1).
        runner = recurring.Runner(lambda: [self.library], clock=lambda: NOON)
        self.assertTrue(runner.run("snapshots", self.library, force=True)[0].ran)
        for snapshot in snapshots.listed(self.library.path):
            self.assertEqual([], self.query("SELECT id FROM job_runs WHERE outcome = 'running'", path=snapshot.path),
                             snapshot.name)
        restored = snapshot_service.restore(self.library, snapshots.listed(self.library.path, "daily")[0].name,
                                            apply=True, now=NOON + 60)
        self.assertEqual(1, restored.changed)
        self.assertEqual([], self.query("SELECT id FROM job_runs WHERE outcome = 'running'"))
        again = runner.run("snapshots", self.library, force=True)[0]
        self.assertEqual((True, None), (again.ran, again.why))

    def test_a_restore_leaves_no_run_the_snapshot_held_running(self):
        # A snapshot taken before this fix, or copied by hand, can still hold one.
        snapshots.take(self.library.path, now=NOON)
        daily = snapshots.listed(self.library.path, "daily")[0]
        conn = db.connect(daily.path)
        try:
            conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES"
                         " ('snapshots', 'harbour', '2026-09-01 12:00:00', 'running', ?)", (job_runs.file_journal.owner(),))
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        snapshot_service.restore(self.library, daily.name, apply=True, now=NOON + 60)
        self.assertEqual([("abandoned",)], self.query("SELECT outcome FROM job_runs"))

    def test_the_commands_list_run_and_restore(self):
        taken = self.cli("jobs", "run", "snapshots")
        self.assertEqual(0, taken.exit_code, taken.output)
        self.assertIn("snapshots for harbour: done, 3 changed", taken.output)
        listed = self.cli("jobs")
        self.assertEqual(0, listed.exit_code, listed.output)
        self.assertIn("snapshots", listed.output)
        self.assertIn("prune-journal", listed.output)

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

    def test_a_job_it_has_not_is_named(self):
        answer = self.cli("jobs", "run", "nosuch")
        self.assertNotEqual(0, answer.exit_code)
        self.assertIn("There is no recurring job nosuch", answer.output)


if __name__ == "__main__":
    unittest.main()

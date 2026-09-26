"""Only the web server -- the always-on process -- runs the recurring jobs (owner,
2026-09-26; docs/ARCHITECTURE.md, phase 8). The CLI ran what was due before every
command, so the first `stats` of a day waited 20 s for a snapshot and a look wrote to
the library; the MCP server ran them on a thread as it started. The CLI keeps `jobs` and
`jobs run NAME`, by hand.
"""
import os
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402


class NotTheCli(unittest.TestCase):
    def test_a_command_runs_no_recurring_job_first(self):
        home = own_home.for_test(self, prefix="jobs_")
        path = home.library("harbour.db")
        library_actions.create(path)
        with mock.patch.dict(os.environ, {runtimes.RUN_JOBS: "1"}), \
                mock.patch.object(tagpup_cli.runtimes, "recurring_jobs") as runner:
            answer = CliRunner().invoke(tagpup_cli.cli, ["--db", path, "history"])
        self.assertEqual(0, answer.exit_code, answer.output)
        runner.assert_not_called()


class NotTheMcpServer(unittest.TestCase):
    def test_it_starts_no_recurring_job(self):
        own_home.for_test(self, prefix="jobs_")
        with mock.patch.dict(os.environ, {runtimes.RUN_JOBS: "1"}), \
                mock.patch.object(server.runtimes, "recurring_jobs") as runner, \
                mock.patch.object(server.logs, "to_file", return_value="(no log file)"), \
                mock.patch.object(server, "build") as build:
            server.main()
        build.return_value.run.assert_called_once_with("stdio")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""scripts/install_app.py: the apps run from a copy of the code, against the checkout's home.

Run from the repository, every saved .py restarted TagPup and TagTuner and threw away
their in-memory state. An installed version is a copy nothing edits.
"""
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import install_app  # noqa: E402
sys.path.insert(0, WORKSPACE_DIR)
from tagpup.core import processes  # noqa: E402
from measure_identify_faces import free_port, remove_sandbox  # noqa: E402


class InstallCase(unittest.TestCase):
    def setUp(self):
        self.dest = tempfile.mkdtemp(prefix="tagpup_install_")
        self.home = tempfile.mkdtemp(prefix="tagpup_install_home_")
        self.addCleanup(remove_sandbox, self.home)
        self.addCleanup(remove_sandbox, self.dest)
        self.said = []

    def install(self, name=None, apply=True):
        return install_app.install(self.dest, self.home, sys.executable, name=name,
                                   apply=apply, say=self.said.append)


class WhatAnInstallIs(InstallCase):
    def test_a_dry_run_changes_nothing(self):
        shutil.rmtree(self.dest)
        self.install(apply=False)
        self.assertFalse(os.path.exists(self.dest))
        self.assertTrue(any("Dry run" in line for line in self.said))

    def test_a_version_holds_the_code_and_the_programs_and_nothing_else(self):
        name, _ = self.install()
        folder = os.path.join(self.dest, "versions", name)
        for part in ("scripts", "tagpup", os.path.join("web", "tagpup"), os.path.join("web", "tuner"), "tagpup_web.py",
                     "tagpup_cli.py", "runner.py", "VERSION.txt"):
            self.assertTrue(os.path.exists(os.path.join(folder, part)), part)
        # The libraries and the settings stay in the home.
        self.assertFalse(os.path.exists(os.path.join(folder, "data")))
        self.assertFalse(os.path.exists(os.path.join(folder, "config" + ".ini")))
        self.assertEqual(install_app.read_current(self.dest), name)

    def test_each_launcher_runs_the_current_version_against_the_home(self):
        self.install()
        for launcher, (script, args) in install_app.LAUNCHERS.items():
            with open(os.path.join(self.dest, launcher), encoding="utf-8", newline="") as handle:
                text = handle.read()
            self.assertIn('set "TAGPUP_HOME=%s"' % self.home, text)
            self.assertIn('"%s"' % sys.executable, text)
            # %~dp0 is the launcher's own folder, and ends in a backslash.
            self.assertIn('"%~dp0versions\\%TAGPUP_VERSION%\\' + script + '" ' + args, text)
            # It installs a new commit first, then reads which version to run.
            self.assertLess(text.index("--if-changed"), text.index("set /p TAGPUP_VERSION"))
            self.assertNotIn("\n", text.replace("\r\n", ""), "cmd.exe wants CRLF throughout")

    def test_a_version_is_named_for_when_and_which_commit(self):
        name = install_app.version_name(datetime.datetime(2026, 9, 23, 20, 5, 0))
        self.assertTrue(name.startswith("20260923-200500-"), name)
        self.assertGreater(len(name), len("20260923-200500-"))


class InstallingANewCommitAtStart(InstallCase):
    """What each launcher runs first: a merge reaches the apps at their next start."""

    def test_only_a_committed_checkout_on_another_commit_is_installed(self):
        self.assertTrue(install_app.stale("20260925-115722-0dd8402", "bfeb9b7", False))
        self.assertFalse(install_app.stale("20260925-115722-0dd8402", "0dd8402", False))
        self.assertFalse(install_app.stale("20260925-115722-0dd8402", "bfeb9b7", True),
                         "half an edit must never be installed")
        self.assertTrue(install_app.stale("20260925-115722-0dd8402-uncommitted", "0dd8402", False))
        self.assertFalse(install_app.stale("20260925-115722-0dd8402+", "0dd8402", False))
        self.assertTrue(install_app.stale(None, "0dd8402", False))
        self.assertFalse(install_app.stale("20260925-115722-0dd8402", "", False), "no git: leave it")

    def update(self, commit, dirty):
        answers = {"rev-parse": commit, "status": " M tagpup/x.py" if dirty else ""}
        with mock.patch.object(install_app, "git", side_effect=lambda *a: answers[a[0]]):
            return install_app.update(self.dest, self.home, sys.executable, say=self.said.append)

    def test_a_new_commit_is_installed_and_the_same_one_is_not(self):
        first = self.install(name="20260925-115722-0dd8402")[0]
        self.assertIsNone(self.update("0dd8402", False))
        self.assertEqual(first, install_app.read_current(self.dest))
        installed = self.update("bfeb9b7", False)
        self.assertEqual(installed, install_app.read_current(self.dest))
        self.assertNotEqual(first, installed)

    def test_an_edit_in_progress_starts_the_installed_version(self):
        first = self.install(name="20260925-115722-0dd8402")[0]
        self.assertIsNone(self.update("bfeb9b7", True))
        self.assertEqual(first, install_app.read_current(self.dest))
        self.assertTrue(any("uncommitted" in line for line in self.said))

    def test_a_failed_install_still_lets_the_app_start(self):
        first = self.install(name="20260925-115722-0dd8402")[0]
        with mock.patch.object(install_app, "copy_code", side_effect=OSError("disk full")):
            self.assertIsNone(self.update("bfeb9b7", False))
        self.assertEqual(first, install_app.read_current(self.dest))


class KeepingVersions(InstallCase):
    def test_installing_again_moves_to_the_new_version_and_keeps_three(self):
        names = ["20260101-000000-a", "20260102-000000-b", "20260103-000000-c",
                 "20260104-000000-d"]
        for name in names:
            self.install(name=name)
        self.assertEqual(install_app.versions(self.dest), names[1:])
        self.assertEqual(install_app.read_current(self.dest), names[-1])

    def test_the_version_being_replaced_is_never_removed(self):
        # It is what the running apps were started from.
        self.assertEqual(install_app.to_remove(["a", "b", "c", "d"], "e", "a"), ["b"])

    def test_two_installs_in_one_second_are_two_versions(self):
        first, _ = self.install(name="20260101-000000-a")
        second, _ = self.install(name="20260101-000000-a")
        self.assertNotEqual(first, second)
        self.assertEqual(len(install_app.versions(self.dest)), 2)


@unittest.skipUnless(os.name == "nt", "the launchers are cmd.exe scripts")
class TheInstalledAppRuns(InstallCase):
    """TagTuner.cmd, run for real: the installed code, against the home."""

    def stop(self, process):
        processes.kill_tree(process.pid)
        process.wait(timeout=30)

    def test_tagtuner_starts_from_the_install_and_keeps_its_data_in_the_home(self):
        self.install()
        port, other = free_port(), free_port()
        # As a restart: the launcher's --open would open a tab, and a restart never does.
        env = dict(os.environ, TAGPUP_WEB_RELOADED="1", TAGPUP_NO_MODEL_WEIGHTS="1")
        env.pop("TAGPUP_HOME", None)   # the launcher sets it
        process = processes.start(["cmd", "/c", os.path.join(self.dest, "TagTuner.cmd"),
                                    "--db", "installed", "--tuner-port", str(port),
                                    "--tagpup-port", str(other)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop, process)

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                self.assertIsNone(process.poll(), "the launcher exited")
                time.sleep(0.25)
        reply = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/api/databases" % port, timeout=30).read())

        self.assertIn("installed", reply["databases"])
        self.assertTrue(os.path.exists(os.path.join(self.home, "data", "installed.db")))
        self.assertTrue(os.path.exists(os.path.join(self.home, "data", "logs", "tagpup_web.log")))


if __name__ == "__main__":
    unittest.main()

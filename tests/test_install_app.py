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
        # The records of the servers running (tagpup.launcher): the test's, not the user's.
        servers = mock.patch.dict(os.environ, {"TAGPUP_SERVERS": os.path.join(self.home, "servers")})
        servers.start()
        self.addCleanup(servers.stop)
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

    def update(self, commit, dirty, newer=True):
        answers = {"rev-parse": commit, "status": " M tagpup/x.py" if dirty else ""}
        with mock.patch.object(install_app, "git", side_effect=lambda *a: answers[a[0]]), \
                mock.patch.object(install_app, "is_ancestor", return_value=newer):
            return install_app.update(self.dest, self.home, sys.executable, say=self.said.append)

    def test_an_older_or_unrelated_commit_is_never_installed(self):
        """A checkout moved back (a branch switched, a bisect) is not newer than what is
        installed, and the always-on process installs unattended."""
        first = self.install(name="20260925-115722-0dd8402")[0]
        self.assertIsNone(self.update("bfeb9b7", False, newer=False))
        self.assertEqual(first, install_app.read_current(self.dest))
        self.assertTrue(any("not newer" in line for line in self.said), self.said)

    def test_newer_is_asked_of_git(self):
        head = install_app.git("rev-parse", "--short", "HEAD")
        self.assertTrue(install_app.is_ancestor(head), "a commit is its own ancestor")
        self.assertTrue(install_app.is_ancestor(install_app.git("rev-parse", "--short", "HEAD~1")))
        self.assertFalse(install_app.is_ancestor("0000000"), "a commit git does not know")

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


class AVersionWithoutItsProgramsIsNeverCurrent(InstallCase):
    def test_the_launchers_keep_the_version_they_had(self):
        from unittest import mock
        first = self.install(name="20260925-115722-0dd8402")[0]
        # The copy lands somewhere else, leaving the version's folder empty.
        with mock.patch.object(install_app, "copy_code", side_effect=lambda dest, *a, **k: os.makedirs(dest)):
            with self.assertRaises(RuntimeError):
                self.install()
        self.assertEqual(first, install_app.read_current(self.dest))


class Shortcuts(InstallCase):
    """A .cmd cannot be pinned to the taskbar or given an icon; a shortcut can."""

    def test_each_app_gets_a_shortcut_with_its_icon(self):
        folder = tempfile.mkdtemp(prefix="tagpup_shortcuts_")
        self.addCleanup(remove_sandbox, folder)
        name = install_app.install(self.dest, self.home, sys.executable, apply=True,
                                   say=self.said.append, shortcuts_in=[folder])[0]
        # The code goes into the version, and nothing but the shortcuts into the folder:
        # the Start menu once got a copy of the code, and the version folder none.
        self.assertTrue(os.path.exists(os.path.join(self.dest, "versions", name, "tagpup_web.py")))
        self.assertEqual(sorted(install_app.SHORTCUTS), sorted(os.listdir(folder)))
        for name, (launcher, icon, _description) in install_app.SHORTCUTS.items():
            self.assertTrue(os.path.exists(os.path.join(folder, name)), name)
            self.assertTrue(os.path.exists(os.path.join(self.dest, icon)), icon)
            self.assertTrue(os.path.exists(os.path.join(self.dest, launcher)), launcher)

    def test_a_dry_run_makes_no_shortcut(self):
        folder = tempfile.mkdtemp(prefix="tagpup_shortcuts_")
        self.addCleanup(remove_sandbox, folder)
        install_app.install(self.dest, self.home, sys.executable, apply=False,
                            say=self.said.append, shortcuts_in=[folder])
        self.assertEqual([], os.listdir(folder))


class KeepingVersions(InstallCase):
    def test_installing_again_moves_to_the_new_version_and_keeps_three(self):
        names = ["20260101-000000-a", "20260102-000000-b", "20260103-000000-c",
                 "20260104-000000-d"]
        for name in names:
            self.install(name=name)
        self.assertEqual(install_app.versions(self.dest), names[1:])
        self.assertEqual(install_app.read_current(self.dest), names[-1])

    def test_the_versions_the_always_on_process_runs_are_never_removed(self):
        """A server running an old version while an update waits (a long index) had its
        code deleted from under it by the installs after."""
        from tagpup import supervisor
        names = ["20260101-000000-a", "20260102-000000-b", "20260103-000000-c"]
        for name in names:
            self.install(name=name)
        me = {"pid": os.getpid(), "started": processes.started(os.getpid())}
        data = os.path.join(self.home, "data")
        supervisor.write_json(os.path.join(data, supervisor.SERVER_FILE), dict(me, version=names[0], ports={}))
        supervisor.write_json(os.path.join(data, supervisor.STATE_FILE),
                              dict(me, state="running", version=names[1], server_version=names[0]))
        self.install(name="20260104-000000-d")
        self.install(name="20260105-000000-e")
        kept = install_app.versions(self.dest)
        self.assertIn(names[0], kept, "the server's version was removed")
        self.assertIn(names[1], kept, "the supervisor's version was removed")

    def test_the_version_being_replaced_is_never_removed(self):
        # It is what the running apps were started from.
        self.assertEqual(install_app.to_remove(["a", "b", "c", "d"], "e", "a"), ["b"])

    def test_two_installs_in_one_second_are_two_versions(self):
        first, _ = self.install(name="20260101-000000-a")
        second, _ = self.install(name="20260101-000000-a")
        self.assertNotEqual(first, second)
        self.assertEqual(len(install_app.versions(self.dest)), 2)


class AServerOfAnotherVersionRunning(InstallCase):
    """An install never stops a server; it says the next launch replaces it (tagpup.launcher)."""

    def test_an_install_names_each_server_of_another_version_and_what_replaces_it(self):
        from tagpup import launcher
        old = self.install(name="20260101-000000-a")[0]
        launcher.say_where({"tagpup": 7, "tuner": 8}, old, "t")   # as this process's server would
        self.addCleanup(launcher.forget, {"tagpup": 7, "tuner": 8})
        self.said.clear()
        new = self.install(name="20260102-000000-b")[0]
        running = [line for line in self.said if line.startswith("running")]
        self.assertEqual(1, len(running), self.said)
        self.assertIn(old, running[0])
        self.assertIn("next launch", running[0])
        self.assertIn(new, running[0])
        self.said.clear()
        launcher.say_where({"tagpup": 7, "tuner": 8}, new, "t")
        self.install(name="20260103-000000-c")
        self.assertTrue(any(line.startswith("running") for line in self.said), "the version before is not named")
        launcher.say_where({"tagpup": 7, "tuner": 8}, "20260103-000000-c", "t")
        self.said.clear()
        self.install(name="20260103-000000-c")   # the same version again, a second one
        self.assertTrue(any(line.startswith("running") for line in self.said))

    def test_the_version_a_launched_server_runs_is_never_removed(self):
        """Not only the always-on process's: a server started by TagPup.cmd runs from its
        version until the next launch replaces it."""
        from tagpup import launcher
        names = ["20260101-000000-a", "20260102-000000-b", "20260103-000000-c"]
        for name in names:
            self.install(name=name)
        launcher.say_where({"tagpup": 7}, names[0], "t")
        self.addCleanup(launcher.forget, {"tagpup": 7})
        self.install(name="20260104-000000-d")
        self.install(name="20260105-000000-e")
        self.assertIn(names[0], install_app.versions(self.dest), "the running server's version was removed")

    def test_a_launcher_waiting_for_another_install_says_so_first(self):
        """#749: the second window was blank for as long as the first install took."""
        import threading
        from tagpup import supervisor
        self.install(name="20260925-115722-0dd8402")
        held = supervisor.Lock(os.path.join(self.dest, install_app.INSTALL_LOCK))
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)   # a failure leaves no thread waiting on it
        answers = {"rev-parse": "0dd8402", "status": ""}
        with mock.patch.object(install_app, "git", side_effect=lambda *a: answers[a[0]]):
            waiting = threading.Thread(target=lambda: install_app.update(self.dest, self.home, sys.executable,
                                                                         say=self.said.append), daemon=True)
            waiting.start()
            deadline = time.time() + 30
            while not any("another install is running" in line for line in self.said):
                self.assertLess(time.time(), deadline, "nothing said while it waits: %s" % self.said)
                time.sleep(0.05)
            self.assertTrue(waiting.is_alive(), "it did not wait for the install under way")
            held.release()
            waiting.join(30)
        self.assertFalse(waiting.is_alive())

    def test_two_launchers_at_once_install_one_version(self):
        """TagPup.cmd and TagTuner.cmd clicked together both run the install first; the
        second waits for the first and finds its version installed."""
        import threading
        self.install(name="20260925-115722-0dd8402")
        made, gate = [], threading.Event()
        real_install = install_app.install

        def slow_install(*args, **kwargs):
            gate.set()
            time.sleep(1.0)
            made.append(real_install(*args, **kwargs)[0])
            return made[-1], []
        answers = {"rev-parse": "bfeb9b7", "status": ""}
        results = []
        with mock.patch.object(install_app, "git", side_effect=lambda *a: answers[a[0]]), \
                mock.patch.object(install_app, "is_ancestor", return_value=True), \
                mock.patch.object(install_app, "install", side_effect=slow_install):
            first = threading.Thread(target=lambda: results.append(
                install_app.update(self.dest, self.home, sys.executable, say=self.said.append)))
            first.start()
            self.assertTrue(gate.wait(30))
            results.append(install_app.update(self.dest, self.home, sys.executable, say=self.said.append))
            first.join(60)
        # Before, both installed, into one version's folder when in the same second: one
        # failed part-way ("could not install").
        self.assertEqual(1, len([line for line in self.said if "installing" in line]), self.said)
        self.assertFalse([line for line in self.said if "could not" in line], self.said)
        self.assertEqual(1, len(made), "two versions installed for one commit")
        self.assertEqual(made[0], install_app.read_current(self.dest))
        self.assertEqual(1, len([r for r in results if r]), results)


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
        env = dict(os.environ, TAGPUP_WEB_RELOADED="1", TAGPUP_NO_MODEL_WEIGHTS="1", TAGPUP_NO_JOBS="1")
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

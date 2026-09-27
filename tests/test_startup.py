"""scripts/startup.py: the always-on process from login (docs/ARCHITECTURE.md, phase 8).

Only ever against a Startup folder and an installed app of the test's own: the owner's
Startup folder, installed app and data/ are never touched. The installed app here holds
a stand-in for TagPup Background.pyw that runs the real supervisor over a server that
only sleeps -- what is tested is the shortcut, the mark, and starting and stopping.
"""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import startup  # noqa: E402
from tagpup import supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: The stand-in launcher: the real supervisor, its server a process that sleeps. Run by
#: pythonw.exe as the Startup shortcut runs the real one, in the test's home.
FAKE_LAUNCHER = r'''
import os, sys
sys.path.insert(0, %(root)r)
os.environ["TAGPUP_HOME"] = %(home)r
os.environ["TAGPUP_NO_JOBS"] = "1"
os.environ["TAGPUP_NO_MODEL_WEIGHTS"] = "1"
from tagpup import logs, supervisor
logs.to_file("supervisor")
python = supervisor.console_python()
made = supervisor.Supervisor(code_root=os.path.dirname(os.path.abspath(__file__)), hand_over=False, tick=0.05,
                             retry_move=0.1, command=lambda code: [python, "-c", "import time; time.sleep(600)"])
sys.exit(made.main(update_first=False))
'''


@unittest.skipUnless(os.name == "nt", "a Windows shortcut in a Startup folder")
class Startup(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="startup_")
        self.installed = os.path.join(self.home.root, "installed")
        self.folder = os.path.join(self.home.root, "Startup")
        os.makedirs(self.installed)
        os.makedirs(self.folder)
        with open(os.path.join(self.installed, supervisor.BACKGROUND_LAUNCHER), "w", encoding="utf-8") as handle:
            handle.write(FAKE_LAUNCHER % {"root": WORKSPACE_DIR, "home": self.home.root})
        shutil.copyfile(os.path.join(WORKSPACE_DIR, "web", "common", "icons", startup.ICON),
                        os.path.join(self.installed, startup.ICON))
        self.said = []
        self.addCleanup(self.end_any)

    def end_any(self):
        running = supervisor.running()
        if running:
            processes.kill_tree(running["pid"])

    def link(self):
        return os.path.join(self.folder, supervisor.STARTUP_SHORTCUT)

    def marker(self):
        return os.path.join(self.installed, supervisor.ALWAYS_ON)

    def install(self, apply):
        return startup.install(self.installed, self.folder, sys.executable, apply=apply, wait=60,
                               say=self.said.append)

    def test_a_dry_run_says_what_it_would_do_and_does_nothing(self):
        self.assertEqual(0, self.install(apply=False))
        self.assertEqual([], os.listdir(self.folder))
        self.assertFalse(os.path.exists(self.marker()))
        self.assertIsNone(supervisor.running())
        said = "\n".join(self.said)
        self.assertIn("Dry run", said)
        self.assertIn(self.link(), said)
        self.assertIn("pythonw.exe", said.lower())
        self.said.clear()
        self.assertEqual(0, startup.uninstall(self.installed, self.folder, apply=False, say=self.said.append))
        self.assertIn("Dry run", "\n".join(self.said))

    def test_install_makes_the_shortcut_marks_the_app_and_starts_it_and_uninstall_undoes_each(self):
        self.assertEqual(0, self.install(apply=True), "\n".join(self.said))
        self.assertEqual([supervisor.STARTUP_SHORTCUT], os.listdir(self.folder))
        self.assertTrue(supervisor.always_on(self.installed))
        running = supervisor.running()
        self.assertIsNotNone(running, "\n".join(self.said))
        self.assertTrue(any(line.startswith("started      pid %s," % running["pid"]) for line in self.said),
                        self.said)
        server_pid = startup.wait_until(lambda: (supervisor.running() or {}).get("server_pid"), 30)
        self.assertTrue(server_pid)

        # Installed again: nothing is started twice.
        self.said.clear()
        self.assertEqual(0, self.install(apply=True))
        self.assertTrue(any(line.startswith("running      already") for line in self.said), self.said)

        self.said.clear()
        self.assertEqual(0, startup.uninstall(self.installed, self.folder, apply=True, wait=60,
                                              say=self.said.append))
        self.assertEqual([], os.listdir(self.folder))
        self.assertFalse(os.path.exists(self.marker()))
        self.assertIsNone(supervisor.running())
        self.assertIn("stopped      pid %s" % running["pid"], self.said)
        self.assertEqual("stopped", supervisor.last_state()["state"])
        self.assertIsNone(processes.started(server_pid), "the server outlived its supervisor")

    def test_it_refuses_an_installed_app_without_the_launcher(self):
        os.remove(os.path.join(self.installed, supervisor.BACKGROUND_LAUNCHER))
        self.assertEqual(1, self.install(apply=True))
        self.assertEqual([], os.listdir(self.folder))
        self.assertIn("install it again first", "\n".join(self.said))

    def test_status_says_when_updates_are_refused(self):
        from tagpup.core import processes as core_processes
        me = {"pid": os.getpid(), "started": core_processes.started(os.getpid())}
        # Gone before the cleanup that ends a running supervisor: this "supervisor" is the test.
        self.addCleanup(supervisor.remove, supervisor.data_file(supervisor.STATE_FILE))
        supervisor.write_json(supervisor.data_file(supervisor.STATE_FILE), dict(
            me, state="running", version="v1", since="2026-09-26 10:00:00",
            update_refused={"said": "TagPup: the checkout's 1a2b3c4 is not newer than the installed 5d6e7f8, so it was not installed; starting the installed version.", "since": "2026-09-26 09:00:00"}))
        said = []
        startup.status(self.installed, self.folder, say=said.append)
        line = [line for line in said if line.startswith("updates")]
        self.assertEqual(1, len(line), said)
        self.assertIn("not newer", line[0])
        self.assertIn("2026-09-26 09:00:00", line[0])

    def test_status_says_what_is_there(self):
        said = []
        startup.status(self.installed, self.folder, say=said.append)
        self.assertIn("shortcut     none", said)
        self.assertIn("always on    no", said)
        self.assertIn("running      no", said)


class TheInstalledLauncher(unittest.TestCase):
    """What install_app writes as TagPup Background.pyw runs the version current.txt names."""

    def test_reads_current_txt_and_names_the_home(self):
        import install_app
        text = install_app.BACKGROUND.format(home="C:\\Users\\someone\\TagPup Home")
        compile(text, supervisor.BACKGROUND_LAUNCHER, "exec")
        self.assertIn("'C:\\\\Users\\\\someone\\\\TagPup Home'", text)
        self.assertIn("current.txt", text)
        self.assertIn('"--installed", HERE', text)

    def test_it_reaches_the_supervisor_of_the_current_version(self):
        sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
        import install_app
        from measure_identify_faces import remove_sandbox
        home = tempfile.mkdtemp(prefix="startup_home_")
        dest = tempfile.mkdtemp(prefix="startup_install_")
        self.addCleanup(remove_sandbox, home)
        self.addCleanup(remove_sandbox, dest)
        install_app.install(dest, home, sys.executable, apply=True, say=lambda line: None)
        launcher = os.path.join(dest, supervisor.BACKGROUND_LAUNCHER)
        self.assertTrue(os.path.exists(launcher))
        # --help: argparse answers and exits before anything starts.
        env = dict(os.environ, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1")
        done = processes.run([sys.executable, launcher, "--help"], env=env, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("always-on", done.stdout)
        self.assertIn("--installed", done.stdout)
        with open(os.path.join(dest, "TagPup.cmd"), encoding="utf-8") as handle:
            self.assertIn('--open tagpup --installed "%~dp0."', handle.read())


if __name__ == "__main__":
    unittest.main()

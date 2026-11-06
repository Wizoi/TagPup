"""ffmpeg is a prerequisite of the server and of the CLI's index runs (owner, 2026-11-06; tagpup.config.ffmpeg_problem).

The owner double-clicks TagPup on a machine without ffmpeg (or with one a broken upgrade left that does not run): the
window says what to install and stays open, and nothing was started -- no running server was asked to make way, no port
bound, no migration begun. An install hands over to a new version only when that version would start: one without
ffmpeg leaves the old server running and says why. The CLI's `index`, `thumbs warm` and `sync --apply` say the same
sentence and write nothing; a sync's dry run needs none. An install says it, and installs. With ffmpeg there, each
starts as it did.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

import install_app  # noqa: E402
import tagpup_web  # noqa: E402
from tagpup import config, launcher  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup_cli import cli  # noqa: E402

MISSING = config.FFMPEG_MISSING
BROKEN = config.FFMPEG_BROKEN % (r"C:\ffmpeg\bin\ffmpeg.exe", "it exited with 3221225781")
OLD, NEW = "20261106-090000-aaaaaaa", "20261106-100000-bbbbbbb"


def no_ffmpeg(test):
    """The machine's ffmpeg hidden, for this process and the ones it starts: PATH and winget's folder both empty."""
    folder = tempfile.mkdtemp(prefix="tagpup-no-ffmpeg-")
    test.addCleanup(shutil.rmtree, folder, True)
    hidden = mock.patch.dict(os.environ, {"PATH": folder, "LOCALAPPDATA": folder})
    hidden.start()
    test.addCleanup(hidden.stop)
    config._FFMPEG.clear()
    test.addCleanup(config._FFMPEG.clear)


class TheServer(unittest.TestCase):
    """tagpup_web.main: the check comes before anything is started (the pattern of TheServerBindsFirst)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="ffmpeg_server_")
        for patch in (mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"}),
                      mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")):
            patch.start()
            self.addCleanup(patch.stop)

    def start(self, problem, *args):
        printed = []
        with mock.patch.object(config, "ffmpeg_problem", return_value=problem), \
                mock.patch.object(tagpup_web, "print", side_effect=lambda line, **kw: printed.append(line), create=True), \
                mock.patch.object(tagpup_web.launcher, "make_way") as make_way, \
                mock.patch.object(tagpup_web.runtimes, "retire_run_logs") as retire, \
                mock.patch.object(tagpup_web.web, "bind_all", side_effect=OSError("in use")) as bind_all, \
                mock.patch.object(tagpup_web.library_actions, "bring_up_to_date_in_background") as migrating, \
                mock.patch.object(tagpup_web.runtimes, "background") as background, \
                mock.patch.object(tagpup_web.web, "create_app") as create_app, \
                mock.patch.object(tagpup_web.web, "serve") as serve:
            code = tagpup_web.main(["--tagpup-port", "7", "--tuner-port", "8"] + list(args))
        return code, printed, (make_way, retire, bind_all, migrating, background, create_app, serve)

    def test_without_ffmpeg_it_starts_nothing_and_says_what_to_install(self):
        for problem in (MISSING, BROKEN):
            with self.subTest(problem=problem[:20]):
                code, printed, began = self.start(problem, "--open", "tagpup")
                self.assertEqual(launcher.MISSING_PREREQUISITE, code)
                for each in began:
                    each.assert_not_called()
                self.assertEqual(["TagPup: Not started: " + problem], printed)

    def test_with_ffmpeg_it_goes_on_to_bind_as_before(self):
        code, _printed, (make_way, retire, bind_all, *_rest) = self.start(None)
        self.assertEqual(launcher.PORTS_TAKEN, code, "it went on to bind, and found the ports held")
        bind_all.assert_called_once()
        retire.assert_called_once()

    def test_check_says_the_sentence_on_stderr_and_exits_4(self):
        with mock.patch.object(config, "ffmpeg_problem", return_value=MISSING), \
                mock.patch.object(sys, "stderr") as stderr, \
                mock.patch.object(tagpup_web.web, "bind_all") as bind_all:
            self.assertEqual(4, tagpup_web.main(["--check"]))
        stderr.write.assert_any_call(MISSING)
        bind_all.assert_not_called()
        with mock.patch.object(config, "ffmpeg_problem", return_value=None):
            self.assertEqual(0, tagpup_web.main(["--check"]))


class AnInstallsHandOver(unittest.TestCase):
    """launcher.starts runs the new version's real tagpup_web.py --check; hand_over leaves the old server running."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="ffmpeg_handover_")
        with open(self.home.library("lib.db"), "wb"):
            pass
        self.installed = os.path.join(self.home.root, "installed")
        for version in (OLD, NEW):
            os.makedirs(os.path.join(self.installed, "versions", version))
        with open(os.path.join(self.installed, "current.txt"), "w", encoding="utf-8") as handle:
            handle.write(NEW)

    def test_a_version_without_ffmpeg_does_not_start_and_the_check_imports_every_module(self):
        no_ffmpeg(self)
        self.assertEqual("it exited with 4: " + MISSING, launcher.starts(sys.executable, WORKSPACE_DIR, self.home.root))

    def test_a_version_with_ffmpeg_starts(self):
        if config.ffmpeg_problem():
            self.skipTest("this machine has no ffmpeg")
        self.assertIsNone(launcher.starts(sys.executable, WORKSPACE_DIR, self.home.root))

    def test_the_old_server_is_left_running_and_the_install_says_why(self):
        said = []
        record = {"pid": 4242, "started": 1, "ports": {"tagpup": 5, "tuner": 6}, "version": OLD, "token": "t"}
        stood_in = {"ask": lambda port, timeout=None: {"version": OLD}, "running": lambda: [record],
                    "record": lambda port: record, "answering": lambda port: True,
                    "starts": lambda *a: "it exited with 4: " + MISSING}
        touched = {name: mock.MagicMock() for name in ("drain", "end", "start_server", "make_way")}
        for name, value in list(stood_in.items()) + list(touched.items()):
            patcher = mock.patch.object(launcher, name, side_effect=value) if callable(value) and name in stood_in \
                else mock.patch.object(launcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertFalse(launcher.hand_over(self.installed, sys.executable, self.home.root, say=said.append,
                                            sleep=lambda s: None, clock=lambda: 0.0))
        for each in touched.values():
            each.assert_not_called()
        self.assertIn("does not start (it exited with 4: " + MISSING, said[-1])
        self.assertIn("TagPup %s was left running" % OLD, said[-1])


class TheCli(unittest.TestCase):
    """index, thumbs warm and sync --apply: the sentence, exit 1, nothing written."""

    def setUp(self):
        self.vl = ViewLibrary(self)
        self.vl.photo("Regatta", "boat.jpg", real=True, size=(400, 300))

    def files(self):
        """Every file of the test's home but the logs and SQLite's shared-memory index (a read touches it), by its
        bytes: what a run wrote."""
        found = {}
        for folder, _dirs, names in os.walk(self.vl.home.root):
            if os.sep + "logs" in folder:
                continue
            for name in names:
                if name.endswith("-shm"):
                    continue
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    found[path] = hashlib.sha256(handle.read()).hexdigest()
        return found

    def run_cli(self, problem, *args):
        with mock.patch.object(config, "ffmpeg_problem", return_value=problem):
            return CliRunner().invoke(cli, ["--db", self.vl.path] + list(args))

    def test_each_index_run_is_refused_with_the_sentence_and_writes_nothing(self):
        folder = os.path.join(self.vl.pictures, "Regatta")
        before = self.files()
        for args in (("index", folder), ("thumbs", "warm"), ("thumbs", "warm", "--apply"), ("sync", "--apply")):
            for problem in (MISSING, BROKEN):
                with self.subTest(args=args, problem=problem[:20]):
                    result = self.run_cli(problem, *args)
                    self.assertEqual(1, result.exit_code, result.output)
                    self.assertEqual("Not run: " + problem, " ".join(result.output.split()))
        after = self.files()
        self.assertEqual(before, after, [path for path in set(before) | set(after) if before.get(path) != after.get(path)])
        self.assertFalse(os.path.exists(self.vl.library.thumbs))

    def test_a_sync_dry_run_needs_no_ffmpeg_and_with_ffmpeg_warm_runs(self):
        result = self.run_cli(MISSING, "sync")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn("Not run", result.output)
        result = self.run_cli(None, "thumbs", "warm")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("1 photo(s) looked at", " ".join(result.output.split()))


class AnInstall(unittest.TestCase):
    """install() says it when ffmpeg is missing, and installs; the server launchers pause on the refusal."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="ffmpeg_install_")
        self.installed = os.path.join(self.home.root, "installed")

    def install(self, problem):
        said = []
        with mock.patch.object(config, "ffmpeg_problem", return_value=problem):
            name, _removed = install_app.install(self.installed, self.home.root, sys.executable, apply=True,
                                                 say=said.append)
        return name, said

    def test_without_ffmpeg_it_installs_and_says_so(self):
        name, said = self.install(MISSING)
        self.assertEqual(name, install_app.read_current(self.installed), "installed all the same")
        self.assertEqual(MISSING + ". TagPup and the CLI's index will not start until it is installed.", said[-1])
        for launcher_name, pauses in (("TagPup.cmd", True), ("TagTuner.cmd", True), ("TagPup CLI.cmd", False)):
            with open(os.path.join(self.installed, launcher_name), encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(pauses, "if %ERRORLEVEL% EQU 4 pause" in text, launcher_name)

    def test_with_ffmpeg_it_says_nothing_of_it(self):
        _name, said = self.install(None)
        self.assertFalse([line for line in said if "winget" in line or "will not start" in line], said)


class TheCheckItself(unittest.TestCase):
    """config.ffmpeg_problem: not found, found and does not run, found and runs (kept for the process)."""

    def setUp(self):
        no_ffmpeg(self)
        config._FFMPEG_RUNS.clear()
        self.addCleanup(config._FFMPEG_RUNS.clear)
        self.bin = os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "WinGet", "Packages",
                                "Gyan.FFmpeg_Microsoft.Winget.Source_x", "ffmpeg-8.1-full_build", "bin")

    def install(self):
        os.makedirs(self.bin)
        for name in ("ffmpeg", "ffprobe"):
            open(os.path.join(self.bin, name + (".exe" if os.name == "nt" else "")), "wb").close()

    def test_not_found(self):
        self.assertEqual(MISSING, config.ffmpeg_problem())

    def test_found_and_not_a_program_does_not_run(self):
        self.install()   # empty files: not programs
        problem = config.ffmpeg_problem()
        self.assertTrue(problem.startswith("ffmpeg was found at %s" % os.path.join(self.bin, "ffmpeg")), problem)
        self.assertIn("but does not run", problem)
        self.assertIn("winget uninstall Gyan.FFmpeg, then winget install Gyan.FFmpeg", problem)

    def test_a_program_that_fails_or_hangs_does_not_run(self):
        self.install()
        for answer, why in ((subprocess.CompletedProcess([], 1), "it exited with 1"),
                            (subprocess.TimeoutExpired("ffmpeg", 30), "it gave no answer in 30 seconds")):
            with self.subTest(why=why), mock.patch.object(processes, "run", side_effect=[answer]):
                self.assertIn("but does not run (%s)" % why, config.ffmpeg_problem())

    def test_one_that_runs_is_kept_for_the_process(self):
        self.install()
        ran = []

        def run(args, **kwargs):
            ran.append(os.path.basename(args[0]))
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(processes, "run", side_effect=run):
            self.assertIsNone(config.ffmpeg_problem())
            self.assertIsNone(config.ffmpeg_problem())
        self.assertEqual(2, len(ran), "ffmpeg and ffprobe, once each: the pass is kept")
        self.assertTrue(all(name.startswith(("ffmpeg", "ffprobe")) for name in ran), ran)


if __name__ == "__main__":
    unittest.main()

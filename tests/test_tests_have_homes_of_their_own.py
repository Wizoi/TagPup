"""A test, a measurement's sandbox and a test's processes each live in a home of their own, and leave none behind.

No test uses the checkout's data/ folder or config.ini (docs/findings.md, #14): they are the folder and
settings of the app somebody is using. Tests made their libraries in data/ and wrote config.ini; their files
turned up in the library picker, a library left by a run on a later schema broke a run on an earlier one, and
the files that used the fixed names could not run at once. A test that needs a library, settings, logs or
anything beside them takes a home from tests/own_home.py. What counts as naming the checkout's is
tools/run_tests.py's SHARED, which also decides whether a file must run alone.

A measurement's sandbox is a home of its own as well (#796): its server wrote its records of where it answers
into the owner's %LOCALAPPDATA%\\TagPup\\servers and took the owner's turn on the graphics card. One helper
(tagpup.config.own_home_environment) says what a home is; no script spells it by hand, no spawn in scripts/
lacks it, no sandbox server runs the recurring jobs (#324), and a server started as the sandboxes start theirs,
under an owner's profile of the test's own, writes nothing in it.

A test that starts servers ends every process it started, and every one they started, however it ends
(own_home.end_processes, #727): a hand-over test once left four running for hours. The reaper that removes the
homes after the run outlives the test process on purpose and says nothing of it (#743).

Lifted: the three files this one took in (sandboxes, ended processes, the reaper) and their separate "the guard sees it"
tests, now asserted beside the scan they check. Each rule fails when its break is made in a scratch copy: a test file
naming config.ini; a sandbox server started without TAGPUP_NO_JOBS; a script spawn with env=dict(os.environ); a script
writing TAGPUP_HOME = sandbox; TAGPUP_SERVERS dropped from own_home_environment; end_processes killing nothing; the
reaper's handle left running (returncode not set).
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(WORKSPACE_DIR, "tests")
for folder in (WORKSPACE_DIR, os.path.join(WORKSPACE_DIR, "scripts"), os.path.join(WORKSPACE_DIR, "tools"), TESTS):
    sys.path.insert(0, folder)

import own_home  # noqa: E402
import run_tests  # noqa: E402
import sandbox  # noqa: E402
from free_port import free_port  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup.core import processes  # noqa: E402

Q = '"'   # so that the spellings below are not themselves what they describe

#: A sandbox's environment spelled by hand: its home alone, the user's folders left as they were.
BY_HAND = re.compile(r"""TAGPUP_HOME\s*=\s*(sandbox|home)\b|environ\[["']TAGPUP_HOME["']\]\s*=""")

#: Spawns in scripts/ that need no home of their own, and why: the reloader passes the environment of the
#: process it restarts on unchanged.
SPAWN_EXCEPTIONS = {"reloader.py": "restarts the process it runs in"}

#: A query of the machine, not a TagPup process: git, powershell, nvidia-smi (the card's use, sandbox.Sampler).
QUERIES = ("git", "powershell", "nvidia-smi")

#: Starts a process of its own from the same folder and exits, leaving it running: what a process handing over does.
STARTER = '''import os, sys
sys.path.insert(0, %r)
from tagpup.core import processes
here = os.path.dirname(os.path.abspath(__file__))
processes.start([sys.executable, os.path.join(here, "waits.py")], own_group=True)
''' % WORKSPACE_DIR

#: Starts the reaper as the first test home does, collects garbage, and ends as a test process does.
REAPER_PROGRAM = """
import gc, os, sys, tempfile
sys.path.insert(0, %r)
import own_home
own_home._reap_after_exit(tempfile.mkdtemp(prefix="tagpup_reaper_quiet_"))
gc.collect()
print("collected")
"""


def source(name):
    with open(os.path.join(TESTS, name), encoding="utf-8") as handle:
        return handle.read()


def scripts():
    """(name, source) of every program in scripts/."""
    folder = os.path.join(WORKSPACE_DIR, "scripts")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                yield name, handle.read()


def _starts(tree):
    """Each call of processes.start / processes.run in `tree`."""
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("start", "run")
            and getattr(node.func.value, "id", None) == "processes"]


def spawns_without_a_home(text):
    """Each processes.start / run in `text` whose env is not a call of `environment(...)` of a sandbox: [line]."""
    found = []
    for node in _starts(ast.parse(text)):
        command = node.args[0] if node.args else None
        while isinstance(command, ast.BinOp):   # ["git", ...] + list(args)
            command = command.left
        if (isinstance(command, ast.List) and command.elts and isinstance(command.elts[0], ast.Constant)
                and command.elts[0].value in QUERIES):
            continue
        env = [kw.value for kw in node.keywords if kw.arg == "env"]
        if not (env and isinstance(env[0], ast.Call) and getattr(env[0].func, "id", None) == "environment"):
            found.append(node.lineno)
        elif env[0].args and isinstance(env[0].args[0], ast.Call) and getattr(env[0].args[0].func, "attr", None) == "home":
            found.append(node.lineno)   # the owner's home is not a sandbox's (#817)
    return found


def servers_with_jobs(text):
    """Each processes.start in `text` of tagpup_web.py whose environment does not set TAGPUP_NO_JOBS: [line]. A
    server in a sandbox would snapshot the library copy a minute in (#324)."""
    found = []
    for node in _starts(ast.parse(text)):
        if node.func.attr != "start" or not node.args or "tagpup_web.py" not in ast.dump(node.args[0]):
            continue
        env = [kw.value for kw in node.keywords if kw.arg == "env"]
        if not (env and isinstance(env[0], ast.Call) and any(kw.arg == "TAGPUP_NO_JOBS" for kw in env[0].keywords)):
            found.append(node.lineno)
    return found


class NoTestNamesTheCheckoutsDataOrSettings(unittest.TestCase):
    def test_no_file_in_tests_does_so_the_runner_runs_none_of_them_alone(self):
        found = [name for name in sorted(os.listdir(TESTS))
                 if name.endswith(".py") and run_tests.SHARED.search(source(name))
                 and name[:-3] not in run_tests.READS_CODE_NOT_DATA]
        self.assertEqual([], found, "give these a home of their own (tests/own_home.py)")
        self.assertEqual([], [module for module in run_tests.test_files() if run_tests.uses_the_checkout(module)])

    def test_each_exemption_is_a_file_that_still_needs_it(self):
        for module in run_tests.READS_CODE_NOT_DATA:
            self.assertTrue(run_tests.SHARED.search(source(module + ".py")),
                            "%s no longer names config.ini; drop it from READS_CODE_NOT_DATA" % module)

    def test_what_counts_as_naming_them(self):
        for text in ("os.path.join(WORKSPACE_DIR, %sdata%s, %stest_x.db%s)" % (Q, Q, Q, Q),
                     "os.path.join(project_root, %sdata%s, %stest_x.db%s)" % (Q, Q, Q, Q),
                     "os.path.join(WORKSPACE_DIR, %sconfig.ini%s)" % (Q, Q),
                     "os.path.join(%sdata%s, %sphoto_index.db%s)" % (Q, Q, Q, Q),
                     "set_active_db_path(%sdata/regatta.db%s)" % (Q, Q),
                     "open(%sconfig.ini%s)" % (Q, Q)):
            self.assertTrue(run_tests.SHARED.search(text), text)
        for text in ("home.library(%stest_x.db%s)" % (Q, Q),
                     "os.path.join(self.home, %sdata%s, %sharbour.db%s)" % (Q, Q, Q, Q),
                     "config.config_path(self.home)",
                     "re.compile(r%sdata%s.get%s)" % (Q, chr(92), Q),
                     "the checkout's config.ini, in a sentence"):
            self.assertFalse(run_tests.SHARED.search(text), text)

    def test_a_home_is_tagpup_home_while_it_is_open_and_gone_after(self):
        before = os.environ.get("TAGPUP_HOME")
        home = own_home.OwnHome("tagpup_own_home_")
        try:
            self.assertEqual(os.path.normcase(tagpup_config.home()), os.path.normcase(home.root))
            self.assertEqual(os.path.normcase(tagpup_config.data_dir()), os.path.normcase(home.data))
            self.assertEqual(os.path.normcase(tagpup_config.library_path("harbour.db")),
                             os.path.normcase(home.library("harbour.db")))
        finally:
            home.close()
        self.assertEqual(before, os.environ.get("TAGPUP_HOME"))
        self.assertFalse(os.path.exists(home.root))

    def test_one_for_a_test_goes_when_the_test_does(self):
        test = unittest.TestCase()
        home = own_home.for_test(test)
        self.assertTrue(os.path.isdir(home.data))
        test.doCleanups()
        self.assertFalse(os.path.exists(home.root))


class NoSandboxIsMadeByHand(unittest.TestCase):
    def test_a_sandbox_server_runs_no_recurring_jobs(self):
        found = ["%s:%s" % (name, lines) for name, text in scripts() for lines in [servers_with_jobs(text)] if lines]
        self.assertEqual([], found, "pass environment(sandbox, TAGPUP_NO_JOBS='1')")
        start = "processes.start([sys.executable, os.path.join(s, 'tagpup_web.py')], env=%s)" + chr(10)
        self.assertEqual([1], servers_with_jobs(start % "environment(s)"))
        self.assertEqual([], servers_with_jobs(start % "environment(s, TAGPUP_NO_JOBS='1')"))
        self.assertEqual([], servers_with_jobs("processes.start(['git', 'status'])" + chr(10)))

    def test_every_spawn_in_scripts_runs_in_a_home_of_its_own(self):
        found = ["%s:%s" % (name, lines) for name, text in scripts() for lines in [spawns_without_a_home(text)]
                 if lines and name not in SPAWN_EXCEPTIONS]
        self.assertEqual([], found, "pass env=sandbox.environment(home), or list the script in SPAWN_EXCEPTIONS with why")
        self.assertEqual([2], spawns_without_a_home("x = 1\nprocesses.start(cmd)\n"))
        self.assertEqual([1], spawns_without_a_home("processes.run(cmd, env=dict(os.environ))\n"))
        self.assertEqual([], spawns_without_a_home("processes.start(cmd, env=environment(sandbox))\n"))
        self.assertEqual([1], spawns_without_a_home("processes.start(cmd, env=environment(tagpup_config.home()))\n"))

    def test_every_script_takes_its_sandboxs_environment_from_the_helper(self):
        found = ["%s:%d" % (name, number) for name, text in scripts() if name != "install_app.py"   # its launchers name the owner's home
                 for number, line in enumerate(text.splitlines(), 1) if BY_HAND.search(line)]
        self.assertEqual([], found, "use sandbox.environment(sandbox) or sandbox.enter(sandbox)")

    def test_a_sandbox_that_loads_models_shares_the_owners_card_line(self):
        from tagpup.ml import gpu
        with mock.patch.dict(os.environ, {gpu.ENV: "elsewhere"}):
            self.assertNotIn(gpu.ENV, sandbox.environment(r"C:\x"))
            sandbox.enter(r"C:\x")
            self.assertNotIn(gpu.ENV, os.environ)
            for name in ("TAGPUP_SERVERS", "TAGPUP_HOME", "TAGPUP_DOWNLOADS", "TAGPUP_RECYCLE_BIN"):
                os.environ.pop(name, None)

    def test_a_tests_home_is_what_the_helper_says_and_it_moves_every_folder_of_the_users_own(self):
        home = own_home.for_test(self, prefix="sandbox_helper_")
        for name, value in tagpup_config.own_home_environment(home.root).items():
            self.assertEqual(value, os.environ.get(name), name)
        env = tagpup_config.own_home_environment(r"C:\x")
        self.assertEqual({"TAGPUP_HOME", "TAGPUP_SERVERS", "TAGPUP_GPU_LOCK", "TAGPUP_DOWNLOADS", "TAGPUP_RECYCLE_BIN"},
                         set(env))
        for name in ("TAGPUP_SERVERS", "TAGPUP_GPU_LOCK", "TAGPUP_DOWNLOADS"):
            self.assertEqual(os.path.normcase(r"C:\x"), os.path.normcase(os.path.dirname(env[name])), name)

    @unittest.skipUnless(os.name == "nt", "the owner's folders are Windows'")
    def test_a_sandbox_server_writes_nothing_under_the_owners_folders(self):
        home = own_home.for_test(self, prefix="sandbox_server_")
        self.addCleanup(own_home.end_processes, home.root)
        owner = os.path.join(home.root, "owner_profile")
        os.makedirs(owner)
        # The owner's shell: their profile (here the test's), none of a home's folders set.
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": owner}):
            for name in tagpup_config.own_home_environment(home.root):
                os.environ.pop(name, None)
            env = sandbox.environment(home.root, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1",
                                      TAGPUP_WEB_NO_WARMUP="1", TAGPUP_WEB_RELOADED="1")
        ports = [free_port(), free_port()]
        log = open(os.path.join(home.root, "server.log"), "wb")
        self.addCleanup(log.close)
        server = processes.start([sys.executable, os.path.join(WORKSPACE_DIR, "tagpup_web.py"),
                                  "--tagpup-port", str(ports[0]), "--tuner-port", str(ports[1])],
                                 env=env, cwd=home.root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: server.poll() is None and processes.kill_tree(server.pid))
        deadline = time.time() + 90
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % ports[0], timeout=5) as reply:
                    json.loads(reply.read().decode("utf-8"))
                break
            except OSError:
                self.assertIsNone(server.poll(), "the server exited")
                self.assertLess(time.time(), deadline, "the server never answered")
                time.sleep(0.2)
        # Its records are the sandbox's (written once it serves, before it answers) ... and nothing is under the owner's profile.
        self.assertEqual(sorted("%d.json" % port for port in ports),
                         sorted(os.listdir(os.path.join(home.root, "servers"))))
        self.assertEqual([], [os.path.join(r, n) for r, _d, names in os.walk(owner) for n in names])
        self.assertEqual([], os.listdir(owner))


@unittest.skipUnless(os.name == "nt", "a process's command line is read from Windows")
class EndingWhatATestStarted(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="ended_")
        self.addCleanup(own_home.end_processes, self.home.root)

    def test_a_process_left_by_one_the_test_started_is_ended_too(self):
        for name, text in (("starts.py", STARTER), ("waits.py", "import time\ntime.sleep(600)\n")):
            with open(os.path.join(self.home.root, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        first = processes.start([sys.executable, os.path.join(self.home.root, "starts.py")],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: first.poll() is None and processes.kill_tree(first.pid))
        self.assertEqual(0, first.wait(timeout=60))
        # What the test knows of has ended; what it left has not, and no record names it.
        deadline = time.time() + 30
        while not own_home._named_under(self.home.root):
            self.assertLess(time.time(), deadline, "the process it starts never ran")
            time.sleep(0.2)
        self.assertTrue(own_home.end_processes(self.home.root))
        self.assertEqual([], own_home._named_under(self.home.root), "a process started from the home still runs")

    def test_a_server_named_only_by_its_record_is_ended(self):
        """A server run from the code's folder names no home on its command line."""
        script = ("import sys, time; sys.path.insert(0, %r); from tagpup import launcher; "
                  "launcher.say_where({'tagpup': 1}, None, 't'); time.sleep(600)" % WORKSPACE_DIR)
        server = processes.start([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: server.poll() is None and processes.kill_tree(server.pid))
        deadline = time.time() + 60
        while launcher.record(1) is None:
            self.assertIsNone(server.poll(), "it did not start")
            self.assertLess(time.time(), deadline, "it never said where it answers")
            time.sleep(0.1)
        recorded = launcher.record(1)["pid"]
        self.assertTrue(own_home.ran_processes(self.home.root))
        own_home.end_processes(self.home.root)
        self.assertIsNotNone(server.wait(timeout=30))
        self.assertIsNone(processes.started(recorded))


class TheReaperIsHeld(unittest.TestCase):
    def test_collecting_garbage_while_it_runs_warns_of_nothing(self):
        done = processes.run([sys.executable, "-W", "default", "-c", REAPER_PROGRAM % TESTS],
                             capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
        self.assertIn("collected", done.stdout, done.stderr)
        self.assertNotIn("ResourceWarning", done.stderr)


if __name__ == "__main__":
    unittest.main()

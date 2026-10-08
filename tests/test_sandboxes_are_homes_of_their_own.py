"""A measurement's sandbox is a home of its own, as a test's is (docs/findings.md, #796).

A measurement run (scripts/measure_identify_faces.py) started its sandbox's server with only
TAGPUP_HOME set: the server wrote its records of where it answers into the owner's
%LOCALAPPDATA%\\TagPup\\servers -- what a launch or an install hands over -- left them there
when it ended, and took the owner's turn on the graphics card. A test's home had moved those
folders into itself (tests/own_home.py); the sandboxes did not. Now one helper says what a
home of its own is (tagpup.config.own_home_environment), and both use it.

Here: no script builds a sandbox's environment by hand; a test's home is what the helper
says; and a server started as the sandboxes start theirs, under an owner's profile of the
test's own, writes nothing in it.
"""
import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import own_home  # noqa: E402
from free_port import free_port  # noqa: E402

import sandbox  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: A sandbox's environment spelled by hand: its home alone, the user's folders left as they were.
BY_HAND = re.compile(r"""TAGPUP_HOME\s*=\s*(sandbox|home)\b|environ\[["']TAGPUP_HOME["']\]\s*=""")


#: Spawns in scripts/ that need no home of their own, and why: a query or git, and the reloader, which
#: passes the environment of the process it restarts on unchanged.
SPAWN_EXCEPTIONS = {"reloader.py": "restarts the process it runs in"}

#: A query of the machine, not a TagPup process: git, powershell.
QUERIES = ("git", "powershell")


def spawns_without_a_home(source):
    """Each call of processes.start or processes.run in `source` whose env is not a call of
    `environment(...)`: [line]."""
    import ast
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("start", "run")
                and getattr(node.func.value, "id", None) == "processes"):
            continue
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


def servers_with_jobs(source):
    """Each call of processes.start in `source` that starts tagpup_web.py with an environment that does not
    set TAGPUP_NO_JOBS: [line]. A server in a sandbox would snapshot the library copy a minute in (#324)."""
    import ast
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "start"
                and getattr(node.func.value, "id", None) == "processes" and node.args):
            continue
        if "tagpup_web.py" not in ast.dump(node.args[0]):
            continue
        env = [kw.value for kw in node.keywords if kw.arg == "env"]
        if not (env and isinstance(env[0], ast.Call) and any(kw.arg == "TAGPUP_NO_JOBS" for kw in env[0].keywords)):
            found.append(node.lineno)
    return found


class NoSandboxIsMadeByHand(unittest.TestCase):
    def test_a_sandbox_server_runs_no_recurring_jobs(self):
        found = []
        folder = os.path.join(WORKSPACE_DIR, "scripts")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                lines = servers_with_jobs(handle.read())
            if lines:
                found.append("%s:%s" % (name, ", ".join(map(str, lines))))
        self.assertEqual([], found, "pass environment(sandbox, TAGPUP_NO_JOBS='1')")

    def test_the_guard_sees_a_server_with_jobs(self):
        start = "processes.start([sys.executable, os.path.join(s, 'tagpup_web.py')], env=%s)" + chr(10)
        self.assertEqual([1], servers_with_jobs(start % "environment(s)"))
        self.assertEqual([], servers_with_jobs(start % "environment(s, TAGPUP_NO_JOBS='1')"))
        self.assertEqual([], servers_with_jobs("processes.start(['git', 'status'])" + chr(10)))

    def test_every_spawn_in_scripts_runs_in_a_home_of_its_own(self):
        found = []
        folder = os.path.join(WORKSPACE_DIR, "scripts")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                lines = spawns_without_a_home(handle.read())
            if lines and name not in SPAWN_EXCEPTIONS:
                found.append("%s:%s" % (name, ", ".join(map(str, lines))))
        self.assertEqual([], found, "pass env=sandbox.environment(home), or list the script in SPAWN_EXCEPTIONS with why")

    def test_the_guard_sees_a_spawn_without_a_home(self):
        self.assertEqual([2], spawns_without_a_home("x = 1\nprocesses.start(cmd)\n"))
        self.assertEqual([1], spawns_without_a_home("processes.run(cmd, env=dict(os.environ))\n"))
        self.assertEqual([], spawns_without_a_home("processes.start(cmd, env=environment(sandbox))\n"))
        self.assertEqual([1], spawns_without_a_home("processes.start(cmd, env=environment(tagpup_config.home()))\n"))

    def test_a_sandbox_that_loads_models_shares_the_owners_card_line(self):
        from tagpup.ml import gpu
        with mock.patch.dict(os.environ, {gpu.ENV: "elsewhere"}):
            self.assertNotIn(gpu.ENV, sandbox.environment(r"C:\x"))
            sandbox.enter(r"C:\x")
            self.assertNotIn(gpu.ENV, os.environ)
            os.environ.pop("TAGPUP_SERVERS", None)
            os.environ.pop("TAGPUP_HOME", None)
            os.environ.pop("TAGPUP_DOWNLOADS", None)
            os.environ.pop("TAGPUP_RECYCLE_BIN", None)

    def test_every_script_takes_its_sandboxs_environment_from_the_helper(self):
        found = []
        folder = os.path.join(WORKSPACE_DIR, "scripts")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py") or name == "install_app.py":   # its launchers name the owner's home
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if BY_HAND.search(line):
                        found.append("%s:%d" % (name, number))
        self.assertEqual([], found, "use sandbox.environment(sandbox) or sandbox.enter(sandbox)")

    def test_a_tests_home_is_what_the_helper_says(self):
        home = own_home.for_test(self, prefix="sandbox_helper_")
        for name, value in tagpup_config.own_home_environment(home.root).items():
            self.assertEqual(value, os.environ.get(name), name)

    def test_the_helper_moves_every_folder_of_the_users_own_into_the_home(self):
        env = tagpup_config.own_home_environment(r"C:\x")
        self.assertEqual({"TAGPUP_HOME", "TAGPUP_SERVERS", "TAGPUP_GPU_LOCK", "TAGPUP_DOWNLOADS", "TAGPUP_RECYCLE_BIN"},
                         set(env))
        for name in ("TAGPUP_SERVERS", "TAGPUP_GPU_LOCK", "TAGPUP_DOWNLOADS"):
            self.assertEqual(os.path.normcase(r"C:\x"), os.path.normcase(os.path.dirname(env[name])), name)


@unittest.skipUnless(os.name == "nt", "the owner's folders are Windows'")
class ASandboxServer(unittest.TestCase):
    def test_writes_nothing_under_the_owners_folders(self):
        home = own_home.for_test(self, prefix="sandbox_server_")
        self.addCleanup(own_home.end_processes, home.root)
        owner = os.path.join(home.root, "owner_profile")
        os.makedirs(owner)
        # The owner's shell: their profile (here the test's), none of a home's folders set.
        shell = {name: None for name in tagpup_config.own_home_environment(home.root)}
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": owner}):
            for name in shell:
                os.environ.pop(name, None)
            env = sandbox.environment(home.root, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1",
                                      TAGPUP_WEB_NO_WARMUP="1", TAGPUP_WEB_RELOADED="1")
        env.pop("TAGPUP_SUPERVISOR_TOKEN", None)
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
        # Its records are the sandbox's (written once it serves, before it answers) ...
        self.assertEqual(sorted("%d.json" % port for port in ports),
                         sorted(os.listdir(os.path.join(home.root, "servers"))))
        # ... and nothing is under the owner's profile.
        written = [os.path.join(root, name) for root, _dirs, names in os.walk(owner) for name in names]
        self.assertEqual([], written)
        self.assertEqual([], os.listdir(owner))


if __name__ == "__main__":
    unittest.main()

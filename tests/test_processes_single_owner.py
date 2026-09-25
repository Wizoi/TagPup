"""Other processes are started, run and stopped through tagpup.core.processes, and
nowhere else.

Every spawn called subprocess itself and decided for itself whether the child got a
console window; most never decided, and a test run put a terminal window on the
desktop for every server it started and every test process's reaper (docs/findings.md,
#124). One owner, and a guard that fails the next spawn written around it.
"""
import os
import re
import subprocess
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import processes  # noqa: E402

OWNER = os.path.join("tagpup", "core", "processes.py")
GUARD = os.path.join("tests", "test_processes_single_owner.py")

#: What only the owner may say.
SPAWNS = re.compile(r"subprocess\.(Popen|run|call|check_output|check_call)\(")
FLAGS = re.compile(r"creationflags|CREATE_NO_WINDOW|DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|\btaskkill\b|\btasklist\b")


def python_sources():
    for folder in ("tagpup", "scripts", "tests", "tools"):
        for root, _dirs, names in os.walk(os.path.join(WORKSPACE_DIR, folder)):
            for name in names:
                if name.endswith(".py"):
                    yield os.path.join(root, name)
    for name in os.listdir(WORKSPACE_DIR):
        if name.endswith(".py"):
            yield os.path.join(WORKSPACE_DIR, name)


class NobodyElseSpawns(unittest.TestCase):
    def offenders(self, pattern):
        found = []
        for path in python_sources():
            relative = os.path.relpath(path, WORKSPACE_DIR)
            if os.path.normcase(relative) in (os.path.normcase(OWNER), os.path.normcase(GUARD)):
                continue
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if pattern.search(line) and not line.lstrip().startswith("#"):
                        found.append("%s:%d" % (relative, number))
        return found

    def test_nothing_else_calls_subprocess(self):
        self.assertEqual([], self.offenders(SPAWNS), "start, run or kill through tagpup.core.processes")

    def test_nothing_else_spells_a_flag_or_taskkill(self):
        self.assertEqual([], self.offenders(FLAGS))

    def test_the_owner_does(self):
        with open(os.path.join(WORKSPACE_DIR, OWNER), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("subprocess.Popen(", source)
        self.assertIn("CREATE_NO_WINDOW", source)


class WhatTheOwnerDoes(unittest.TestCase):
    def test_a_child_gets_no_window_of_its_own(self):
        done = processes.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
        self.assertEqual("hi", done.stdout.strip())

    @unittest.skipUnless(os.name == "nt", "the flag is Windows'")
    def test_the_flags_say_hidden_and_never_detached(self):
        self.assertTrue(processes._flags(False) & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(processes._flags(True) & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(processes._flags(True) & subprocess.DETACHED_PROCESS)

    def test_kill_tree_ends_a_process_and_its_children(self):
        child = processes.start([sys.executable, "-c",
                                 "import subprocess, sys, time; "
                                 "__import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                                 "time.sleep(60)"])
        try:
            self.assertTrue(processes.is_alive(child.pid))
            processes.kill_tree(child.pid)
            child.wait(timeout=30)
            self.assertFalse(processes.is_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()


if __name__ == "__main__":
    unittest.main()

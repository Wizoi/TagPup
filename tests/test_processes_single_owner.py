"""What tagpup.core.processes does for the children it starts (hidden, ended with their tree), and that no
test ends a launcher with terminate(). That nothing else spawns is a line rule in test_rules_have_one_owner (#124)."""
import os
import subprocess
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core import processes  # noqa: E402

def terminated_launchers(source):
    """Each `x.terminate()` in `source` where x was started by processes.start: [line]. On Windows the venv's
    python.exe is a launcher, and terminate() ends the launcher alone, which may leave the interpreter it
    started (and everything that one started) running -- kill_tree reaches them all (#736)."""
    import ast

    def dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
        return None

    tree = ast.parse(source)
    started = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and dotted(node.value.func) == "processes.start"):
            started.update(name for name in map(dotted, node.targets) if name)
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "terminate"
            and dotted(node.func.value) in started]


class NobodyTerminatesALauncher(unittest.TestCase):
    def test_a_test_ends_what_it_started_with_kill_tree(self):
        found = []
        folder = os.path.join(WORKSPACE_DIR, "tests")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    lines = terminated_launchers(handle.read())
                if lines:
                    found.append("%s:%s" % (name, ", ".join(map(str, lines))))
        self.assertEqual([], found, "processes.kill_tree(process.pid), then wait")

    def test_the_guard_sees_one(self):
        self.assertEqual([2], terminated_launchers("p = processes.start(c)" + chr(10) + "p.terminate()" + chr(10)))
        self.assertEqual([2], terminated_launchers("cls.p = processes.start(c)" + chr(10) + "cls.p.terminate()" + chr(10)))
        self.assertEqual([], terminated_launchers("et = Session()" + chr(10) + "et.terminate()" + chr(10)))


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

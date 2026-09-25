"""The command .mcp.json gives Claude Code starts TagPup's MCP server from any folder
(docs/findings.md, #173).

It named `.venv/Scripts/python.exe`, relative to the folder the server was started in: a
session started anywhere but the checkout's root, or in a worktree (which has no .venv),
had no server. The command is run here as Claude Code runs it -- from another folder, with
and without the CLAUDE_PROJECT_DIR Claude Code gives the server's process.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import own_home  # noqa: E402

from tagpup.core import processes  # noqa: E402


def configured():
    with open(os.path.join(ROOT, ".mcp.json"), encoding="utf-8") as handle:
        server = json.load(handle)["mcpServers"]["tagpup"]
    return server["command"], server["args"]


class TheConfiguredCommand(unittest.TestCase):
    def argv(self):
        command, args = configured()
        found = shutil.which(command)
        self.assertIsNotNone(found, "%s is not on PATH" % command)
        return [found] + args

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env.update(extra)
        return env

    def which(self, cwd, **extra):
        done = processes.run(self.argv(), cwd=cwd, env=self.env(TAGPUP_MCP_WHICH="1", **extra),
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        python, checkout = done.stdout.splitlines()
        self.assertTrue(os.path.isfile(python), python)
        self.assertEqual(os.path.normcase(ROOT), os.path.normcase(checkout))
        return python

    def test_finds_an_interpreter_from_another_folder_given_the_project(self):
        elsewhere = tempfile.mkdtemp(prefix="mcp_cwd_")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        self.which(elsewhere, CLAUDE_PROJECT_DIR=ROOT)

    def test_finds_an_interpreter_from_a_folder_inside_the_checkout(self):
        self.which(os.path.join(ROOT, "tests", "frontend"))

    def test_the_interpreter_is_a_venvs(self):
        python = self.which(ROOT, CLAUDE_PROJECT_DIR=ROOT)
        self.assertIn(".venv", python.replace(os.sep, "/").split("/"))

    def test_starts_the_server_from_another_folder(self):
        home = own_home.for_test(self)
        elsewhere = tempfile.mkdtemp(prefix="mcp_cwd_")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        child = processes.start(self.argv(), cwd=elsewhere,
                                env=self.env(CLAUDE_PROJECT_DIR=ROOT, TAGPUP_HOME=home.root),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: child.poll() is None and processes.kill_tree(child.pid))
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in child.stdout], daemon=True).start()
        child.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}}) + "\n").encode("utf-8"))
        child.stdin.flush()
        self.assertEqual("tagpup", json.loads(lines.get(timeout=60))["result"]["serverInfo"]["name"])
        child.stdin.close()
        self.assertEqual(0, child.wait(timeout=60))
        child.stdout.close()


if __name__ == "__main__":
    unittest.main()

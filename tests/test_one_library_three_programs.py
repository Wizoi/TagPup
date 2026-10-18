"""One library, three programs: the server, the CLI and the MCP server act on it at once (scenario I7).

A drain sees only its own process's work, so these are the owner's days a test of one process cannot see: an update while a CLI
index or `sync --apply` is writing; a look from Claude (MCP) at a library the server has not migrated yet; the CLI's sync and the
server's sync (the watcher's, the recurring job's, the page's) over one drift. Real processes through tagpup.core.processes, in a
home of their own with ports of their own; the writer in the first test is a process holding the store's own writer open, as a
CLI index does, since a real index cannot be held part-way without the card.

Lifted: test_sync_entry_points' TheCli.test_a_dry_run_says_what_it_found_and_changes_nothing (the dry run of the real CLI is the
first step of the sync test here). Broken in a scratch copy and this file fails: runtime.sync applying on a dry run (the
CLI's dry run wrote a change: the sync test), the MCP server's find_library calling `schema.ensure` (the library migrated by a
look), the launcher's drain start not saying to finish the CLI's work first (the update test). The server's stale second apply is
idempotent today (the row already holds the path the plan moved it to), which the sync test pins; no break of the journal's
`expect` guard is caught, since the update it guards is the same value.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from free_port import free_port  # noqa: E402
import migration_names  # noqa: E402

import tagpup_web  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402
from tagpup import launcher, runtime  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

THEN = 1_700_000_000
NEW = "20261004-110000-bbbbbbb"

#: A CLI's long write, stood in for: one row, a marker file, a wait for the test's go-ahead, a second row, the commit.
WRITER = """
import os, sys, time
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(tests)r)
import photo_rows
from tagpup.store import db
mid, go = %(mid)r, %(go)r
def write(conn):
    photo_rows.add_read(conn, "C:/Cli Index/one.jpg", {})
    open(mid, "w").close()
    while not os.path.exists(go):
        time.sleep(0.05)
    photo_rows.add_read(conn, "C:/Cli Index/two.jpg", {})
db.write_with_connection(%(path)r, write)
"""


class OneLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="three_programs_")
        self.addCleanup(own_home.end_processes, self.home.root)   # the reaper: every process this test started, and theirs
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)

    def rows(self, sql):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def cli(self, *args):
        done = processes.run([sys.executable, os.path.join(WORKSPACE_DIR, "tagpup_cli.py"), "--db", self.path, *args],
                             capture_output=True, text=True, cwd=self.home.root, timeout=180)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return done.stdout

    def test_an_update_while_a_cli_write_is_under_way_neither_waits_for_it_nor_breaks_it(self):
        env = dict(os.environ, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1", TAGPUP_WEB_NO_WARMUP="1", TAGPUP_WEB_RELOADED="1")
        ports = [free_port(), free_port()]
        old = processes.start([sys.executable, os.path.join(WORKSPACE_DIR, "tagpup_web.py"), "--tagpup-port", str(ports[0]),
                               "--tuner-port", str(ports[1])], env=env, cwd=self.home.root, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: old.poll() is None and processes.kill_tree(old.pid))
        self.wait_until(lambda: self.serving(ports[0]), "the server never answered")
        mid, go = os.path.join(self.home.root, "mid"), os.path.join(self.home.root, "go")
        writer = processes.start([sys.executable, "-c", WRITER % {"root": WORKSPACE_DIR, "tests": os.path.dirname(os.path.abspath(__file__)), "mid": mid, "go": go, "path": self.path}],
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: writer.poll() is None and processes.kill_tree(writer.pid))
        self.wait_until(lambda: os.path.exists(mid), "the CLI's write never began")

        said = []
        with mock.patch.object(launcher, "QUIET", 0):
            way = launcher.make_way(ports[0], NEW, say=said.append, open_page=lambda: None, answering=tagpup_web.answering)

        self.assertEqual(launcher.SERVE, way[0], said)
        self.assertIsNotNone(old.wait(timeout=60), "the old server still runs")
        self.assertTrue(any("finish a cli index, sync --apply or mcp write before updating" in line.lower() for line in said), said)
        self.assertIsNone(writer.poll(), "the update ended the CLI's write")
        open(go, "w").close()
        self.assertEqual(0, writer.wait(timeout=60))
        self.assertEqual([("ok",)], self.rows("PRAGMA integrity_check"))
        self.assertEqual([(paths.stored("C:/Cli Index/one.jpg"),), (paths.stored("C:/Cli Index/two.jpg"),)],
                         self.rows("SELECT path FROM photos ORDER BY path"), "the write is whole: both rows, once")

    @staticmethod
    def serving(port):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=5) as reply:
                return "version" in json.loads(reply.read().decode("utf-8"))
        except OSError:
            return False

    def wait_until(self, check, why, seconds=90):
        import time
        deadline = time.time() + seconds
        while not check():
            self.assertLess(time.time(), deadline, why)
            time.sleep(0.2)

    def test_an_mcp_look_at_a_library_one_migration_behind_does_not_migrate_it(self):
        behind = self.home.library("regatta.db")
        schema.ensure(behind)
        self.enterContext(migration_names.ahead())   # the code knows a step the library has not had
        with open(behind, "rb") as handle:
            before = handle.read()

        async def look():
            async with create_connected_server_and_client_session(server.build()) as client:
                return [await client.call_tool(tool, {"library": "regatta"}) for tool in ("sync_state", "summary", "history")]
        with mock.patch.object(server.config, "exiftool_path", return_value=os.path.join(self.home.root, "no-exiftool.exe")):
            asked = asyncio.run(look())

        self.assertEqual(3, len(asked))
        with open(behind, "rb") as handle:
            self.assertEqual(before, handle.read(), "a look migrated the library")
        self.assertEqual(migration_names.BASE, schema.version_of(behind))

    def test_the_clis_sync_and_the_servers_sync_over_one_drift_write_it_once(self):
        folder = os.path.join(self.home.root, "Regatta")
        known = os.path.join(folder, "regatta_01.jpg")
        os.makedirs(folder)
        with open(known, "wb") as handle:
            handle.write(b"jpeg")
        os.utime(known, (THEN, THEN))
        conn = db.connect(self.path)
        try:
            photo_rows.add_read(conn, known, {})
            conn.commit()
        finally:
            conn.close()
        os.makedirs(os.path.join(folder, "2019"))
        moved = os.path.join(folder, "2019", "regatta_01.jpg")
        os.rename(known, moved)     # a file moved in Explorer: its row follows it, by name, size and time
        library = Library(self.path)

        changes = self.rows("SELECT id FROM changes")
        dry = self.cli("sync")

        self.assertIn("1 moved file(s)", dry)
        self.assertIn("--apply", dry)
        self.assertNotIn("regatta_", dry, "a count, never a file")
        self.assertEqual(changes, self.rows("SELECT id FROM changes"), "a dry run wrote: it stamped the library or recorded a change")
        # The server's sync has planned the same drift and is held there; the CLI applies it; the server's apply is then written.
        planned, release, answer = threading.Event(), threading.Event(), {}
        real = sync_service.look

        def held_after_looking(*args, **kwargs):
            seen = real(*args, **kwargs)
            planned.set()
            release.wait(60)
            return seen

        with mock.patch.object(sync_service, "look", held_after_looking):
            servers = threading.Thread(target=lambda: answer.update(result=runtime.sync(library, apply=True)))
            servers.start()
            self.assertTrue(planned.wait(60), "the server's sync never planned")
            applied = self.cli("sync", "--apply")
            release.set()
            servers.join(60)

        self.assertIn("Wrote 1 row(s): 0 read again, 1 moved", applied)
        result = answer["result"]
        self.assertEqual((True, 0, 0), (result.ok, result.changed, result.details["queued"]), "the second apply wrote or queued")
        self.assertEqual([("sync",)], self.rows("SELECT operation FROM changes WHERE operation = 'sync'"), "one change, not two")
        self.assertEqual([(moved,)], [(path,) for (path,) in self.rows("SELECT path FROM photos")], "the row follows its file, once")
        self.assertIn("In step", self.cli("sync"))


if __name__ == "__main__":
    unittest.main()

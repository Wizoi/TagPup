"""The always-on process's supervisor (tagpup.supervisor; docs/ARCHITECTURE.md, phase 8):
it restarts the server after a crash and gives up on one that keeps crashing, refuses a
second copy of itself, waits while another server holds the ports, and moves the server
onto a newer installed version only once it has drained.

The server here is a stand-in, a few lines of Python that crash, sleep or answer the
drain as told: what is tested is the supervisor, and a real server would load the app
for each of a dozen starts. The drain itself is tests/test_server_drain.py's.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: The stand-in server: `fake.py MODE VERSION RECORD` notes each start in RECORD, then
#: crashes, exits as the real one does when the ports are taken, or serves /api/server
#: and the drain (refused while a file `busy` is beside RECORD) until it is stopped.
FAKE_SERVER = r'''
import http.server, json, os, sys
sys.path.insert(0, %(root)r)
from tagpup import supervisor
mode, version, record = sys.argv[1], sys.argv[2], sys.argv[3]
with open(record, "a", encoding="utf-8") as handle:
    handle.write(version + "\n")
with open(record, encoding="utf-8") as handle:
    starts = len(handle.read().split())
if mode == "crash" or (mode == "crash-once" and starts == 1):
    sys.exit(7)
if mode == "ports":
    sys.exit(supervisor.PORTS_TAKEN)
busy = os.path.join(os.path.dirname(record), "busy")

class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, {"version": version})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get(supervisor.TOKEN_HEADER) != os.environ.get(supervisor.TOKEN):
            return self.reply(403, {"success": False})
        if os.path.exists(busy):
            return self.reply(200, {"success": True, "drained": False, "waiting_for": ["1 Suggest run(s)"]})
        self.reply(200, {"success": True, "drained": True})

    def log_message(self, *args):
        pass

server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
supervisor.write_server({"tagpup": server.server_address[1]}, version)
server.serve_forever()
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="supervisor_")
        self.work = tempfile.mkdtemp(prefix="supervisor_work_", dir=self.home.root)
        self.fake = os.path.join(self.work, "fake_server.py")
        with open(self.fake, "w", encoding="utf-8") as handle:
            handle.write(FAKE_SERVER % {"root": WORKSPACE_DIR})
        self.record = os.path.join(self.work, "starts.txt")
        # A server a test starts runs no recurring job and loads no model weights.
        self.env = dict(os.environ, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1")

    def make(self, mode, **timings):
        options = dict(backoff=(0.05,), max_crashes=3, crash_window=60, tick=0.02, retry_move=0.05,
                       update_every=3600, ports_wait=0.05)
        options.update(timings)
        made = supervisor.Supervisor(
            code_root=self.work, env=self.env, hand_over=False,
            command=lambda code: [sys.executable, self.fake, mode, os.path.basename(code), self.record],
            **options)
        self.addCleanup(made.stop_child)
        return made

    def starts(self):
        try:
            with open(self.record, encoding="utf-8") as handle:
                return handle.read().split()
        except OSError:
            return []

    def in_thread(self, made):
        found = []
        thread = threading.Thread(target=lambda: found.append(made.run()), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        self.addCleanup(made.stop)
        return thread, found

    def wait_until(self, check, seconds=30):
        deadline = time.time() + seconds
        while True:
            found = check()
            if found:
                return found
            self.assertLess(time.time(), deadline, "timed out")
            time.sleep(0.02)

    def answering_version(self):
        where = supervisor.server()
        if where is None:
            return None
        port = where["ports"]["tagpup"]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=5) as reply:
                return json.loads(reply.read().decode("utf-8"))["version"]
        except OSError:
            return None


class AfterACrash(Base):
    def test_the_server_is_started_again(self):
        made = self.make("crash-once")
        thread, ended = self.in_thread(made)
        self.wait_until(lambda: len(self.starts()) == 2 and made.child_alive() and supervisor.server())
        self.assertEqual("running", supervisor.running()["state"])
        made.stop()
        thread.join(30)
        self.assertEqual([0], ended)
        self.assertFalse(made.child_alive())
        self.assertEqual("stopped", supervisor.last_state()["state"])
        self.assertIsNone(supervisor.running())

    def test_it_gives_up_on_one_that_keeps_crashing_and_says_why(self):
        made = self.make("crash")
        with self.assertLogs("tagpup.supervisor", level="ERROR") as logged:
            self.assertEqual(supervisor.GAVE_UP, made.run())
        self.assertEqual(3, len(self.starts()), "gave up after the wrong number of crashes")
        state = supervisor.last_state()
        self.assertEqual("gave up", state["state"])
        self.assertIn("3 times in 1 minutes", state["why"])
        self.assertIn("exit code 7", state["why"])
        self.assertIn("giving up", "\n".join(logged.output))

    def test_crashes_long_apart_are_not_counted_together(self):
        made = self.make("crash", crash_window=0.01, max_crashes=2)
        thread, ended = self.in_thread(made)
        self.wait_until(lambda: len(self.starts()) >= 4)
        self.assertTrue(thread.is_alive(), "gave up on crashes minutes apart")
        made.stop()
        thread.join(30)
        self.assertEqual([0], ended)


class PortsAnotherHolds(Base):
    def test_are_waited_for_not_counted_as_crashes(self):
        made = self.make("ports", max_crashes=2)
        with self.assertLogs("tagpup.supervisor", level="WARNING") as logged:
            thread, ended = self.in_thread(made)
            self.wait_until(lambda: len(self.starts()) >= 4)
        self.assertTrue(thread.is_alive(), "gave up on a server whose ports were taken")
        said = "\n".join(logged.output)
        self.assertIn("Another server answers on the ports", said)
        self.assertEqual(1, said.count("Another server answers"), "said again at every try")
        self.assertNotIn("exit code", said, "counted as a crash")
        made.stop()
        thread.join(30)
        self.assertEqual([0], ended)


class OnePerHome(Base):
    def test_a_second_supervisor_is_refused(self):
        holder = processes.start(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, %r); from tagpup import supervisor; "
             "lock = supervisor.Lock(supervisor.data_file(supervisor.LOCK_FILE)); "
             "print(lock.acquire(), flush=True); time.sleep(120)" % WORKSPACE_DIR],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait, 30)
        self.addCleanup(processes.kill_tree, holder.pid)
        self.assertEqual("True", holder.stdout.readline().strip())
        made = self.make("crash-once")
        with self.assertLogs("tagpup.supervisor", level="WARNING") as logged:
            self.assertEqual(supervisor.ALREADY_RUNNING, made.main())
        self.assertEqual([], self.starts(), "the second supervisor started a server")
        self.assertIn("Another TagPup supervisor", "\n".join(logged.output))

    def test_the_lock_is_let_go_when_it_stops(self):
        first = supervisor.Lock(supervisor.data_file(supervisor.LOCK_FILE))
        self.assertTrue(first.acquire())
        self.assertFalse(supervisor.Lock(first.path).acquire())
        first.release()
        again = supervisor.Lock(first.path)
        self.assertTrue(again.acquire())
        again.release()


class StoppingByFile(Base):
    def test_the_stop_file_drains_and_stops_it(self):
        made = self.make("serve")
        thread, ended = self.in_thread(made)
        self.wait_until(supervisor.server)
        supervisor.request_stop()
        thread.join(30)
        self.assertEqual([0], ended)
        self.assertFalse(os.path.exists(supervisor.data_file(supervisor.STOP_FILE)))
        self.assertIsNone(supervisor.server())


class MovingOntoANewVersion(Base):
    """The owner's rule (2026-09-26): a newer version is moved onto at the next quiet
    moment, the work under way finished first; then the new version answers."""

    def setUp(self):
        super().setUp()
        self.installed = os.path.join(self.work, "installed")
        self.set_current("20260926-090000-aaaaaaa")

    def set_current(self, version):
        os.makedirs(os.path.join(self.installed, "versions", version), exist_ok=True)
        with open(os.path.join(self.installed, "current.txt"), "w", encoding="utf-8") as handle:
            handle.write(version)

    def make_installed(self, installs):
        made = supervisor.Supervisor(
            installed=self.installed, env=self.env, hand_over=False, install=lambda: installs.pop(0)() if installs else None,
            command=lambda code: [sys.executable, self.fake, "serve", os.path.basename(code), self.record],
            backoff=(0.05,), tick=0.02, retry_move=0.05, update_every=0.2)
        self.addCleanup(made.stop_child)
        return made

    def test_waits_for_the_work_under_way_then_the_new_version_answers(self):
        busy = os.path.join(self.work, "busy")
        with open(busy, "w", encoding="utf-8"):
            pass
        made = self.make_installed([lambda: self.set_current("20260926-120000-bbbbbbb")])
        thread, _ended = self.in_thread(made)
        self.wait_until(lambda: self.answering_version() == "20260926-090000-aaaaaaa")
        first = made._child.pid

        # Installed, but a Suggest run is under way: the old version keeps answering.
        self.wait_until(lambda: made._pending == "20260926-120000-bbbbbbb")
        time.sleep(0.5)
        self.assertEqual(first, made._child.pid, "the server was stopped while it was busy")
        self.assertEqual("20260926-090000-aaaaaaa", self.answering_version())

        os.remove(busy)
        self.wait_until(lambda: self.answering_version() == "20260926-120000-bbbbbbb")
        self.assertEqual(["20260926-090000-aaaaaaa", "20260926-120000-bbbbbbb"], self.starts())
        self.assertIsNone(made._pending)
        self.assertEqual("20260926-120000-bbbbbbb", supervisor.running()["server_version"])

    def test_an_install_the_owner_made_by_hand_is_moved_onto_too(self):
        made = self.make_installed([])
        thread, _ended = self.in_thread(made)
        self.wait_until(lambda: self.answering_version() == "20260926-090000-aaaaaaa")
        self.set_current("20260926-130000-ccccccc")   # scripts/install_app.py --apply
        self.wait_until(lambda: self.answering_version() == "20260926-130000-ccccccc")

    def test_no_update_is_looked_for_while_a_move_waits(self):
        busy = os.path.join(self.work, "busy")
        with open(busy, "w", encoding="utf-8"):
            pass
        calls = []
        installs = [lambda: calls.append(1) or self.set_current("20260926-120000-bbbbbbb")] + \
            [lambda: calls.append(1)] * 50
        made = self.make_installed(installs)
        thread, _ended = self.in_thread(made)
        self.wait_until(lambda: made._pending)
        time.sleep(1.0)
        self.assertEqual(1, len(calls), "a second version could be installed over the one the server runs")


class TheInterpreters(unittest.TestCase):
    def test_the_windowless_and_the_console_interpreter_are_each_others(self):
        folder = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(folder, "pythonw.exe")):
            self.assertEqual(os.path.join(folder, "pythonw.exe"), supervisor.windowless_python(sys.executable))
            self.assertEqual(os.path.join(folder, "python.exe"),
                             supervisor.console_python(os.path.join(folder, "pythonw.exe")))


if __name__ == "__main__":
    unittest.main()

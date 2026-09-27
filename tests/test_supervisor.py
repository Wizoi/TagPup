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
from unittest import mock

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
import time

class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if mode == "hang":
            time.sleep(600)   # alive, and answering nothing
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


class AServerThatStopsAnswering(Base):
    def test_is_ended_and_started_again_after_it_misses_its_checks(self):
        made = self.make("hang", health_every=0.1, health_timeout=0.3, max_unanswered=2, max_crashes=5)
        with self.assertLogs("tagpup.supervisor", level="WARNING") as logged:
            thread, ended = self.in_thread(made)
            self.wait_until(lambda: len(self.starts()) >= 2)
        said = "\n".join(logged.output)
        self.assertIn("stopped answering", said)
        self.assertIn("exit code no answer", said)
        made.stop()
        thread.join(30)

    def test_one_that_answers_is_left_alone(self):
        made = self.make("serve", health_every=0.05, health_timeout=1, max_unanswered=2)
        thread, ended = self.in_thread(made)
        self.wait_until(supervisor.server)
        time.sleep(1)
        self.assertEqual(1, len(self.starts()))
        made.stop()
        thread.join(30)
        self.assertEqual([0], ended)


class StoppingABusyServer(Base):
    def test_gives_up_on_the_drain_after_its_bound_and_ends_it(self):
        with open(os.path.join(self.work, "busy"), "w", encoding="utf-8"):
            pass
        made = self.make("serve", stop_drain_limit=0.5)
        thread, ended = self.in_thread(made)
        self.wait_until(supervisor.server)
        with self.assertLogs("tagpup.supervisor", level="WARNING") as logged:
            made.stop()
            thread.join(30)
        self.assertEqual([0], ended)
        self.assertFalse(made.child_alive())
        self.assertIn("ending it anyway", "\n".join(logged.output))


class WhatAStoppedSupervisorLeaves(Base):
    def orphan(self, token):
        """A server its supervisor left running: started with `token`, serving."""
        env = dict(self.env, **{supervisor.TOKEN: token})
        orphan = processes.start([sys.executable, self.fake, "serve", "orphan", self.record], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: orphan.poll() is None and processes.kill_tree(orphan.pid))
        self.wait_until(supervisor.server)
        return orphan

    def test_a_server_whose_token_it_knows_is_drained_and_ended_before_its_own_starts(self):
        orphan = self.orphan("an-old-token")
        supervisor.write_json(supervisor.data_file(supervisor.STATE_FILE),
                              {"pid": 1, "state": "running", "server_token": "an-old-token"})
        made = self.make("serve")
        with self.assertLogs("tagpup.supervisor", level="WARNING") as logged:
            thread, ended = self.in_thread(made)
            self.wait_until(lambda: len(self.starts()) == 2 and supervisor.server()
                            and supervisor.server()["version"] != "orphan")
        self.assertIsNotNone(orphan.wait(timeout=30), "the orphan is still running")
        said = "\n".join(logged.output)
        self.assertIn("left running", said)
        self.assertIn("once it drains", said)
        made.stop()
        thread.join(30)

    def test_one_that_ends_by_itself_is_not_waited_for_nor_killed_after(self):
        """The drain went on to its bound for a server that had gone, then killed its
        pid -- by then, maybe another process's."""
        with open(os.path.join(self.work, "busy"), "w", encoding="utf-8"):
            pass
        orphan = self.orphan("an-old-token")
        supervisor.write_json(supervisor.data_file(supervisor.STATE_FILE),
                              {"pid": 1, "state": "running", "server_token": "an-old-token"})
        made = self.make("serve", stop_drain_limit=60, retry_move=0.1)
        end_it = processes.kill_tree   # the test's own, before the supervisor's is watched
        threading.Timer(0.5, lambda: end_it(orphan.pid)).start()
        with mock.patch.object(supervisor.processes, "kill_tree", wraps=supervisor.processes.kill_tree) as kill:
            started = time.monotonic()
            made.end_orphan()
        self.assertLess(time.monotonic() - started, 15, "it waited out its bound for a server that was gone")
        kill.assert_not_called()

    def test_one_whose_token_it_does_not_know_is_ended(self):
        orphan = self.orphan("a-token-nobody-kept")
        made = self.make("serve")
        thread, ended = self.in_thread(made)
        self.wait_until(lambda: len(self.starts()) == 2 and supervisor.server()
                        and supervisor.server()["version"] != "orphan")
        self.assertIsNotNone(orphan.wait(timeout=30))
        made.stop()
        thread.join(30)

    def test_at_login_the_orphan_is_seen_to_before_a_hand_over_and_its_token_kept(self):
        """A supervisor starting at login that handed over at once wrote supervisor.json
        with no token, and its successor ended the orphan without draining it."""
        installed = os.path.join(self.work, "installed")
        os.makedirs(installed)
        with open(os.path.join(installed, "current.txt"), "w", encoding="utf-8") as handle:
            handle.write("v2")
        supervisor.write_json(supervisor.data_file(supervisor.STATE_FILE),
                              {"pid": 1, "state": "running", "server_token": "an-old-token"})
        made = supervisor.Supervisor(installed=installed, env=self.env, install=lambda: None)
        made.own_version = "v1"
        order = []

        def hand_over_to(version):
            made._say("handing over")
            order.append(("hand over", supervisor.last_state().get("server_token")))
            return True
        with mock.patch.object(made, "end_orphan", side_effect=lambda: order.append("orphan")), \
                mock.patch.object(made, "hand_over_to", side_effect=hand_over_to), \
                mock.patch.object(made, "_taken_over", return_value=True):
            self.assertEqual(0, made.main())
        self.assertEqual(["orphan", ("hand over", "an-old-token")], order)

    def test_a_supervisor_failing_ends_its_server_with_it(self):
        made = self.make("serve")

        def fail():
            raise RuntimeError("a bug")
        made.look_for_update = fail
        made.update_every = 0
        with self.assertRaises(RuntimeError):
            made.main()
        self.assertFalse(made.child_alive(), "its server outlived it")
        self.assertIsNone(supervisor.server())


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


class AServerThatWillNotEnd(Base):
    """A hand-over finished with the old server still running, and nothing watching it."""

    def stuck(self):
        child = mock.Mock(pid=4242)
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired("server", 30)
        return child

    def test_is_kept_by_the_supervisor(self):
        made = self.make("serve")
        made._child = self.stuck()
        with mock.patch.object(supervisor.processes, "kill_tree"):
            self.assertFalse(made.stop_child())
        self.assertIsNotNone(made._child, "let go of a server still running")

    def test_abandons_the_hand_over(self):
        installed = os.path.join(self.work, "installed")
        os.makedirs(installed)
        made = supervisor.Supervisor(installed=installed, env=self.env, command=lambda code: ["x"], hand_over_wait=5)
        made._child = self.stuck()
        successor = mock.Mock(pid=5151)
        successor.poll.return_value = None
        me = {"pid": os.getpid(), "started": processes.started(os.getpid()), "version": "v2"}
        supervisor.write_json(supervisor.data_file(supervisor.HANDOVER_FILE), me)
        with mock.patch.object(supervisor, "start_in_background", return_value=successor), \
                mock.patch.object(supervisor, "remove"), \
                mock.patch.object(supervisor.processes, "kill_tree") as kill, \
                self.assertLogs("tagpup.supervisor", level="ERROR"):
            self.assertFalse(made.hand_over_to("v2"))
        self.assertIn(mock.call(5151), kill.call_args_list, "the new supervisor was left running")
        self.assertIsNotNone(made._child)
        made._child = None


class ItsFilesWithAReader(Base):
    """On Windows a file another process has open cannot be replaced or deleted: the
    supervisor's state and server.json, read by startup.py status, the launchers and the
    install, failed its move with PermissionError (seen in a run of this suite)."""

    def hold_open(self, path, seconds):
        handle = open(path, encoding="utf-8")
        timer = threading.Timer(seconds, handle.close)
        timer.start()
        self.addCleanup(timer.join)
        self.addCleanup(handle.close)

    def test_a_file_being_read_is_still_removed(self):
        path = supervisor.data_file(supervisor.SERVER_FILE)
        supervisor.write_json(path, {"pid": 1})
        self.hold_open(path, 0.3)
        self.assertTrue(supervisor.remove(path))
        self.assertFalse(os.path.exists(path))

    def test_one_held_past_the_wait_is_logged_and_left_not_raised(self):
        """A reader holding server.json longer than READER_WAIT took the supervisor down
        with it: stop_child raised out of a move."""
        path = supervisor.data_file(supervisor.SERVER_FILE)
        supervisor.write_json(path, {"pid": 1})
        with mock.patch.object(supervisor, "READER_WAIT", 0.2), \
                mock.patch.object(supervisor.os, "remove", side_effect=PermissionError(32, "in use")), \
                self.assertLogs("tagpup.supervisor", level="ERROR") as logged:
            self.assertFalse(supervisor.remove(path))
        self.assertIn("could not remove", "\n".join(logged.output).lower())

    def test_a_file_being_read_is_still_written(self):
        path = supervisor.data_file(supervisor.STATE_FILE)
        supervisor.write_json(path, {"state": "starting"})
        self.hold_open(path, 0.3)
        supervisor.write_json(path, {"state": "running"})
        self.assertEqual("running", supervisor.read_json(path)["state"])


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

    def test_asks_for_a_quiet_moment_until_the_update_has_waited_its_patience(self):
        made = self.make_installed([])
        asked = []
        made._drain_at = lambda where, token, quiet=0: asked.append(quiet) or {"drained": False, "waiting_for": []}
        made._child = mock.Mock(poll=lambda: None, pid=1)
        with mock.patch.object(supervisor, "server", return_value={"ports": {"tagpup": 1}}):
            made._pending, made._pending_since = "v2", made._clock()
            made.move()
            made._pending_since -= made.patience + 1
            made.move()
        made._child = None
        self.assertEqual([supervisor.QUIET, 0], asked)

    def test_after_a_failed_hand_over_it_is_not_pinned_for_ever(self):
        """Pinned to its own version after one failed hand-over, it read the pin as what
        was installed, and never looked at current.txt again."""
        made = self.make_installed([])
        made._child_version = "20260926-090000-aaaaaaa"
        self.set_current("20260926-120000-bbbbbbb")
        made._keep("20260926-090000-aaaaaaa")
        made.look_for_update()
        self.assertEqual("20260926-120000-bbbbbbb", made._pending, "current.txt was not read")
        # And once the patience is over, the server starts from what is installed again.
        self.assertEqual("20260926-090000-aaaaaaa", made.current()[0])
        made._pinned_until = made._clock() - 1
        self.assertEqual("20260926-120000-bbbbbbb", made.current()[0])

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

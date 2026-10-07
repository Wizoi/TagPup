"""An install by hand hands the running server over to the version it installed
(tagpup.launcher.hand_over; docs/findings.md, #795).

The owner ran install_app.py --apply and refreshed TagTuner, and the server started that
morning from the version before kept answering: only the next launch of TagPup.cmd or
TagTuner.cmd replaced it, and the install said so in a line nobody reads. Now the install
does what a launch does -- the server is asked to drain with its record's token, a long job
is waited out, saying what for, and it is ended once drained or hung -- and starts the new
version on the same ports, with no window and no new tab, so a page left open says TagPup
was updated.

Here: what the hand-over does with each server it finds (the always-on process's, one run
from a checkout, one of another installed folder: left alone), a long job waited out, a
version that does not start at all (the running server never asked), two installs at once,
the owner's Ctrl+C, the always-on process chosen; and, for real, in a home of the test's
own on ports of its own: an idle server replaced, a hung one ended, and a new version that
does not start once the old has gone, the old started again.
"""
import http.client
import json
import os
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

import install_app  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup import supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

OLD, NEW = "20261006-093740-aaaaaaa", "20261006-103400-bbbbbbb"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Started:
    """A Popen stood in for: running until told otherwise."""
    pid = 9191

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class Choices(unittest.TestCase):
    """hand_over with the server it finds and the processes it starts stood in for."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="handover_unit_")
        self.installed = os.path.join(self.home.root, "installed")
        for version in (OLD, NEW):
            os.makedirs(os.path.join(self.installed, "versions", version))
        with open(os.path.join(self.installed, "current.txt"), "w", encoding="utf-8") as handle:
            handle.write(NEW)
        self.said, self.started, self.clock = [], [], Clock()
        self.records = [{"pid": 4242, "started": 1, "ports": {"tagpup": 5, "tuner": 6}, "version": OLD,
                         "token": "t", "supervised": False}]
        self.port_held = True
        self.serving = OLD            # what GET /api/server says while the port is held
        self.drains = []              # what each drain answers, in turn

        def ask(port, timeout=None):
            return {"version": self.serving, "supervised": False} if self.port_held else None

        def drain(port, token, quiet=None):
            return self.drains.pop(0)

        def end(found, port, answering, sleep=None):
            self.port_held = False
            return True

        def start_server(python, code, home, ports):
            self.started.append((os.path.basename(code), dict(ports)))
            self.port_held, self.serving = True, os.path.basename(code)
            return Started()
        for name, value in (("ask", ask), ("drain", drain), ("end", end), ("start_server", start_server),
                            ("running", lambda: self.records), ("record", lambda port: self.records[0]),
                            ("answering", lambda port: self.port_held), ("starts", lambda *a: None)):
            patcher = mock.patch.object(launcher, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def hand_over(self):
        return launcher.hand_over(self.installed, sys.executable, self.home.root, say=self.said.append,
                                  sleep=self.clock.sleep, clock=self.clock)

    def test_nothing_running_only_installs(self):
        self.records = []
        self.assertTrue(self.hand_over())
        self.assertEqual([], self.started)
        self.assertEqual([], self.said)

    def test_an_idle_server_is_drained_ended_and_the_new_version_started_on_its_ports(self):
        self.drains = [{"drained": True}]
        self.assertTrue(self.hand_over())
        self.assertEqual([(NEW, {"tagpup": 5, "tuner": 6})], self.started)
        said = "\n".join(self.said)
        self.assertIn("handing over to %s" % NEW, said)
        self.assertIn("TagPup %s answers on port 5, 6" % NEW, said)
        self.assertIn("a page left open says TagPup was updated", said)

    def test_a_long_job_is_waited_out_saying_what_for_and_never_ended_under(self):
        self.drains = [{"drained": False, "waiting_for": ["a bulk edit"]}] * 3 + [{"drained": True}]
        self.assertTrue(self.hand_over())
        busy = [line for line in self.said if "is busy" in line]
        self.assertEqual(1, len(busy), self.said)    # said once, not every ten seconds
        self.assertIn("a bulk edit", busy[0])
        self.assertIn("stopping this install (Ctrl+C) leaves the update to the next launch", busy[0])
        self.assertNotIn("closing this window", busy[0])
        self.assertEqual(1, len(self.started))
        self.assertGreaterEqual(self.clock.now, 3 * launcher.RETRY_BUSY)

    def test_a_refused_drain_leaves_it_running_and_opens_no_page(self):
        self.drains = [{"drained": False, "refused": 403}]
        self.assertFalse(self.hand_over())
        self.assertEqual([], self.started)
        self.assertIn("leaving it running", self.said[-1])
        self.assertNotIn("page", self.said[-1])

    def test_a_version_that_does_not_start_at_all_never_disturbs_the_running_server(self):
        with mock.patch.object(launcher, "starts", return_value="it exited with 1: SyntaxError: invalid syntax"), \
                mock.patch.object(launcher, "make_way") as make_way:
            self.assertFalse(self.hand_over())
        make_way.assert_not_called()
        self.assertEqual([], self.started)
        self.assertIn("does not start (it exited with 1: SyntaxError", self.said[-1])
        self.assertIn("TagPup %s was left running" % OLD, self.said[-1])

    def test_the_always_on_processs_a_checkouts_and_another_folders_servers_are_left_alone(self):
        self.records = [dict(self.records[0], supervised=True), dict(self.records[0], pid=1, version=None),
                        dict(self.records[0], pid=2, version="20250101-000000-ccccccc")]
        with mock.patch.object(launcher, "make_way") as make_way:
            self.assertTrue(self.hand_over())
        make_way.assert_not_called()
        self.assertEqual([], self.started)
        kinds = [launcher.kind_of(found, self.installed, NEW) for found in self.records]
        self.assertEqual([launcher.ALWAYS_ON, launcher.CHECKOUT, launcher.ELSEWHERE], kinds)

    def test_the_install_says_what_it_does_with_each(self):
        self.records.append(dict(self.records[0], pid=1, version=None, ports={"tagpup": 7}))
        lines = install_app.still_running(NEW, self.installed, hand_over=True)
        self.assertEqual(2, len(lines), lines)
        self.assertIn("handing it over to %s" % NEW, lines[0])
        self.assertIn("run from a checkout, so the install leaves it alone", lines[1])
        # --no-restart: as before, the next launch.
        self.assertIn("the next launch of TagPup or TagTuner replaces it", install_app.still_running(NEW, self.installed)[0])

    def test_a_second_install_at_once_leaves_the_server_to_the_first(self):
        held = supervisor.Lock(os.path.join(self.installed, launcher.HANDOVER_LOCK))
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)
        with mock.patch.object(launcher, "make_way") as make_way:
            self.assertTrue(self.hand_over())
        make_way.assert_not_called()
        self.assertIn("Another install is handing the running server over", self.said[0])

    def test_the_version_installed_since_is_the_one_started(self):
        newer = "20261006-120000-ddddddd"
        os.makedirs(os.path.join(self.installed, "versions", newer))

        def drain(port, token, quiet=None):
            with open(os.path.join(self.installed, "current.txt"), "w", encoding="utf-8") as handle:
                handle.write(newer)    # another install, while this one waited
            return {"drained": True}
        with mock.patch.object(launcher, "drain", side_effect=drain):
            self.hand_over()
        self.assertEqual(newer, self.started[0][0])

    def test_the_new_version_not_answering_starts_the_old_again(self):
        self.drains = [{"drained": True}]
        attempts = []

        def start_server(python, code, home, ports):
            attempts.append(os.path.basename(code))
            started = Started()
            if os.path.basename(code) == NEW:
                started.code = 7          # exits at once, the port left free
            else:
                self.port_held, self.serving = True, OLD
            return started
        os.makedirs(os.path.join(self.installed, "versions", OLD), exist_ok=True)
        with open(os.path.join(self.installed, "versions", OLD, "tagpup_web.py"), "w", encoding="utf-8") as handle:
            handle.write("")
        with mock.patch.object(launcher, "start_server", side_effect=start_server):
            self.assertFalse(self.hand_over())
        self.assertEqual([NEW, OLD], attempts)
        said = "\n".join(self.said)
        self.assertIn("TagPup %s did not start (it exited with 7)" % NEW, said)
        self.assertIn("Started TagPup %s again" % OLD, said)
        self.assertIn("write %s in" % OLD, said)

    def test_the_always_on_process_chosen_is_started_rather_than_a_server_of_the_installs(self):
        self.drains = [{"drained": True}]

        def start_in_background(installed, python=None):
            self.port_held, self.serving = True, NEW
        with mock.patch.object(supervisor, "always_on", return_value=True), \
                mock.patch.object(supervisor, "running", return_value=None), \
                mock.patch.object(supervisor, "start_in_background", side_effect=start_in_background) as background:
            self.assertTrue(self.hand_over())
        background.assert_called_once()
        self.assertEqual([], self.started)

    def test_ctrl_c_while_it_waits_says_the_old_server_still_answers(self):
        with mock.patch.object(launcher, "make_way", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.hand_over()
        self.assertIn("Stopped: TagPup %s still answers on port 5, 6" % OLD, self.said[-1])

    def test_ctrl_c_after_the_old_was_ended_says_to_start_the_app(self):
        self.drains = [{"drained": True}]
        with mock.patch.object(launcher, "start_server", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.hand_over()
        self.assertIn("nothing answers on port 5, 6; start TagPup or TagTuner", self.said[-1])


class AReplyThatIsNotHttp(unittest.TestCase):
    """The hand-over's test met it: GET /api/server answered by its own request read back
    (http.client.BadStatusLine), which ask() let through and the install stopped on a
    traceback, the old server ended and nothing started."""

    def test_is_no_answer(self):
        with mock.patch("urllib.request.urlopen", side_effect=http.client.BadStatusLine("GET /api/server HTTP/1.1")):
            self.assertIsNone(launcher.ask(5, timeout=1))
            self.assertFalse(launcher.drain(5, "t", quiet=0, seconds=1)["drained"])
            self.assertIn("no_answer", launcher.drain(5, "t", quiet=0, seconds=1))


class TheInstallsMain(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="handover_main_")
        self.dest = os.path.join(self.home.root, "installed")

    def main(self, *more):
        return install_app.main(["--apply", "--to", self.dest, "--home", self.home.root, "--python", sys.executable,
                                 "--no-shortcuts"] + list(more))

    def test_it_hands_over_after_installing_and_not_with_no_restart(self):
        with mock.patch.object(install_app, "install") as install, \
                mock.patch.object(launcher, "hand_over", return_value=True) as hand_over:
            self.assertEqual(0, self.main())
            hand_over.assert_called_once()
            self.assertTrue(install.call_args.kwargs["hand_over"])
            hand_over.reset_mock()
            self.assertEqual(0, self.main("--no-restart"))
            hand_over.assert_not_called()
            self.assertFalse(install.call_args.kwargs["hand_over"])

    def test_a_hand_over_that_failed_is_a_failed_run(self):
        with mock.patch.object(install_app, "install"), mock.patch.object(launcher, "hand_over", return_value=False):
            self.assertEqual(1, self.main())

    def test_two_installs_at_once_take_turns(self):
        held = supervisor.Lock(os.path.join(self.dest, install_app.INSTALL_LOCK))
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)
        with mock.patch.object(install_app, "INSTALL_WAIT", 0.5), \
                mock.patch.object(install_app, "install") as install, \
                mock.patch.object(launcher, "hand_over") as hand_over, \
                mock.patch("builtins.print") as said:
            self.assertEqual(1, self.main())
        install.assert_not_called()
        hand_over.assert_not_called()
        lines = " ".join(str(call.args[0]) for call in said.call_args_list)
        self.assertIn("another install is running; waiting for it", lines)
        self.assertIn("nothing was installed", lines)


@unittest.skipUnless(os.name == "nt", "the launchers are Windows'")
class ForReal(unittest.TestCase):
    """Real servers on ports of the test's own, in a home of its own."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="handover_real_")
        # Before anything starts: every process the test started, and theirs (#727).
        self.addCleanup(own_home.end_processes, self.home.root)
        self.ports = {"tagpup": free_port(), "tuner": free_port()}
        self.installed = os.path.join(self.home.root, "installed")
        # What the servers the hand-over starts inherit, as they are a test's: no jobs, no
        # model weights, no warm-up, never a browser.
        environ = mock.patch.dict(os.environ, {"TAGPUP_NO_JOBS": "1", "TAGPUP_NO_MODEL_WEIGHTS": "1",
                                               "TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop(supervisor.TOKEN, None)
        # A drain waits for a moment with no request: the test's servers have none to wait for.
        quiet = mock.patch.object(launcher, "QUIET", 1)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.said = []

    def install(self, name):
        install_app.install(self.installed, self.home.root, sys.executable, name=name, apply=True,
                            say=lambda line: None, hand_over=True)
        return os.path.join(self.installed, "versions", name)

    def start_old(self, code):
        log = open(os.path.join(self.home.root, "old.log"), "ab")
        self.addCleanup(log.close)
        old = processes.start([sys.executable, os.path.join(code, "tagpup_web.py"),
                               "--tagpup-port", str(self.ports["tagpup"]), "--tuner-port", str(self.ports["tuner"])],
                              cwd=self.home.root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: old.poll() is None and processes.kill_tree(old.pid))
        self.wait_until(lambda: self.version(self.ports["tagpup"]) == OLD, why="the old server never answered")
        return old

    def version(self, port):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=5) as reply:
                return json.loads(reply.read().decode("utf-8"))["version"] or "checkout"
        except OSError:
            return None

    def wait_until(self, check, seconds=90, why="timed out"):
        deadline = time.time() + seconds
        while True:
            found = check()
            if found:
                return found
            self.assertLess(time.time(), deadline, why)
            time.sleep(0.2)

    def hand_over(self):
        return launcher.hand_over(self.installed, sys.executable, self.home.root, say=self.said.append)

    def test_an_idle_server_is_replaced_by_the_version_installed(self):
        old = self.start_old(self.install(OLD))
        serving = launcher.record(self.ports["tagpup"])["pid"]   # the interpreter, not the venv's launcher
        self.install(NEW)
        self.assertTrue(self.hand_over(), self.said)
        self.assertEqual({NEW}, {self.version(port) for port in self.ports.values()}, self.said)
        self.assertIsNotNone(old.wait(timeout=30), "the old server still runs")
        found = launcher.record(self.ports["tuner"])
        self.assertEqual(NEW, found["version"])
        self.assertFalse(found["supervised"])
        said = "\n".join(self.said)
        self.assertIn("TagPup %s is running (process %d); handing over to %s" % (OLD, serving, NEW), said)
        self.assertIn("finished what it was doing; ending it and starting %s" % NEW, said)
        self.assertIn("TagPup %s answers on port" % NEW, said)
        # It outlives the install: a process of its own, writing where the install said.
        self.assertTrue(os.path.exists(launcher.started_log(self.home.root)))

    def test_a_hung_server_is_ended_and_replaced(self):
        self.install(OLD)
        port = self.ports["tagpup"]
        script = ("import socket, sys, time; sys.path.insert(0, %r); from tagpup import launcher; "
                  "s = socket.socket(); s.bind(('127.0.0.1', %d)); s.listen(5); "
                  "launcher.say_where({'tagpup': %d, 'tuner': %d}, %r, 'hung'); time.sleep(600)"
                  % (WORKSPACE_DIR, port, port, self.ports["tuner"], OLD))
        hung = processes.start([sys.executable, "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: hung.poll() is None and processes.kill_tree(hung.pid))
        self.wait_until(lambda: launcher.record(port), why="the hung server never said where it answers")
        self.install(NEW)
        with mock.patch.object(launcher, "HUNG_TIMEOUT", 0.5):
            self.assertTrue(self.hand_over(), self.said)
        self.assertIsNotNone(hung.wait(timeout=30), "the hung server still runs")
        self.assertEqual(NEW, self.version(port))
        self.assertIn("has not answered for", "\n".join(self.said))

    def test_a_new_version_that_does_not_serve_gives_the_owner_the_old_one_back(self):
        old = self.start_old(self.install(OLD))
        new_code = self.install(NEW)
        # It imports and answers --help, then exits: what the check before cannot see.
        with open(os.path.join(new_code, "tagpup_web.py"), "w", encoding="utf-8") as handle:
            handle.write("import sys\nsys.exit(0 if '--help' in sys.argv else 7)\n")
        self.assertFalse(self.hand_over())
        self.assertIsNotNone(old.wait(timeout=30), "the old server was never ended")
        self.assertEqual({OLD}, {self.version(port) for port in self.ports.values()}, self.said)
        said = "\n".join(self.said)
        self.assertIn("TagPup %s did not start (it exited with 7)" % NEW, said)
        self.assertIn("Started TagPup %s again" % OLD, said)
        self.assertEqual(NEW, install_app.read_current(self.installed), "current.txt is the owner's to change")

    def test_with_nothing_running_it_only_installs(self):
        self.install(OLD)
        self.assertTrue(self.hand_over())
        self.assertEqual([], self.said)
        self.assertIsNone(self.version(self.ports["tagpup"]))


if __name__ == "__main__":
    unittest.main()

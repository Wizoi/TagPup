"""A launch of another version replaces the server running, without the always-on process
(tagpup.launcher; docs/findings.md, #726).

The owner installed a new version and still saw the old page: TagPup.cmd installed it, but
tagpup_web.py --open found a server answering on the port -- one started hours before from
the version before -- and only opened its page. They ended server processes by hand.

Here: what a launcher decides about each server it finds (make_way, with what it asks
the server stood in for); who may drain a server (a launcher on this machine with the
token of the server's record, nobody else); the version on every response; the records,
a stale one among them; and, for real, servers on ports of the test's own, in a home of
its own: one run from a checkout replaced by an installed version, two launches at once,
and a server that hangs.
"""
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

import tagpup_web  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import lifecycle as lifecycles  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

OLD, NEW = "20261004-100000-aaaaaaa", "20261004-110000-bbbbbbb"
TOKEN = "the-token-in-the-record"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Way(unittest.TestCase):
    """make_way, with the server it asks stood in for."""

    def setUp(self):
        self.said, self.opened, self.drained, self.ended, self.quiets = [], [], [], [], []
        self.clock = Clock()
        self.answers = []          # what each GET /api/server says, in turn (the last repeats)
        self.drains = []           # what each drain answers, in turn
        self.found = {"pid": 4242, "started": 1, "ports": {"tagpup": 5, "tuner": 6}, "version": OLD,
                      "token": TOKEN, "supervised": False}
        self.port_held = True

        def ask(port):
            return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

        def drain(port, token, quiet=None):
            self.drained.append(token)
            self.quiets.append(quiet)
            return self.drains.pop(0)

        def end(found, port, answering, sleep=None):
            self.ended.append(found["pid"])
            self.port_held = False
            return True
        for name, value in (("ask", ask), ("drain", drain), ("end", end), ("record", lambda port: self.found)):
            patcher = mock.patch.object(launcher, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def way(self, version=NEW):
        return launcher.make_way(5, version, say=self.said.append, open_page=lambda: self.opened.append(1),
                                 answering=lambda port: self.port_held, sleep=self.clock.sleep, clock=self.clock)

    def status(self, version=OLD, supervised=False, busy=()):
        return {"version": version, "supervised": supervised, "taking_work": True, "requests": 0, "busy": list(busy)}

    def test_nothing_answering_is_served(self):
        self.port_held = False
        self.assertEqual((launcher.SERVE, False), self.way())

    def test_the_version_launched_is_opened_and_left_alone(self):
        self.answers = [self.status(NEW)]
        self.assertEqual((launcher.OPEN, False), self.way())
        self.assertEqual([], self.drained)
        self.assertEqual([], self.ended)

    def test_another_version_idle_drains_is_ended_and_the_launch_serves(self):
        self.answers = [self.status(OLD)]
        self.drains = [{"drained": True}]
        self.assertEqual((launcher.SERVE, False), self.way())
        self.assertEqual([TOKEN], self.drained, "drained with the record's token")
        self.assertEqual([4242], self.ended)
        self.assertEqual([], self.opened, "the old page was opened")

    def test_one_run_from_a_checkout_is_replaced_by_an_installed_version(self):
        self.answers = [self.status(None)]
        self.found["version"] = None
        self.drains = [{"drained": True}]
        self.assertEqual(launcher.SERVE, self.way()[0])
        self.assertEqual([4242], self.ended)

    def test_a_long_job_is_waited_out_with_the_running_page_open_and_never_ended_under(self):
        """The startup migrations, an index, a bulk edit: the drain is refused while they run
        (Lifecycle.long_work), and the launcher asks again, saying so once per change."""
        self.answers = [self.status(OLD)]
        migrating = ["bringing 1 library(ies) up to date"]
        self.drains = [{"drained": False, "waiting_for": migrating}, {"drained": False, "waiting_for": migrating},
                       {"drained": False, "waiting_for": ["indexing in 1 library(ies)"]}, {"drained": True}]
        self.assertEqual((launcher.SERVE, True), self.way())
        self.assertEqual([1], self.opened, "the running page is opened once while it waits")
        busy = [line for line in self.said if "is busy" in line]
        self.assertEqual(2, len(busy), self.said)
        self.assertIn("bringing 1 library(ies) up to date", busy[0])
        self.assertIn(NEW, busy[0])
        self.assertEqual([4242], self.ended, "ended before the drain said it was done")
        self.assertEqual(4, len(self.drained))

    def test_the_always_on_processs_server_is_left_to_it(self):
        self.answers = [self.status(OLD, supervised=True)]
        self.assertEqual(launcher.OPEN, self.way()[0])
        self.assertEqual([], self.drained)

    def test_one_with_no_record_is_not_replaced_and_the_owner_is_told(self):
        """A version from before the records, another user's (their records are in their own
        folder), another home's: nothing to drain it with."""
        self.answers = [self.status(OLD)]
        self.found = None
        self.assertEqual(launcher.OPEN, self.way()[0])
        self.assertEqual([], self.drained)
        self.assertEqual([], self.ended)
        self.assertTrue(any("cannot be asked to make way" in line for line in self.said), self.said)

    def test_a_refused_drain_is_not_forced(self):
        self.answers = [self.status(OLD)]
        self.drains = [{"drained": False, "refused": 403}]
        self.assertEqual(launcher.OPEN, self.way()[0])
        self.assertEqual([], self.ended)

    def test_a_hung_server_with_a_live_record_is_ended_after_its_tries(self):
        self.answers = [None]
        self.assertEqual(launcher.SERVE, self.way()[0])
        self.assertEqual([4242], self.ended)
        self.assertTrue(any("has not answered" in line for line in self.said), self.said)

    def test_a_hung_server_the_always_on_process_runs_is_left_to_it(self):
        """#745: the hung path read no `supervised`; a launcher ended the supervisor's child."""
        self.answers = [None]
        self.found["supervised"] = True
        self.assertEqual(launcher.OPEN, self.way()[0])
        self.assertEqual([], self.ended)
        self.assertTrue(any("always-on process" in line for line in self.said), self.said)

    def test_a_page_in_use_is_not_cut_until_the_patience_runs_out(self):
        """#746: the drain waits for QUIET seconds with no request -- a save under way is not
        cut -- and, after QUIET_PATIENCE of a page asking all the while, takes the next moment
        with nothing in flight."""
        self.answers = [self.status(OLD)]
        in_use = {"drained": False, "waiting_for": ["a request 3s ago"]}
        tries = launcher.QUIET_PATIENCE // launcher.RETRY_BUSY
        self.drains = [dict(in_use) for _ in range(tries)] + [{"drained": True}]
        self.assertEqual(launcher.SERVE, self.way()[0])
        self.assertEqual([launcher.QUIET] * tries, self.quiets[:tries])
        self.assertEqual(0, self.quiets[-1], "never stopped waiting for a quiet moment")
        self.assertEqual(tries + 1, len(self.quiets))
        busy = [line for line in self.said if "is busy" in line]
        self.assertEqual(1, len(busy), "said again at every try: %s" % busy)
        self.assertIn("the app is in use", busy[0])

    def test_a_silent_port_without_a_record_is_waited_for_then_opened_never_ended(self):
        """A server starting -- its record comes once it serves -- or not this user's."""
        self.answers = [None]
        self.found = None
        self.assertEqual(launcher.OPEN, self.way()[0])
        self.assertEqual([], self.ended)
        self.assertGreaterEqual(self.clock.now, launcher.STARTING_WAIT)


class Records(unittest.TestCase):
    def setUp(self):
        own_home.for_test(self, prefix="launcher_records_")

    def test_a_server_says_where_it_answers_and_forgets_only_its_own(self):
        launcher.say_where({"tagpup": 7, "tuner": 8}, OLD, TOKEN)
        found = launcher.record(8)
        self.assertEqual((os.getpid(), OLD, TOKEN, False), (found["pid"], found["version"], found["token"],
                                                            found["supervised"]))
        self.assertEqual({OLD}, launcher.versions_running())
        launcher.forget({"tagpup": 7, "tuner": 8})
        self.assertIsNone(launcher.record(7))

    def test_a_record_left_by_a_process_that_has_ended_is_nobody(self):
        """A server ended without stopping leaves its record; its id may be another's."""
        port = free_port()
        script = ("import sys; sys.path.insert(0, %r); from tagpup import launcher; "
                  "launcher.say_where({'tagpup': %d}, %r, 'gone')" % (WORKSPACE_DIR, port, OLD))
        done = processes.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(os.path.exists(launcher.record_path(port)))
        self.assertIsNone(launcher.record(port))
        self.assertEqual([], launcher.running())
        # The next server to say where it answers clears it.
        live = {"tagpup": free_port()}
        launcher.say_where(live, NEW, "t")
        self.addCleanup(launcher.forget, live)
        self.assertFalse(os.path.exists(launcher.record_path(port)), "a record of an ended server is kept")
        with mock.patch.object(launcher, "ask", return_value=None), \
                mock.patch.object(launcher.processes, "kill_tree") as killed:
            way = launcher.make_way(port, NEW, say=lambda line: None, open_page=lambda: None,
                                    answering=lambda p: False)
        self.assertEqual(launcher.SERVE, way[0])
        killed.assert_not_called()

    def test_a_record_naming_a_live_process_by_the_wrong_start_is_nobody(self):
        """#744: the start time is half the guard -- a record whose process ended and whose id
        another process now has. Here that other process is this one."""
        port = free_port()
        launcher.say_where({"tagpup": port}, OLD, "t")
        path = launcher.record_path(port)
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
        found["started"] += 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(found, handle)
        self.assertIsNone(launcher.record(port))
        self.assertEqual([], launcher.running())
        self.assertEqual(1, launcher.clear_stale())
        self.assertFalse(os.path.exists(path))
        with mock.patch.object(launcher.processes, "kill_tree") as killed:
            self.assertTrue(launcher.end(found, port, answering=lambda p: False, wait=0.5))
        killed.assert_not_called()

    def test_the_launchers_drain_asks_for_quiet_and_a_lease(self):
        sent = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"drained": true, "success": true}'

        def urlopen(request, timeout=None):
            sent.append((request.get_header(launcher.HEADER.capitalize()) or request.headers.get(launcher.HEADER),
                         json.loads(request.data.decode("utf-8"))))
            return Reply()
        with mock.patch.object(launcher.urllib.request, "urlopen", side_effect=urlopen):
            self.assertTrue(launcher.drain(5, TOKEN)["drained"])
        self.assertEqual({"seconds": launcher.DRAIN_SECONDS, "quiet": launcher.QUIET, "lease": launcher.LEASE},
                         sent[0][1])

    def test_the_folder_is_the_users_own_unless_named(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\someone\AppData\Local"}):
            os.environ.pop(launcher.ENV, None)
            self.assertEqual(os.path.join(r"C:\Users\someone\AppData\Local", "TagPup", "servers"), launcher.folder())


class WhoMayDrain(unittest.TestCase):
    """POST /api/server/drain: the supervisor's token, or a launcher's from this machine."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="launcher_drain_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.lifecycle = Lifecycle(version=OLD, launcher_token=TOKEN, work=lambda: [])
        self.app = web.create_app("tagpup", lifecycle=self.lifecycle)
        self.app.testing = True

    def drain(self, token=TOKEN, remote="127.0.0.1"):
        return self.app.test_client().post("/api/server/drain", json={"seconds": 5},
                                           headers={launcher.HEADER: token}, environ_base={"REMOTE_ADDR": remote})

    def test_a_launcher_on_this_machine_with_the_token_drains_it(self):
        reply = self.drain()
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertTrue(reply.get_json()["drained"])
        self.assertFalse(self.lifecycle.status()["taking_work"])

    def test_over_ipv6_loopback_too(self):
        self.assertEqual(200, self.drain(remote="::1").status_code)

    def test_another_machine_is_refused_whatever_it_sends(self):
        reply = self.drain(remote="192.168.1.20")
        self.assertEqual(403, reply.status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])

    def test_a_wrong_token_or_none_is_refused(self):
        self.assertEqual(403, self.drain(token="guessed").status_code)
        self.assertEqual(403, self.drain(token="").status_code)
        resume = self.app.test_client().post("/api/server/resume", headers={launcher.HEADER: "guessed"})
        self.assertEqual(403, resume.status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])

    def test_a_server_with_no_launcher_token_refuses_every_launcher(self):
        app = web.create_app("tagpup", lifecycle=Lifecycle(version=OLD, work=lambda: []))
        reply = app.test_client().post("/api/server/drain", json={"seconds": 5}, headers={launcher.HEADER: ""})
        self.assertEqual(403, reply.status_code)

    def test_a_lease_not_ended_gives_the_server_back_its_work(self):
        """#747: a launcher's window closed between its drain and the end left the server
        turning every request away for LEFT_DRAINED (180 s), past the pages' two minutes."""
        clock = [1000.0]
        lifecycle = Lifecycle(version=OLD, launcher_token=TOKEN, work=lambda: [], clock=lambda: clock[0])
        client = web.create_app("tagpup", lifecycle=lifecycle).test_client()
        reply = client.post("/api/server/drain", json={"seconds": 5, "lease": 20}, headers={launcher.HEADER: TOKEN})
        self.assertTrue(reply.get_json()["drained"], reply.get_json())
        self.assertEqual(503, client.get("/harbour/api/tags").status_code)
        clock[0] += 21
        self.assertEqual(200, client.get("/harbour/api/tags").status_code, "still drained past its lease")

    def test_a_drain_with_no_lease_keeps_the_supervisors_wait(self):
        clock = [1000.0]
        lifecycle = Lifecycle(version=OLD, launcher_token=TOKEN, work=lambda: [], clock=lambda: clock[0])
        client = web.create_app("tagpup", lifecycle=lifecycle).test_client()
        client.post("/api/server/drain", json={"seconds": 5}, headers={launcher.HEADER: TOKEN})
        clock[0] += 21
        self.assertEqual(503, client.get("/harbour/api/tags").status_code)
        clock[0] += lifecycles.LEFT_DRAINED
        self.assertEqual(200, client.get("/harbour/api/tags").status_code)

    def test_a_lease_out_of_range_is_refused(self):
        for lease in (0, -1, lifecycles.LEFT_DRAINED + 1, "20", True):
            reply = self.app.test_client().post("/api/server/drain", json={"seconds": 5, "lease": lease},
                                                headers={launcher.HEADER: TOKEN})
            self.assertEqual(400, reply.status_code, lease)
        self.assertTrue(self.lifecycle.status()["taking_work"])

    def test_a_launchers_drain_waits_for_the_startup_migrations(self):
        """The startup migration thread is long work (#664): the drain is refused while it runs."""
        lifecycle = Lifecycle(version=OLD, launcher_token=TOKEN)
        app = web.create_app("tagpup", lifecycle=lifecycle)
        with mock.patch.object(lifecycles.library_actions, "bringing_up_to_date", return_value=1):
            reply = app.test_client().post("/api/server/drain", json={"seconds": 5}, headers={launcher.HEADER: TOKEN})
        self.assertEqual({"drained": False, "success": True,
                          "waiting_for": ["bringing 1 library(ies) up to date"]}, reply.get_json())
        self.assertTrue(lifecycle.status()["taking_work"])


class TheVersionOnEveryResponse(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="launcher_version_")
        library_actions.create(self.home.library("harbour.db"))

    def test_each_response_names_the_version_answering(self):
        lifecycle = Lifecycle(version=NEW, launcher_token=TOKEN, work=lambda: [])
        client = web.create_app("tagpup", lifecycle=lifecycle).test_client()
        for path in ("/api/server", "/harbour/api/tags", "/"):
            self.assertEqual(NEW, client.get(path).headers.get(launcher.VERSION_HEADER), path)
        client.post("/api/server/drain", json={"seconds": 5}, headers={launcher.HEADER: TOKEN})
        refused = client.get("/harbour/api/tags")
        self.assertEqual(503, refused.status_code)
        self.assertEqual(NEW, refused.headers.get(launcher.VERSION_HEADER))

    def test_one_run_from_a_checkout_says_so(self):
        client = web.create_app("tuner", lifecycle=Lifecycle(version=None)).test_client()
        self.assertEqual(launcher.FROM_A_CHECKOUT, client.get("/api/server").headers.get(launcher.VERSION_HEADER))


class TheLaunchersMain(unittest.TestCase):
    """tagpup_web.main as a launcher, with the server it would run stood in for."""

    def setUp(self):
        own_home.for_test(self, prefix="launcher_main_")
        for patch in (mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"}),
                      mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)"),
                      mock.patch.object(tagpup_web.time, "sleep")):
            patch.start()
            self.addCleanup(patch.stop)

    def main(self, ways, bind=None, serve=None):
        ways = list(ways)
        with mock.patch.object(tagpup_web.launcher, "make_way", side_effect=lambda *a, **k: ways.pop(0)), \
                mock.patch.object(tagpup_web.web, "bind_all", side_effect=bind or (lambda ports, listen: [])) as bound, \
                mock.patch.object(tagpup_web, "open_page") as opened, \
                mock.patch.object(tagpup_web.web, "serve", side_effect=serve) as served:
            code = tagpup_web.main(["--open", "tagpup", "--tagpup-port", "7", "--tuner-port", "8"])
        return code, bound, opened, served

    def test_it_binds_before_it_serves_and_its_record_carries_the_drain_token(self):
        seen = []

        def serve(apps, ready=None, sockets=None, **how):
            seen.append(sockets)
            ready()
            seen.append(launcher.record(7))
        code, bound, opened, served = self.main([(launcher.SERVE, False)], serve=serve)
        self.assertEqual(0, code)
        bound.assert_called_once_with([7, 8], "local")
        self.assertEqual([], seen[0], "served on the sockets it bound")
        lifecycle = served.call_args[0][0][7].config["LIFECYCLE"]
        self.assertTrue(lifecycle.allows_launcher(seen[1]["token"], "127.0.0.1"))
        self.assertIsNone(launcher.record(7), "the record outlived the server")
        opened.assert_called_once_with("http://localhost:7/")

    def test_a_launch_that_loses_the_ports_to_another_opens_the_winners_page(self):
        binds = [OSError("in use")]

        def bind(ports, listen):
            if binds:
                raise binds.pop(0)
            return []
        code, bound, opened, served = self.main([(launcher.SERVE, False), (launcher.OPEN, False)], bind=bind)
        self.assertEqual(0, code)
        served.assert_not_called()
        opened.assert_called_once_with("http://localhost:7/")

    def test_the_page_opened_while_it_waited_is_not_opened_again(self):
        code, bound, opened, served = self.main([(launcher.SERVE, True)],
                                                serve=lambda apps, ready=None, **how: ready())
        self.assertEqual(0, code)
        opened.assert_not_called()


@unittest.skipUnless(os.name == "nt", "the launchers are Windows'")
class ForReal(unittest.TestCase):
    """Real servers on ports of the test's own, in a home of its own."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="launcher_real_")
        # Before anything starts: every process the test started, and theirs (#727).
        self.addCleanup(own_home.end_processes, self.home.root)
        self.ports = {"tagpup": free_port(), "tuner": free_port()}
        self.env = dict(os.environ, TAGPUP_NO_JOBS="1", TAGPUP_NO_MODEL_WEIGHTS="1", TAGPUP_WEB_NO_WARMUP="1",
                        # The browser opens only on a first start: a test's never does.
                        TAGPUP_WEB_RELOADED="1")
        self.env.pop("TAGPUP_SUPERVISOR_TOKEN", None)

    def install(self, name):
        import install_app
        installed = os.path.join(self.home.root, "installed")
        install_app.install(installed, self.home.root, sys.executable, name=name, apply=True, say=lambda line: None)
        return os.path.join(installed, "versions", name)

    def start(self, code, *more):
        path = os.path.join(self.home.root, "launch_%d.log" % len(os.listdir(self.home.root)))
        log = open(path, "wb")
        self.addCleanup(log.close)
        started = processes.start([sys.executable, os.path.join(code, "tagpup_web.py"),
                                   "--tagpup-port", str(self.ports["tagpup"]), "--tuner-port", str(self.ports["tuner"])]
                                  + list(more), env=self.env, cwd=self.home.root, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: started.poll() is None and processes.kill_tree(started.pid))
        started.log_path = path
        return started

    def said(self, started):
        """What a launch said at its window."""
        with open(started.log_path, encoding="utf-8", errors="replace") as handle:
            return handle.read()

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

    def logs(self):
        text = []
        for name in sorted(os.listdir(self.home.root)):
            if name.startswith("launch_"):
                with open(os.path.join(self.home.root, name), encoding="utf-8", errors="replace") as handle:
                    text.append(name + ":\n" + handle.read()[-3000:])
        return "\n".join(text)

    def test_a_server_run_from_a_checkout_makes_way_for_the_installed_version(self):
        old = self.start(WORKSPACE_DIR)
        self.wait_until(lambda: self.version(self.ports["tagpup"]) == "checkout", why="the old server never answered")
        new_code = self.install(NEW)
        new = self.start(new_code, "--open", "tagpup")
        self.wait_until(lambda: all(self.version(port) == NEW for port in self.ports.values()), seconds=120,
                        why="the new version never answered on both ports:\n" + self.logs())
        self.assertIsNotNone(old.wait(timeout=30), "the old server still runs")
        self.assertIsNone(new.poll(), "the launch that replaced it is the server")
        found = launcher.record(self.ports["tuner"])
        self.assertEqual(NEW, found["version"], "the record is not the new server's")
        # What the owner sees at the launcher's window.
        said = self.said(new)
        self.assertIn("TagPup: TagPup from a checkout is running", said)
        self.assertIn("handing over to %s" % NEW, said)
        self.assertIn("finished what it was doing; ending it and starting %s" % NEW, said)

    def test_two_launches_at_once_leave_exactly_one_new_server(self):
        old_code, new_code = self.install(OLD), self.install(NEW)
        old = self.start(old_code)
        self.wait_until(lambda: self.version(self.ports["tagpup"]) == OLD, why="the old server never answered")
        first = self.start(new_code, "--open", "tagpup")
        second = self.start(new_code, "--open", "tuner")
        self.wait_until(lambda: all(self.version(port) == NEW for port in self.ports.values()), seconds=120,
                        why="the new version never answered on both ports:\n" + self.logs())
        self.assertIsNotNone(old.wait(timeout=30), "the old server still runs")
        # The one that did not win opens the page of the one that did, and exits.
        self.wait_until(lambda: (first.poll() is None) != (second.poll() is None), seconds=120,
                        why="not exactly one launch serves:\n" + self.logs())
        loser = first if first.poll() is not None else second
        self.assertEqual(0, loser.returncode, self.logs())
        winner = second if loser is first else first
        self.assertEqual({launcher.record(port)["pid"] for port in self.ports.values()},
                         {launcher.record(self.ports["tagpup"])["pid"]})
        self.assertIsNone(winner.poll())

    def test_a_server_that_hangs_is_ended_once_its_tries_are_out(self):
        """It holds the port and says where it answers, and never answers a request."""
        port = self.ports["tagpup"]
        script = ("import socket, sys, time; sys.path.insert(0, %r); from tagpup import launcher; "
                  "s = socket.socket(); s.bind(('127.0.0.1', %d)); s.listen(5); "
                  "launcher.say_where({'tagpup': %d}, %r, 'hung'); time.sleep(600)" % (WORKSPACE_DIR, port, port, OLD))
        hung = processes.start([sys.executable, "-c", script], env=self.env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: hung.poll() is None and processes.kill_tree(hung.pid))
        self.wait_until(lambda: launcher.record(port), why="the hung server never said where it answers")
        said = []
        with mock.patch.object(launcher, "HUNG_TIMEOUT", 0.5):
            way = launcher.make_way(port, NEW, say=said.append, open_page=lambda: None, answering=tagpup_web.answering)
        self.assertEqual(launcher.SERVE, way[0], said)
        self.assertIsNotNone(hung.wait(timeout=30), "the hung server still runs")
        self.assertIsNone(launcher.record(port))


if __name__ == "__main__":
    unittest.main()

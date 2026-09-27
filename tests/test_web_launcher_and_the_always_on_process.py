"""tagpup_web.py beside the always-on process (docs/ARCHITECTURE.md, phase 8).

As its child, the server says where it answers (data/server.json) and waits, rather than
crashing, while another server holds its ports. As a launcher -- TagPup.cmd and
TagTuner.cmd, which name the installed app -- it opens the page on the always-on server
when the owner chose it, starting the process if it is not running, rather than
starting a server of its own. Beside its requests it runs the background tasks
(tagpup.runtime.BACKGROUND), and lets its models go after --release-models-after.
"""
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

import tagpup_web  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup import supervisor  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402

ARGS = ["--tagpup-port", "1", "--tuner-port", "2"]


def quiet():
    return [mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"}),
            mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")]


class AsTheSupervisorsChild(unittest.TestCase):
    def setUp(self):
        own_home.for_test(self, prefix="web_child_")
        for patch in quiet():
            patch.start()
            self.addCleanup(patch.stop)

    def test_it_says_where_it_answers_once_bound_and_forgets_it_after(self):
        seen = []

        def serve(apps, ready=None, **how):
            ready()
            seen.append(supervisor.read_json(supervisor.data_file(supervisor.SERVER_FILE)))
            seen.append(supervisor.TOKEN in os.environ)
        with mock.patch.dict(os.environ, {supervisor.TOKEN: "t"}), \
                mock.patch.object(tagpup_web, "answering", return_value=False), \
                mock.patch.object(tagpup_web.web, "serve", side_effect=serve):
            self.assertEqual(0, tagpup_web.main(ARGS))
        self.assertEqual({"tagpup": 1, "tuner": 2}, seen[0]["ports"])
        self.assertFalse(seen[1], "a program the server starts would inherit the token")
        self.assertEqual(os.getpid(), seen[0]["pid"])
        self.assertFalse(os.path.exists(supervisor.data_file(supervisor.SERVER_FILE)))

    def test_a_server_not_supervised_writes_nothing(self):
        with mock.patch.dict(os.environ), mock.patch.object(tagpup_web.web, "serve",
                                                            side_effect=lambda apps, ready=None, **how: ready()):
            os.environ.pop(supervisor.TOKEN, None)
            self.assertEqual(0, tagpup_web.main(ARGS))
        self.assertFalse(os.path.exists(supervisor.data_file(supervisor.SERVER_FILE)))

    def test_it_waits_while_another_server_holds_its_ports(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        with mock.patch.dict(os.environ, {supervisor.TOKEN: "t"}), \
                mock.patch.object(tagpup_web.web, "serve") as serve:
            code = tagpup_web.main(["--tagpup-port", str(port), "--tuner-port", "2"])
        self.assertEqual(supervisor.PORTS_TAKEN, code)
        serve.assert_not_called()


class AsTheLauncher(unittest.TestCase):
    def setUp(self):
        own_home.for_test(self, prefix="web_launcher_")
        for patch in quiet():
            patch.start()
            self.addCleanup(patch.stop)
        self.installed = tempfile.mkdtemp(prefix="installed_", dir=os.environ["TAGPUP_HOME"])

    def choose_always_on(self):
        with open(os.path.join(self.installed, supervisor.ALWAYS_ON), "w", encoding="utf-8") as handle:
            handle.write("a shortcut\n")

    def launch(self, running, answers):
        answers = list(answers)
        with mock.patch.object(tagpup_web.supervisor, "running", return_value=running), \
                mock.patch.object(tagpup_web.supervisor, "start_in_background") as start, \
                mock.patch.object(tagpup_web, "answering", side_effect=lambda port: answers.pop(0)), \
                mock.patch.object(tagpup_web, "open_page") as opened, \
                mock.patch.object(tagpup_web.time, "sleep"), \
                mock.patch.object(tagpup_web.web, "serve") as serve:
            code = tagpup_web.main(["--open", "tuner", "--installed", self.installed] + ARGS)
        return code, start, opened, serve

    def test_starts_the_always_on_process_and_opens_its_page(self):
        self.choose_always_on()
        code, start, opened, serve = self.launch(None, [False, False, False, True])
        self.assertEqual(0, code)
        start.assert_called_once_with(self.installed)
        opened.assert_called_once_with("http://localhost:2/")
        serve.assert_not_called()

    def test_waits_for_one_already_starting(self):
        self.choose_always_on()
        code, start, opened, serve = self.launch({"pid": 1}, [False, False, True])
        self.assertEqual(0, code)
        start.assert_not_called()
        opened.assert_called_once_with("http://localhost:2/")
        serve.assert_not_called()

    def test_serves_itself_when_the_owner_has_not_chosen_it(self):
        code, start, opened, serve = self.launch(None, [False, False, False])
        self.assertEqual(0, code)
        start.assert_not_called()
        serve.assert_called_once()


class BesideItsRequests(unittest.TestCase):
    def setUp(self):
        own_home.for_test(self, prefix="web_background_")
        for patch in quiet():
            patch.start()
            self.addCleanup(patch.stop)

    def test_it_starts_the_background_tasks_and_stops_them(self):
        log = []

        class Task:
            def __init__(self, name):
                self.name = name

            def start(self):
                log.append("start " + self.name)

            def stop(self, timeout=30):
                log.append("stop " + self.name)
                return True

            def busy(self):
                return False
        registry = {"watcher": lambda runtime: Task("watcher")}
        with mock.patch.object(tagpup_web.web, "serve", side_effect=lambda *a, **k: log.append("serve")), \
                mock.patch.dict(runtimes.BACKGROUND, registry, clear=True):
            tagpup_web.main(ARGS)
        self.assertEqual(["start watcher", "serve", "stop watcher"], log)

    def test_it_lets_the_models_go_after_thirty_minutes_unless_told_otherwise(self):
        made = []
        with mock.patch.object(tagpup_web.web, "serve"), \
                mock.patch.object(tagpup_web, "Runtime", side_effect=lambda **kw: made.append(kw) or Runtime(**kw)):
            tagpup_web.main(ARGS)
            tagpup_web.main(ARGS + ["--release-models-after", "5"])
            tagpup_web.main(ARGS + ["--release-models-after", "0"])
        self.assertEqual([30 * 60, 5 * 60, None], [kw["idle_after"] for kw in made])


if __name__ == "__main__":
    unittest.main()

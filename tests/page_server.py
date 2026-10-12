"""The real apps for a page test, on loopback ports the system chooses (contract table, rows 12 and 22).

    servers = page_server.PageServers(self, {"seed_name": seed})
    servers.run_node("page.test.mjs", "against the server")

A node test's `fetch` goes to them (tests/frontend/harness.mjs, `realLibrary`), which asks `/__seed/<seed_name>` for a library
of its own, made by `seed(Library)` with the code that makes the real rows; the seed returns what the page needs to know.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading

from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import lifecycle  # noqa: E402

FRONTEND = os.path.join(tagpup_config.CODE_ROOT, "tests", "frontend")


class PageServers:
    def __init__(self, testcase, seeds):
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self.testcase, self.seeds, self.made, self.lock = testcase, seeds, 0, threading.Lock()
        self.home = own_home.for_test(testcase)    # its reaper ends what a failed test leaves
        gate = lifecycle.Lifecycle(version=tagpup_config.code_version())
        self.urls = {}
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=None, lifecycle=gate)
            app.testing = True
            server = make_server("127.0.0.1", 0, self._seeding(app), threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            testcase.addCleanup(self._stop, server, thread)
            self.urls[kind] = "http://127.0.0.1:%d" % server.server_port

    @staticmethod
    def _stop(server, thread):
        server.shutdown()
        thread.join(30)
        server.server_close()

    def _seeding(self, app):
        """The app, and `/__seed/<name>` beside it: a library made by that seed, answered as {"library", ...what it says}."""
        def serve(environ, start_response):
            request = Request(environ)
            if not request.path.startswith("/__seed/"):
                return app(environ, start_response)
            seed = request.path.rsplit("/", 1)[1]
            with self.lock:
                self.made += 1
                library = "%s%d" % (seed, self.made)
                path = self.home.library(library + ".db")
                library_actions.create(path)
                facts = self.seeds[seed](Library(path))
            return Response(json.dumps({"library": library, **facts}), mimetype="application/json")(environ, start_response)
        return serve

    def run_node(self, test_file, only="against the server"):
        """Run the tests of a node file whose name holds `only` against these servers. Fails with node's output when node does,
        or when it ran none (a test that skipped itself ran nothing)."""
        node = shutil.which("node")
        self.testcase.assertTrue(node, "node is not on the PATH: the page tests need it")
        done = processes.run([node, "--test", "--test-name-pattern=" + only, os.path.join(FRONTEND, test_file)],
                             env=dict(os.environ, TAGPUP_PAGE_SERVERS=json.dumps(self.urls)), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=240)
        self.testcase.assertEqual(0, done.returncode, done.stdout)
        self.testcase.assertNotRegex(done.stdout, r"\bpass 0\b", "no test of the real server ran")
        return done.stdout

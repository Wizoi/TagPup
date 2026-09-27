"""The server lets the work under way finish before the always-on process moves it onto a
new version (docs/ARCHITECTURE.md, phase 8, "Updating itself"; tagpup.web.lifecycle).

Owner, 2026-09-26: it "auto updates at next available request, completing current calls
and actions first". So a drain turns new requests away (503, which the pages wait out),
lets the ones in flight finish -- a write among them -- and is refused outright while a
Suggest run, an index or a recurring job is under way. A deadline that passes gives the
server back its work; an update never interrupts a write.
"""
import os
import sys
import threading
import time
import unittest

from flask import jsonify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import supervisor  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import lifecycle as lifecycles  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

TOKEN = "a-token-of-the-supervisors"
HEADERS = {supervisor.TOKEN_HEADER: TOKEN}


class FakeBackground:
    """tagpup.runtime.Background's shape: what a drain stops and a resume starts."""

    def __init__(self):
        self.calls = []
        self.working = []

    def start(self):
        self.calls.append("start")

    def stop(self, timeout=30):
        self.calls.append("stop")
        return []

    def busy(self):
        return list(self.working)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="drain_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)
        self.long = []
        self.background = FakeBackground()
        self.clock = Clock()
        self.lifecycle = Lifecycle(version="20260926-101500-abc1234", token=TOKEN, background=self.background,
                                   work=lambda: list(self.long))
        self.app = web.create_app("tagpup", startup=self.library, lifecycle=self.lifecycle)
        self.app.testing = True
        # A write that takes a while: what is in flight when the drain begins.
        self.entered, self.release, self.written = threading.Event(), threading.Event(), []

        def slow_write():
            self.entered.set()
            self.release.wait(30)
            self.written.append("row")
            return jsonify({"success": True, "changed": 1})
        self.app.add_url_rule("/api/test-slow-write", "slow_write", slow_write, methods=["POST"])

    def client(self):
        return self.app.test_client()

    def write(self):
        """The slow write, its reply read to the end and closed, as a server sends it: a
        request is in flight until then."""
        return self.client().post("/harbour/api/test-slow-write", buffered=True)

    def in_thread(self, call):
        found = []
        thread = threading.Thread(target=lambda: found.append(call()), daemon=True)
        thread.start()
        return thread, found

    def drain(self, seconds=30):
        return self.client().post("/api/server/drain", json={"seconds": seconds}, headers=HEADERS)

    def wait_until(self, check, seconds=10):
        deadline = time.time() + seconds
        while not check():
            self.assertLess(time.time(), deadline, "timed out")
            time.sleep(0.01)


class ADrain(Base):
    def test_lets_the_write_in_flight_finish_and_turns_new_requests_away(self):
        writing, wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        draining, drained = self.in_thread(self.drain)
        self.wait_until(lambda: not self.lifecycle.status()["taking_work"])

        refused = self.client().get("/harbour/api/tags")
        self.assertEqual(503, refused.status_code)
        self.assertEqual("1", refused.headers[lifecycles.UPDATING_HEADER])
        self.assertEqual(str(lifecycles.RETRY_AFTER_SECONDS), refused.headers["Retry-After"])
        self.assertTrue(refused.get_json()["updating"])
        # Asked while draining, the server still says how it is.
        status = self.client().get("/api/server").get_json()
        self.assertEqual((False, 1), (status["taking_work"], status["requests"]))
        time.sleep(0.3)
        self.assertTrue(draining.is_alive(), "drained with a write in flight")
        self.assertEqual([], self.written)

        self.release.set()
        writing.join(10)
        draining.join(10)
        self.assertEqual(200, wrote[0].status_code)
        self.assertEqual(["row"], self.written, "the write was interrupted")
        self.assertEqual({"drained": True, "success": True}, drained[0].get_json())
        self.assertEqual(["stop"], self.background.calls, "the recurring jobs were not stopped")
        self.assertEqual(503, self.client().get("/harbour/api/tags").status_code,
                         "a drained server took new work before it was stopped")

    def test_is_refused_at_once_while_a_long_run_is_under_way(self):
        self.long.append("1 Suggest run(s)")
        answer = self.drain().get_json()
        self.assertEqual({"drained": False, "waiting_for": ["1 Suggest run(s)"], "success": True}, answer)
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code, "work was turned away")
        self.assertEqual([], self.background.calls)
        self.background.working.append("recurring jobs")
        self.long.clear()
        self.assertEqual(["recurring jobs"], self.drain().get_json()["waiting_for"])

    def test_that_runs_out_of_time_takes_work_again(self):
        writing, _wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        answer = self.drain(seconds=0.3).get_json()
        self.assertEqual({"drained": False, "waiting_for": ["1 request(s)"], "success": True}, answer)
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code)
        self.assertEqual(["stop", "start"], self.background.calls, "the recurring jobs were not started again")
        self.release.set()
        writing.join(10)
        self.assertEqual(["row"], self.written)

    def test_left_undone_by_a_supervisor_that_went_away_ends_by_itself(self):
        self.lifecycle._clock = self.clock
        self.assertTrue(self.drain().get_json()["drained"])
        self.assertEqual(503, self.client().get("/harbour/api/tags").status_code)
        self.clock.now += lifecycles.LEFT_DRAINED + 1
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code)
        self.assertEqual(["stop", "start"], self.background.calls)

    def test_can_be_resumed_by_the_supervisor(self):
        self.assertTrue(self.drain().get_json()["drained"])
        self.assertTrue(self.client().post("/api/server/resume", headers=HEADERS).get_json()["resumed"])
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code)


class OnlyTheSupervisor(Base):
    def test_may_drain_or_resume_the_server(self):
        for headers in ({}, {supervisor.TOKEN_HEADER: "a guess"}):
            self.assertEqual(403, self.client().post("/api/server/drain", json={}, headers=headers).status_code)
            self.assertEqual(403, self.client().post("/api/server/resume", headers=headers).status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])

    def test_and_a_server_it_did_not_start_is_drained_by_nobody(self):
        app = web.create_app("tagpup", startup=self.library)
        self.assertEqual(403, app.test_client().post("/api/server/drain", json={}, headers=HEADERS).status_code)
        self.assertEqual(403, app.test_client().post("/api/server/drain", json={},
                                                     headers={supervisor.TOKEN_HEADER: ""}).status_code)

    def test_a_deadline_must_be_a_number_of_seconds(self):
        for seconds in (0, -1, "60", True, lifecycles.MAX_DRAIN_SECONDS + 1):
            answer = self.client().post("/api/server/drain", json={"seconds": seconds}, headers=HEADERS)
            self.assertEqual(400, answer.status_code, seconds)
        self.assertTrue(self.lifecycle.status()["taking_work"])


class WhichVersionAnswers(Base):
    def test_both_apps_say_it_with_or_without_a_library(self):
        tuner = web.create_app("tuner", startup=self.library, lifecycle=self.lifecycle)
        for app in (self.app, tuner):
            for url in ("/api/server", "/harbour/api/server"):
                status = app.test_client().get(url).get_json()
                self.assertEqual("20260926-101500-abc1234", status["version"])
                self.assertEqual((True, True), (status["supervised"], status["taking_work"]))

    def test_an_app_made_without_a_lifecycle_says_its_code_folders(self):
        from tagpup import config as tagpup_config
        app = web.create_app("tagpup", startup=self.library)
        self.assertEqual(tagpup_config.code_version(), app.test_client().get("/api/server").get_json()["version"])
        self.assertFalse(app.test_client().get("/api/server").get_json()["supervised"])


class WhatALongRunIs(unittest.TestCase):
    """The drain's refusal reads the process's own runs: a Suggest run and an index."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="drain_runs_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.go = threading.Event()
        self.addCleanup(self.go.set)

    def test_an_index_under_way(self):
        def index(folder, cluster, report):
            self.go.wait(30)
            from tagpup.core.result import Result
            return Result()
        self.assertEqual([], lifecycles.long_work())
        indexing_jobs.queue_for(self.library).start([self.home.root], index)
        self.assertEqual(["indexing in 1 library(ies)"], lifecycles.long_work())
        self.go.set()
        deadline = time.time() + 10
        while lifecycles.long_work() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual([], lifecycles.long_work())

    def test_a_suggest_run_under_way(self):
        go = self.go

        class Work:
            def photos(self):
                go.wait(30)
                return {}

            def begin(self):
                raise AssertionError("no photos, no model")
        suggestion_jobs.runs_for(self.library).start(self.home.root, Work())
        self.assertEqual(["1 Suggest run(s)"], lifecycles.long_work())
        self.go.set()
        deadline = time.time() + 10
        while lifecycles.long_work() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual([], lifecycles.long_work())


if __name__ == "__main__":
    unittest.main()

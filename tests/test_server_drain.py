"""The server lets the work under way finish before a launch or an install moves it onto a
new version (docs/ARCHITECTURE.md, phase 8, "Updating itself"; tagpup.web.lifecycle).

Owner, 2026-09-26: it "auto updates at next available request, completing current calls
and actions first". So a drain turns new requests away (503, which the pages wait out),
lets the ones in flight finish -- a write among them -- and is refused outright while a
Suggest run, an index or a recurring job is under way. A deadline that passes gives the
server back its work; an update never interrupts a write. Only a launcher on this machine
drains (the token of the server's record), always with a lease: a launcher whose window
closes between its drain and the end it would have made does not leave the server turning
requests away. Every response says which version answers.

Lifted: test_launcher_hands_over's WhoMayDrain (#747 the lease, #664 the startup migrations) and
TheVersionOnEveryResponse, and the resume route's test (the route is deleted, DECISIONS 2026-10-15). Breaks that fail
the kept tests, each applied to a copy of the code: `self._lease = lease` read as LEFT_DRAINED fails
test_a_drain_left_undone_by_a_launcher_that_went_away_ends_at_its_lease; security.from_this_pc always True fails
test_only_this_machine_may_drain; `bringing` dropped from lifecycle.long_work fails
test_is_refused_while_the_startup_migrations_run; the version label dropped from the gate's start_response fails
test_each_response_names_the_version_answering_also_when_turned_away; the lease check dropped from the drain route
fails test_a_deadline_and_a_lease_must_be_numbers_of_seconds.
"""
import collections
import os
import sys
import threading
import time
import unittest
from unittest import mock

from flask import jsonify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from waiting import wait_until  # noqa: E402

from tagpup import launcher  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.web import lifecycle as lifecycles  # noqa: E402
from tagpup.web import responses  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.jobs import watching  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import batches  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, job_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

TOKEN = "a-token-of-the-launchers"
HEADERS = {launcher.HEADER: TOKEN}
#: What the launcher asks for (launcher.LEASE): the drain requires one.
LEASE = 20


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
        self.runtime = Runtime()
        self.reads = ReadsCounted()
        self.lifecycle = Lifecycle(version="20260926-101500-abc1234", launcher_token=TOKEN, background=self.background,
                                   work=lambda: list(self.long), runtime=self.runtime, clock=self.reads)
        self.app = web.create_app("tagpup", startup=self.library, runtime=self.runtime, lifecycle=self.lifecycle)
        self.app.testing = True
        self.addCleanup(self.lifecycle.resume)      # a drain asks the batches to stop, and keeps asking until it resumes
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
        return self.client().post("/api/server/drain", json={"seconds": seconds, "lease": LEASE}, headers=HEADERS)


class ReadsCounted:
    """time.monotonic for a Lifecycle, counting its reads by the thread and the function that read it: a drain reads it
    once each time it has looked and goes on waiting, which is what a test waits for in place of a fixed time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reads = collections.Counter()

    def __call__(self):
        function = sys._getframe(1).f_code.co_name
        with self._lock:
            self._reads[(threading.get_ident(), function)] += 1
        return time.monotonic()

    def by(self, function, thread=None):
        """The reads in `function`, on `thread` (a Thread) or on any."""
        with self._lock:
            return sum(count for (ident, name), count in self._reads.items()
                       if name == function and (thread is None or ident == thread.ident))


class ADrain(Base):
    def test_lets_the_write_in_flight_finish_and_turns_new_requests_away(self):
        writing, wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        draining, drained = self.in_thread(self.drain)
        wait_until(lambda: not self.lifecycle.status()["taking_work"])

        refused = self.client().get("/harbour/api/tags")
        self.assertEqual(503, refused.status_code)
        self.assertEqual("1", refused.headers[lifecycles.UPDATING_HEADER])
        self.assertEqual(str(responses.RETRY_AFTER_SECONDS), refused.headers["Retry-After"])
        self.assertTrue(refused.get_json()["updating"])
        # Asked while draining, the server still says how it is.
        status = self.client().get("/api/server").get_json()
        self.assertEqual((False, 1), (status["taking_work"], status["requests"]))
        # Once to bound the background's stop, once more after it found the write in flight and went on waiting.
        wait_until(lambda: self.reads.by("_drain_until") >= 2, what="the drain to look at the write in flight")
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
        self.long.append(lifecycles.Work(lifecycles.SUGGEST, None, "1 Suggest run(s)"))
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

    def test_a_request_in_flight_beside_a_batch_of_no_request_is_still_named(self):
        """The server's settle thread carries out a batch of its own (file_changes._settle_in_turn), no request: a write
        in flight beside it is still a request the drain waits for and names. On the code before, every batch was taken
        for a request in flight and the write was left out: the drain answered drained with the write still running."""
        holding, done = threading.Event(), threading.Event()

        def settling():
            with batches.claim(self.library, "add to all selected", 30):
                holding.set()
                done.wait(30)
        settle = threading.Thread(target=settling, name="SettleThread harbour", daemon=True)
        settle.start()
        self.addCleanup(settle.join, 30)
        self.addCleanup(done.set)
        self.assertTrue(holding.wait(10))
        writing, _wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        answer = self.drain(seconds=0.3).get_json()
        self.assertEqual({"drained": False, "waiting_for": ["1 request(s)"], "success": True}, answer)
        self.release.set()
        writing.join(10)
        self.assertEqual(["row"], self.written)

    def test_a_drain_left_undone_by_a_launcher_that_went_away_ends_at_its_lease(self):
        """#747: a launcher's window closed between its drain and the end left the server turning every request away
        for LEFT_DRAINED (180 s), past the pages' two minutes; the launcher asks for LEASE."""
        self.lifecycle._clock = self.clock
        self.assertTrue(self.drain().get_json()["drained"])
        self.assertEqual(503, self.client().get("/harbour/api/tags").status_code)
        self.clock.now += LEASE - 1
        self.assertEqual(503, self.client().get("/harbour/api/tags").status_code, "taken back before its lease")
        self.clock.now += 2
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code, "still drained past its lease")
        self.assertEqual(["stop", "start"], self.background.calls)

    def test_is_refused_while_the_startup_migrations_run(self):
        """#664: the startup migration thread is long work."""
        runtime = Runtime()
        lifecycle = Lifecycle(version="v", launcher_token=TOKEN, runtime=runtime)
        app = web.create_app("tagpup", runtime=runtime, lifecycle=lifecycle)
        with mock.patch.object(lifecycles.library_actions, "bringing_up_to_date", return_value=1):
            reply = app.test_client().post("/api/server/drain", json={"seconds": 5, "lease": LEASE}, headers=HEADERS)
        self.assertEqual({"drained": False, "success": True,
                          "waiting_for": ["bringing 1 library(ies) up to date"]}, reply.get_json())
        self.assertTrue(lifecycle.status()["taking_work"])


class ThroughALibrarysAddress(Base):
    """The pages ask /<library>/api/server: the gear for its version, and a page whose
    image failed, to learn whether the server is away (web/common/api.js). That probe is
    turned away while the server drains -- what tells the page it is -- where the bare
    /api/server is answered. A drain through a library's address was counted as its own
    request in flight, and waited out its deadline for itself."""

    def test_the_status_answers_while_open_and_is_turned_away_while_draining(self):
        status = self.client().get("/harbour/api/server")
        self.assertEqual(200, status.status_code)
        self.assertEqual("20260926-101500-abc1234", status.get_json()["version"])
        self.assertTrue(self.drain().get_json()["drained"])
        refused = self.client().get("/harbour/api/server")
        self.assertEqual(503, refused.status_code)
        self.assertEqual("1", refused.headers[lifecycles.UPDATING_HEADER])
        self.assertEqual(200, self.client().get("/api/server").status_code)

    def test_a_drain_through_it_does_not_wait_for_itself(self):
        started = time.monotonic()
        answer = self.client().post("/harbour/api/server/drain", json={"seconds": 3, "lease": LEASE}, headers=HEADERS)
        self.assertTrue(answer.get_json()["drained"], answer.get_json())
        self.assertLess(time.monotonic() - started, 2, "the drain waited for itself")


class OneDrainAtATime(Base):
    def test_a_second_drain_joins_the_first(self):
        writing, _wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        first, first_answer = self.in_thread(self.drain)
        wait_until(lambda: not self.lifecycle.status()["taking_work"])
        second, second_answer = self.in_thread(self.drain)
        # Once for its deadline, once more as it found the first under way and went on waiting for it.
        wait_until(lambda: self.reads.by("drain", second) >= 2, what="the second drain to wait for the first")
        self.assertTrue(second.is_alive(), "the second drain did not wait for the first")
        self.release.set()
        for thread in (writing, first, second):
            thread.join(10)
        self.assertEqual([True, True], [first_answer[0].get_json()["drained"], second_answer[0].get_json()["drained"]])
        self.assertEqual(["stop"], self.background.calls, "the background was stopped twice")

    def test_a_drain_taken_back_while_it_waits_says_so_at_once(self):
        writing, _wrote = self.in_thread(self.write)
        self.assertTrue(self.entered.wait(10))
        draining, answer = self.in_thread(lambda: self.drain(seconds=30))
        wait_until(lambda: not self.lifecycle.status()["taking_work"])
        started = time.monotonic()
        self.lifecycle.resume()      # the server's own: a lease ended
        draining.join(10)
        self.assertLess(time.monotonic() - started, 2, "the drain went on waiting after it was taken back")
        self.assertFalse(answer[0].get_json()["drained"])
        self.release.set()
        writing.join(10)


class AQuietMoment(Base):
    """The owner (2026-09-26): an update moves "at next available request" -- not in the
    middle of someone's session. A drain asked to wait for quiet is refused while a
    request came within that many seconds; the launcher stops asking for quiet once an
    update has waited an hour."""

    def drain_quietly(self, quiet):
        return self.client().post("/api/server/drain", json={"seconds": 5, "quiet": quiet, "lease": LEASE},
                                  headers=HEADERS)

    def test_is_refused_while_someone_is_using_the_app(self):
        self.lifecycle._clock = self.clock
        self.assertEqual(200, self.client().get("/harbour/api/tags").status_code)
        self.clock.now += 30
        answer = self.drain_quietly(120).get_json()
        self.assertFalse(answer["drained"])
        self.assertEqual(["a request 30s ago"], answer["waiting_for"])
        self.assertTrue(self.lifecycle.status()["taking_work"], "work was turned away")
        self.clock.now += 91
        self.assertTrue(self.drain_quietly(120).get_json()["drained"])

    def test_asking_for_none_takes_the_next_moment_with_nothing_in_flight(self):
        self.lifecycle._clock = self.clock
        self.client().get("/harbour/api/tags")
        self.assertTrue(self.drain_quietly(0).get_json()["drained"])

    def test_asking_how_the_server_is_is_not_a_use(self):
        self.lifecycle._clock = self.clock
        self.client().get("/api/server")
        self.assertTrue(self.drain_quietly(120).get_json()["drained"])


class OnlyTheLauncher(Base):
    def test_may_drain_the_server(self):
        for headers in ({}, {launcher.HEADER: "a guess"}):
            self.assertEqual(403, self.client().post("/api/server/drain", json={}, headers=headers).status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])

    def test_only_this_machine_may_drain(self):
        """Another machine is refused whatever it sends; over IPv6 loopback it is this one."""
        elsewhere = self.client().post("/api/server/drain", json={"seconds": 5, "lease": LEASE}, headers=HEADERS,
                                       environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(403, elsewhere.status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])
        here = self.client().post("/api/server/drain", json={"seconds": 5, "lease": LEASE}, headers=HEADERS,
                                  environ_base={"REMOTE_ADDR": "::1"})
        self.assertTrue(here.get_json()["drained"])

    def test_and_a_server_it_did_not_start_is_drained_by_nobody(self):
        app = web.create_app("tagpup", startup=self.library, runtime=Runtime())
        self.assertEqual(403, app.test_client().post("/api/server/drain", json={}, headers=HEADERS).status_code)
        self.assertEqual(403, app.test_client().post("/api/server/drain", json={},
                                                     headers={launcher.HEADER: ""}).status_code)

    def test_a_deadline_and_a_lease_must_be_numbers_of_seconds(self):
        for seconds in (0, -1, "60", True, lifecycles.MAX_DRAIN_SECONDS + 1):
            answer = self.client().post("/api/server/drain", json={"seconds": seconds, "lease": LEASE}, headers=HEADERS)
            self.assertEqual(400, answer.status_code, seconds)
        for lease in (0, -1, lifecycles.LEFT_DRAINED + 1, "20", True, None):     # None: a drain with no lease
            answer = self.client().post("/api/server/drain", json={"seconds": 5, "lease": lease}, headers=HEADERS)
            self.assertEqual(400, answer.status_code, lease)
        self.assertEqual(400, self.client().post("/api/server/drain", json={"seconds": 5}, headers=HEADERS).status_code)
        self.assertTrue(self.lifecycle.status()["taking_work"])


class WhichVersionAnswers(Base):
    def test_both_apps_say_it_with_or_without_a_library(self):
        tuner = web.create_app("tuner", startup=self.library, runtime=self.runtime, lifecycle=self.lifecycle)
        for app in (self.app, tuner):
            for url in ("/api/server", "/harbour/api/server"):
                status = app.test_client().get(url).get_json()
                self.assertEqual("20260926-101500-abc1234", status["version"])
                self.assertTrue(status["taking_work"])

    def test_each_response_names_the_version_answering_also_when_turned_away(self):
        for path in ("/api/server", "/harbour/api/tags", "/"):
            self.assertEqual(self.lifecycle.version, self.client().get(path).headers.get(launcher.VERSION_HEADER), path)
        self.assertTrue(self.drain().get_json()["drained"])
        refused = self.client().get("/harbour/api/tags")
        self.assertEqual(503, refused.status_code)
        self.assertEqual(self.lifecycle.version, refused.headers.get(launcher.VERSION_HEADER))

    def test_one_run_from_a_checkout_says_so(self):
        runtime = Runtime()
        client = web.create_app("tuner", runtime=runtime, lifecycle=Lifecycle(version=None, runtime=runtime)).test_client()
        self.assertEqual(launcher.FROM_A_CHECKOUT, client.get("/api/server").headers.get(launcher.VERSION_HEADER))

    def test_an_app_made_without_a_lifecycle_says_its_code_folders(self):
        from tagpup import config as tagpup_config
        app = web.create_app("tagpup", startup=self.library, runtime=Runtime())
        self.assertEqual(tagpup_config.code_version(), app.test_client().get("/api/server").get_json()["version"])


class WhatARealDrainRefusesBeside(unittest.TestCase):
    """#1450: each kind of work `lifecycle.under_way` lists refuses a real Lifecycle's drain naming it (the default `work`,
    not a test's list); a batch alone does not, it is stopped at its chunk. Changing `busy(but=(BATCH,))` in Lifecycle.drain
    to also leave out INDEX, NAMING, MIGRATING or SUGGEST fails it."""

    def drain_beside(self, source, value):
        self.addCleanup(self.lifecycle.resume)
        with mock.patch.object(source[0], source[1], return_value=value):
            return self.lifecycle.drain(5, 0, lease=LEASE)

    def setUp(self):
        self.home = own_home.for_test(self, prefix="drain_kinds_")
        self.background = FakeBackground()
        self.runtime = Runtime()
        self.lifecycle = Lifecycle(version="v", launcher_token=TOKEN, background=self.background, runtime=self.runtime)

    def test_an_index_a_naming_a_migration_and_a_suggest_run_each_refuse_it(self):
        class Busy:
            library = "harbour"

            def busy(self):
                return True
        every = (self.runtime.index_queues, "values")
        runs = (lifecycles.suggestion_jobs, "under_way")
        naming = (lifecycles.naming_faces_jobs, "running")
        migrating = (lifecycles.library_actions, "bringing_up_to_date")
        for kind, source, value in ((lifecycles.INDEX, every, [Busy()]),
                                    (lifecycles.SUGGEST, runs, [{"library": "harbour"}]),
                                    (lifecycles.NAMING, naming, 1), (lifecycles.MIGRATING, migrating, 1)):
            with self.subTest(kind):
                answer = self.drain_beside(source, value)
                self.assertFalse(answer["drained"], kind)
                self.assertTrue(answer["waiting_for"], kind)
                self.assertTrue(self.lifecycle.status()["taking_work"], kind)
                self.assertEqual([], self.background.calls, "%s: refused only after the server closed and stopped its jobs" % kind)

    def test_a_running_batch_alone_does_not(self):
        stopped = []

        def sentences():
            return [] if stopped else ["1 photo batch"]
        with mock.patch.object(lifecycles.batches, "sentences", sentences), \
                mock.patch.object(lifecycles.batches, "stop_all", lambda: stopped.append(1) or True):
            self.assertTrue(self.lifecycle.drain(5, 0, lease=LEASE)["drained"], "asked to stop, not waited for")
        self.assertEqual([1], stopped)
        self.lifecycle.resume()


class WhatALongRunIs(unittest.TestCase):
    """The drain's refusal reads the process's own runs: a Suggest run and an index."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="drain_runs_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)
        self.runtime = Runtime()
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.go = threading.Event()
        self.addCleanup(self.go.set)

    def test_an_index_under_way(self):
        def index(folder, report):
            self.go.wait(30)
            from tagpup.core.result import Result
            return Result()
        self.assertEqual([], lifecycles.long_work(self.runtime.index_queues))
        self.runtime.index_queues.of(self.library).start([self.home.root], index)
        self.assertEqual(["indexing in 1 library(ies)"], [work.sentence for work in lifecycles.long_work(self.runtime.index_queues)])
        self.go.set()
        wait_until(lambda: not lifecycles.long_work(self.runtime.index_queues), what="no long work")

    def test_a_suggest_run_under_way(self):
        go = self.go

        class Work:
            def photos(self):
                go.wait(30)
                return {}

            def begin(self):
                raise AssertionError("no photos, no model")
        suggestion_jobs.runs_for(self.library).start(self.home.root, Work())
        self.assertEqual(["1 Suggest run(s)"], [work.sentence for work in lifecycles.long_work(self.runtime.index_queues)])
        self.go.set()
        wait_until(lambda: not lifecycles.long_work(self.runtime.index_queues), what="no long work")


class AnIndexsEndWaitingForTheLibrary(unittest.TestCase):
    """#2200: an index ending while another writes the library leaves its end to a thread that waits for it, after the
    queue is idle; a drain that did not ask for it let the launcher stop the server, the end unwritten. A writer that
    never lets go holds the drain only as long as the thread waits (db's busy retries), then the end waits for the
    library's next index, as before."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="drain_end_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)
        self.runtime = Runtime()
        self.lifecycle = Lifecycle(version="v", launcher_token=TOKEN, background=FakeBackground(), runtime=self.runtime)
        self.addCleanup(self.lifecycle.resume)
        self.held = []
        self.addCleanup(self.let_go)

    def let_go(self):
        while self.held:
            conn = self.held.pop()
            conn.rollback()
            conn.close()

    def index_ending_beside_another_writer(self):
        """An index of one folder, the library written by another (a connection of its own, as a CLI's would be) from
        its last moment on: once the queue is idle, the run, its end not yet written."""
        def index(folders, report):
            conn = db.connect(self.library.path, check_same_thread=False)
            db.begin(conn, immediate=True)
            self.held.append(conn)
            return Result(attempted=1, changed=1)
        queue = self.runtime.index_queues.of(self.library)
        self.assertTrue(queue.start([self.home.root], index, together=True).ok)
        queue.wait()
        [run] = [run for run in runs_service.runs(self.library, runs_service.INDEX)]
        self.assertEqual(job_runs.RUNNING, run.outcome)
        return run

    def wait_for_no_long_work(self, seconds):
        wait_until(lambda: not lifecycles.long_work(self.runtime.index_queues), seconds, 0.02, "no long work")

    def test_refuses_a_drain_until_it_is_written(self):
        run = self.index_ending_beside_another_writer()
        answer = self.lifecycle.drain(5, 0, lease=LEASE)
        self.assertEqual((False, ["indexing in 1 library(ies)"]), (answer["drained"], answer.get("waiting_for")), answer)
        self.assertEqual([work for work in lifecycles.under_way(queues=self.runtime.index_queues) if work.library == "harbour"],
                         [lifecycles.Work(lifecycles.INDEX, "harbour", "an index's end waits for the library")])
        self.let_go()
        self.wait_for_no_long_work(30)
        self.assertEqual((job_runs.DONE, 1), (runs_service.get(self.library, run.id).outcome,
                                              runs_service.get(self.library, run.id).changed["changed"]))
        self.assertEqual({"drained": True}, self.lifecycle.drain(5, 0, lease=LEASE))

    def test_a_writer_that_never_lets_go_holds_the_drain_only_while_the_end_is_waited_for(self):
        with mock.patch.object(db, "BUSY_TIMEOUT_MS", 200):
            run = self.index_ending_beside_another_writer()
            self.assertTrue(lifecycles.long_work(self.runtime.index_queues))
            self.wait_for_no_long_work(30)
            self.assertEqual({"drained": True}, self.lifecycle.drain(5, 0, lease=LEASE),
                             "a writer that never let go held the drain for good")
        self.lifecycle.resume()
        self.assertEqual(job_runs.RUNNING, runs_service.get(self.library, run.id).outcome)
        # Its end is kept, written as the library's next index begins.
        self.let_go()
        queue = self.runtime.index_queues.of(self.library)
        self.assertTrue(queue.start([self.home.root], lambda folders, report: Result(), together=True).ok)
        queue.wait()
        self.assertEqual(job_runs.DONE, runs_service.get(self.library, run.id).outcome)


class StuckTask:
    """A background task whose first stop does not return until it is let go: on Windows, a folder watch on a share
    that stopped answering can block in watchdog's observer.stop() itself, which no timeout bounds. Stopped again, it
    has nothing left to stop (the watcher's observer is gone) and returns at once."""

    def __init__(self):
        self.let_go = threading.Event()
        self.stops = self.starts = 0
        self.working = False
        self.busy_once_stopped = False

    def start(self):
        self.starts += 1

    def stop(self, timeout=30):
        self.stops += 1
        if self.stops == 1:
            self.let_go.wait()
        return True

    def busy(self):
        return self.working


class QuickTask(StuckTask):
    def stop(self, timeout=30):
        self.stops += 1
        self.working = self.busy_once_stopped
        return True


class AnUpdateWhileAShareIsAway(Base):
    """2026-11-19: an install's drain logged "Draining for an update" and then neither "Drained" nor "Could not drain";
    every later drain answered "another drain" for 22 minutes, until the owner had the old server ended by hand,
    `GET /api/server` reading taking_work false, no request, nothing busy all the while. A background task whose stop
    never returns held the drain past its deadline (runtime.Background.stop waited on it) with `_draining` set. With
    nothing in flight and nothing busy, a stop that has not returned holds nothing an update must wait for: the drain
    answers drained at its deadline and the server stays closed for the launcher to end. On the code before, the first
    drain was still running 5 s after its 1 s deadline; on the first fix, it took work again and started a new watch
    (#3174)."""

    def setUp(self):
        super().setUp()
        self.stuck, self.quick = StuckTask(), QuickTask()
        self.addCleanup(self.stuck.let_go.set)
        self.lifecycle = Lifecycle(version="v", launcher_token=TOKEN, runtime=self.runtime,
                                   background=runtimes.Background([(runtimes.FOLDER_WATCHER, self.stuck),
                                                                   (runtimes.RECURRING_JOBS, self.quick)]))
        self.addCleanup(self.lifecycle.resume)

    def test_a_stop_that_does_not_return_with_nothing_busy_ends_the_drain_drained_and_closed(self):
        started = time.monotonic()
        with self.assertLogs(lifecycles.logger, "WARNING") as logged:
            first, first_answer = self.in_thread(lambda: self.lifecycle.drain(1, lease=LEASE))
            wait_until(lambda: self.stuck.stops == 1)
            # Given longer than the first, so a loaded machine's first does not outlast it ("another drain").
            second, second_answer = self.in_thread(lambda: self.lifecycle.drain(10, lease=LEASE))
            first.join(5)
            second.join(5)
            self.assertFalse(first.is_alive(),
                             "the drain did not end at its deadline: a stop that never returned held it")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual({"drained": True}, first_answer[0])
        self.assertIn("folder watcher (still stopping)", "\n".join(logged.output))
        # The second, beside it, ended with the first and says how it ended.
        self.assertFalse(second.is_alive())
        self.assertEqual({"drained": True}, second_answer[0])
        self.assertFalse(self.lifecycle.status()["taking_work"], "taken back: a new watch started beside the hung one")
        self.assertEqual((0, 0), (self.stuck.starts, self.quick.starts), "the background was started again")
        # A launcher's next drain finds it drained at once.
        started = time.monotonic()
        self.assertEqual({"drained": True}, self.lifecycle.drain(1, lease=LEASE))
        self.assertLess(time.monotonic() - started, 1)

    def test_a_stop_that_does_not_return_beside_a_busy_task_takes_work_again(self):
        self.quick.busy_once_stopped = True
        with self.assertLogs(lifecycles.logger, "WARNING") as logged:
            answer = self.lifecycle.drain(1, lease=LEASE)
        self.assertEqual({"drained": False, "waiting_for": [runtimes.RECURRING_JOBS]}, answer)
        self.assertIn("Could not drain in 1s (waiting for recurring jobs, folder watcher (still stopping)); taking work "
                      "again.", "\n".join(logged.output))
        self.assertTrue(self.lifecycle.status()["taking_work"], "left closed and not drained")
        self.assertEqual((1, 1), (self.stuck.starts, self.quick.starts), "the background was not started again")

    def test_stops_that_return_at_once_drain_it_and_leave_no_thread_running(self):
        self.stuck.let_go.set()
        self.assertEqual({"drained": True}, self.lifecycle.drain(1, lease=LEASE))
        self.assertEqual((1, 1), (self.stuck.stops, self.quick.stops))
        self.assertEqual([], [thread.name for thread in threading.enumerate()
                              if thread.name.startswith(runtimes.STOPPING_THREAD)])


class AnInstallWhileAWatchedShareStopsAnswering(Base):
    """The task whose stop never returned in the 22-minute hang (2026-11-19; AnUpdateWhileAShareIsAway, #3345). Of the
    server's three background tasks two only join their own thread for the time they are given (the recurring jobs'
    runner, the idle caches' timer); the folder watcher's stop called watchdog's observer.stop(), which no timeout
    bounds: under the observer's lock it stops each watch (its read of the folder cancelled, its handle closed) and
    joins the watch's thread with no timeout, so a read of a share that does not answer, which its cancel does not
    end, holds it for good. Here the real watcher on its real watchdog observer, its watches' reads held as such a
    share holds them, and an install's hand-over (launcher.make_way, through the status and drain routes): the old server
    drains and is ended, and the install goes on, with IDLE_PATIENCE out of reach -- the deadline is not what ends it.
    On the code of the hang (184118b9) the hand-over had not ended 10 s later."""

    OLD, NEW = "20261119-090000-aaaaaaa", "20261119-100000-bbbbbbb"

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.home.root, "Photos")
        os.makedirs(os.path.join(self.root, "Regatta"))
        self.away, self.let_go, self.held = threading.Event(), threading.Event(), []
        self.watcher = watching.Watcher(lambda: [self.library], lambda library: [self.root],
                                        lambda library, folder: None, images.is_photo,
                                        debounce=0.5, recheck=0.2, tick=0.05, observer=self.observer)
        self.addCleanup(self.watcher.stop, 10)
        self.lifecycle = Lifecycle(version=self.OLD, launcher_token=TOKEN, runtime=self.runtime,
                                   background=runtimes.Background([(runtimes.FOLDER_WATCHER, self.watcher)]))
        self.app = web.create_app("tagpup", startup=self.library, runtime=self.runtime, lifecycle=self.lifecycle)
        self.app.testing = True
        self.addCleanup(self.lifecycle.resume)
        # Run first (last in, first out): the share answers again, and the held reads end.
        self.addCleanup(self.let_go.set)
        self.addCleanup(self.away.clear)

    def observer(self):
        """watchdog's own observer, its watches' reads held while the share is away: a held read returns only when the
        share answers again (let_go), whatever its stop does."""
        observer = watching.make_observer()
        away, let_go, held = self.away, self.let_go, self.held

        class AReadOfAShareThatDoesNotAnswer(observer._emitter_class):
            def queue_events(self, timeout):
                if away.is_set():
                    held.append(self)
                    let_go.wait()
                    return
                super().queue_events(timeout)
        observer._emitter_class = AReadOfAShareThatDoesNotAnswer
        return observer

    def test_the_install_drains_and_ends_the_old_server_without_waiting_out_the_idle_patience(self):
        self.away.set()
        self.lifecycle.background.start()
        wait_until(lambda: self.watcher.watched() == [paths.stored(self.root)] and len(self.held) == 2)
        found = {"pid": 4242, "started": 1, "ports": {"tagpup": 5}, "version": self.OLD, "token": TOKEN}
        said, ended, port_held, now = [], [], [True], [0.0]
        client = self.client()

        def ask(port, timeout=None):
            return client.get("/api/server").get_json()

        def drain(port, token, quiet=launcher.QUIET, seconds=None):
            # The launcher's body, with a deadline of 1 s, not its 20.
            return client.post("/api/server/drain", json={"seconds": 1, "quiet": quiet, "lease": launcher.LEASE},
                               headers={launcher.HEADER: token}).get_json()

        def end(theirs, port, answering, sleep=None):
            ended.append(theirs["pid"])
            port_held[0] = False
            return True

        def sleep(seconds):
            now[0] += seconds
        with mock.patch.multiple(launcher, ask=ask, drain=drain, end=end, record=lambda port: found,
                                 IDLE_PATIENCE=float("inf")):
            way, answer = self.in_thread(lambda: launcher.make_way(
                5, self.NEW, say=said.append, open_page=lambda: None, answering=lambda port: port_held[0],
                sleep=sleep, clock=lambda: now[0], page=False))
            way.join(10)
            self.assertFalse(way.is_alive(), "the hand-over did not end: the folder watcher's stop held the drain")
        self.assertEqual([(launcher.SERVE, False)], answer)
        self.assertEqual([4242], ended)
        self.assertIn("TagPup %s finished what it was doing; ending it and starting %s." % (self.OLD, self.NEW), said)
        self.assertEqual([], [line for line in said if "would not let go" in line])
        # The reads (the folder's watch and its parent's) were never ended: the server was ended around them.
        self.assertEqual([True, True], [read.is_alive() for read in self.held])


if __name__ == "__main__":
    unittest.main()

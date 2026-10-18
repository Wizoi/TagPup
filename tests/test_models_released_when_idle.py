"""V6: the server goes idle, and the models, the photo vectors, the pages' caches and the graphics card are let go;
the next ask makes them again; a run in use keeps them; the Activity page's Unload button lets them go sooner.

Owner, 2026-09-26 ("can the in-memory cache be minimized when not in use?") and 2026-10-05 (ViT-H-14 and the face models
kept on the card for 30 minutes made Lightroom crawl): the server lets go after five idle minutes, noticed
within one, and the button at once, never under a running Suggest. On photo_index the vectors are about 270 MB and New
Person's pool 185 MB. The models are stand-ins that take the card's turn as the real ones do; nothing loads
(tests/test_no_test_loads_a_model.py). The clock is the test's. Names are fictional.

Lifted: test_page_caches_released_when_idle (4), test_vectors_and_pool_released_when_idle (6), test_unload_models_now
(14, apps-owned), test_web_beside_its_requests (3) and this file's own five. Mutation, each run in a scratch copy and
each failing the test named: Runtime.release_idle returning [] (idle, run-in-use, Identify); _release_models without its
_unload, or without release_if_idle so the card is kept (idle, Unload); _release_indexes without _retire (idle); the
folder scans not registered (idle, run-in-use) or their in_use ignoring a running Suggest (run-in-use); the models
registered without in_use, or IdleCaches.release_now ignoring it (Unload under a run); a run's end not a use of the
models (run-in-use); the "release idle caches" background task off (wiring); the pool or the Identify caches not
registered, or Identify without its in_use (Identify).
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import web_client  # noqa: E402

import tagpup_web  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.web import tagpup_routes, tuner_routes  # noqa: E402

IDLE = 5 * 60
ELSEWHERE = {"REMOTE_ADDR": "192.168.1.20"}
BOTH = ["CLIP", "faces"]
A_RUN = mock.patch("tagpup.services.suggester.model_for_run", lambda *args, **kwargs: object())


class FakeModel:
    """A model that takes the card's turn when it loads, as ClipModel and FaceModel do."""

    def __init__(self, settings, device="cuda"):
        self.settings, self.device = dict(settings), device
        self.gpu, self.on, self.unloads = None, False, 0

    def use(self):
        if gpu.on_the_card(self.device):
            self.gpu.ensure("loading a fake model")
        self.on = True

    def loaded(self):
        return self.on

    def unload(self):
        self.unloads += self.on
        self.on = False


class Clock:
    now = 5000.0

    def __call__(self):
        return self.now


class TheServer(unittest.TestCase):
    kind, device = "tagpup", "cuda"

    def setUp(self):
        self.clock = Clock()
        self.where = tempfile.mkdtemp(prefix="tagpup_gpu_")
        self.card = gpu.Card(self.where, poll=0.05)
        self.addCleanup(shutil.rmtree, self.where, True)
        self.addCleanup(self.card.close)
        self.built = []
        self.runtime = Runtime(build_clip=self.make, build_faces=self.make, idle_after=IDLE, clock=self.clock,
                               card=self.card, keep_models=True)
        self.app, self.home = web_client.app_for(self, self.kind, runtime=self.runtime)
        self.harbour = Library(self.home.library("library.db"))
        self.client = self.app.test_client()
        self.addCleanup(self.runtime.forget, self.harbour)

    def make(self, settings):
        self.built.append(FakeModel(settings, self.device))
        return self.built[-1]

    def load_both(self):
        self.runtime.clip(self.harbour).use()
        self.runtime.faces(self.harbour).use()

    def later(self, seconds):
        self.clock.now += seconds
        return self.runtime.release_idle()


class TheStartupWarmUp(TheServer):
    def test_while_it_loads_the_page_names_it_and_not_a_suggest(self):
        """The warm-up holds a run's lease while it loads (#1423). The Activity page read "a run holds them" as Suggest: it
        said "In use by Suggest." and Unload answered "Suggest is using them" with no Suggest anywhere. Now each names
        what holds them."""
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow_load(model):
            model.on = True
            started.set()
            release.wait(20)
        with mock.patch.object(FakeModel, "load", slow_load, create=True), \
                mock.patch.object(FakeModel, "embed_text", lambda model, text: [0.0], create=True):
            warming = self.runtime.warm_up_in_background([self.harbour])
            self.assertTrue(started.wait(20))
            text = self.client.get("/api/activity/server").get_json()["models"]["text"]
            self.assertIn("In use by the warm-up of Suggest's models (TagPup server).", text)
            self.assertNotIn("Suggest.", text)
            busy = self.client.post("/api/activity/models/unload")
            self.assertEqual(409, busy.status_code)
            self.assertEqual("In use by the warm-up of Suggest's models (TagPup server): they are let go when it ends.",
                             busy.get_json()["error"])
            release.set()
            warming.join(20)
        self.assertNotIn("In use", self.client.get("/api/activity/server").get_json()["models"]["text"])


class TheServerGoesIdle(TheServer):
    def setUp(self):
        super().setUp()
        self.addCleanup(tagpup_routes.folders.forget, self.harbour)
        self.folder = os.path.join(self.home.root, "Regatta")
        os.makedirs(self.folder)

    def scan(self):
        answer = self.client.get("/library/api/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, answer.status_code, answer.get_data(as_text=True))
        return tagpup_routes.folders.held(self.harbour.key)

    def ask_for_everything(self):
        self.load_both()
        index = self.runtime.photo_index(self.harbour)
        self.assertIsNotNone(self.scan())
        return index

    def test_everything_is_let_go_after_the_period_and_the_next_ask_makes_it_again(self):
        index = self.ask_for_everything()
        clip = self.runtime.clip(self.harbour)
        self.assertTrue(self.card.holds())
        self.assertEqual([], self.later(IDLE - 10), "let go before the idle period was over")
        self.ask_for_everything()                                  # each ask starts the period again
        self.assertEqual([], self.later(IDLE - 10))
        self.assertEqual({"models", "photo indexes", "folder scans"}, set(self.later(11)))
        self.assertFalse(self.card.holds(), "the models went and the turn was kept")
        self.assertEqual(1, clip.unloads)
        self.assertIsNone(index.conn, "the index let go still holds its library open")
        self.assertIsNone(tagpup_routes.folders.held(self.harbour.key), "the scans are still held")
        self.assertEqual([], self.later(IDLE * 2), "released again with nothing made since")
        self.assertIsNot(self.runtime.clip(self.harbour), clip, "the next ask got the model that was let go")
        self.assertIsNotNone(self.runtime.photo_index(self.harbour).conn)
        self.assertIsNotNone(self.scan().get(self.folder))

    def test_what_a_run_holds_is_kept_however_long_it_runs(self):
        index = self.ask_for_everything()
        with A_RUN:
            run = self.runtime.begin(self.harbour)
        with mock.patch.object(suggestion_jobs, "running", return_value=1):
            self.assertEqual([], self.later(IDLE * 3), "let go under a running Suggest")
        self.assertIsNotNone(index.conn)
        run.end()
        self.assertEqual(["folder scans"], self.later(0), "the run's end is a use of the models, not of the scans")
        self.assertEqual({"models", "photo indexes"}, set(self.later(IDLE + 1)))

    def test_a_runtime_without_an_idle_period_keeps_them(self):
        runtime = Runtime(build_clip=self.make, build_faces=self.make, clock=self.clock)
        runtime.clip(self.harbour)
        self.clock.now += IDLE * 100
        self.assertEqual([], runtime.release_idle())
        runtime.forget(self.harbour)


class TheUnloadButton(TheServer):
    def test_lets_the_models_go_and_the_card_with_them_but_never_under_a_run(self):
        server = lambda: self.client.get("/api/activity/server").get_json()["models"]  # noqa: E731
        press = lambda **kw: self.client.post("/api/activity/models/unload", **kw)  # noqa: E731
        self.assertEqual([], server()["loaded"])
        self.assertIn("Not loaded; loads when Suggest or indexing needs it", server()["text"])
        self.load_both()
        self.clock.now += 120
        self.assertEqual((BOTH, 5), (server()["loaded"], server()["release_after_minutes"]))
        self.assertIn("Last used 2 minutes ago", server()["text"])
        self.assertEqual(403, press(environ_base=ELSEWHERE).status_code)
        self.assertEqual(BOTH, self.runtime.models_state()["loaded"], "another machine unloaded them")
        with A_RUN:
            run = self.runtime.begin(self.harbour)
        busy = press()
        self.assertEqual(409, busy.status_code)
        self.assertEqual("In use by Suggest in library: they are let go when it ends.", busy.get_json()["error"])
        self.assertIn("In use by Suggest in library.", server()["text"])
        self.assertEqual((BOTH, True), (self.runtime.models_state()["loaded"], self.runtime.models_state()["in_use"]))
        run.end()
        done = press().get_json()
        self.assertEqual(BOTH, done["unloaded"])
        self.assertEqual("Not loaded; loads when Suggest or indexing needs it.", done["models"]["text"])
        self.assertEqual([1, 1], [m.unloads for m in self.built])
        self.assertFalse(self.card.holds())
        self.assertEqual("Nothing was loaded.", press().get_json()["message"], "pressed twice")
        self.load_both()                                           # a Suggest straight after loads them again
        self.assertEqual((BOTH, True), (self.runtime.models_state()["loaded"], self.card.holds()))

    def test_a_server_with_no_runtime_says_so(self):
        app, _home = web_client.app_for(self, "tagpup")
        reply = app.test_client().post("/api/activity/models/unload")
        self.assertEqual(409, reply.status_code)


class OnTheCpu(TheServer):
    device = "cpu"

    def test_a_cpu_only_machine_unloads_and_never_held_the_card(self):
        self.load_both()
        self.assertFalse(self.card.holds())
        self.assertEqual(BOTH, self.runtime.unload_models_now()["unloaded"])


class TheTunersCaches(TheServer):
    kind = "tuner"

    def setUp(self):
        super().setUp()
        self.addCleanup(tuner_routes.identify_cache.forget, self.harbour)
        self.addCleanup(tuner_routes.identify_progress.forget, self.harbour)

    def ask(self, route):
        answer = self.client.get("/library/api/" + route)
        self.assertEqual(200, answer.status_code, answer.get_data(as_text=True))

    def pool_is_held(self):
        return "unnamed_faces" in (tuner_routes.identify_cache.held(self.harbour.key) or {}).keys()

    def test_the_queue_and_new_persons_pool_are_let_go_never_under_a_grid_being_built(self):
        def unnamed_like(library, face_id, unnamed=None, *, named):
            unnamed()
            return {"matches": []}
        with mock.patch.object(tuner_routes.identify_service, "unnamed_like", side_effect=unnamed_like):
            self.ask("unmatched-faces/people")
            self.ask("face-matches-unmatched?id=1")
            self.assertTrue(self.pool_is_held())
            self.assertEqual([], self.later(IDLE - 1))
            progress = tuner_routes.identify_progress.of(self.harbour)
            progress.report("Rowan Thackeray", "reading", 0.5, "Reading faces...")
            self.assertEqual(["New Person pool"], self.later(2))
            self.assertFalse(self.pool_is_held(), "the pool is still held")
            self.assertIsNotNone(tuner_routes.identify_cache.held(self.harbour.key), "let go under a grid")
            progress.done("Rowan Thackeray")
            self.assertEqual(["identify caches"], self.later(0))
            self.assertIsNone(tuner_routes.identify_cache.held(self.harbour.key))
            self.ask("unmatched-faces/people")
            self.ask("face-matches-unmatched?id=1")
            self.assertTrue(self.pool_is_held(), "the pool was not read again")


class TheWebServerRunsIt(unittest.TestCase):
    def setUp(self):
        own_home.for_test(self, prefix="web_background_")
        for patch in (mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"}),
                      mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")):
            patch.start()
            self.addCleanup(patch.stop)
        self.args = ["--tagpup-port", "1", "--tuner-port", "2"]

    def test_the_release_is_a_background_task_looking_at_least_once_a_minute_given_a_period(self):
        task = runtimes.BACKGROUND["release idle caches"](Runtime(idle_after=IDLE))
        self.assertLessEqual(task._seconds, 60)
        self.assertNotIn("release idle caches", runtimes.background(Runtime()).names())

    def test_it_runs_the_background_tasks_beside_its_requests_and_stops_them(self):
        log = []

        class Task:
            start = lambda self: log.append("start")  # noqa: E731
            stop = lambda self, timeout=30: log.append("stop") or True  # noqa: E731
            busy = lambda self: False  # noqa: E731
        with mock.patch.object(tagpup_web.web, "serve", side_effect=lambda *a, **k: log.append("serve")), \
                mock.patch.dict(runtimes.BACKGROUND, {"watcher": lambda runtime: Task()}, clear=True):
            tagpup_web.main(self.args)
        self.assertEqual(["start", "serve", "stop"], log)

    def test_a_default_start_loads_no_model_and_lets_them_go_after_five_minutes_unless_told_otherwise(self):
        # The owner's log: models on the card 15 s after a start, nobody asking (2026-10-05).
        warmed, made = [], []
        with mock.patch.dict(os.environ), mock.patch.object(tagpup_web.web, "serve"), \
                mock.patch.object(Runtime, "warm_up_in_background", side_effect=warmed.append), \
                mock.patch.object(tagpup_web, "Runtime", side_effect=lambda **kw: made.append(kw) or Runtime(**kw)):
            os.environ.pop("TAGPUP_WEB_NO_WARMUP", None)
            tagpup_web.main(self.args)
            self.assertEqual([], warmed, "a default start warmed the models")
            tagpup_web.main(self.args + ["--warm-up", "--release-models-after", "20"])
            tagpup_web.main(self.args + ["--release-models-after", "0"])
        self.assertEqual(1, len(warmed))
        self.assertEqual([5 * 60, 20 * 60, None], [kw["idle_after"] for kw in made])
        self.assertEqual([True, True, True], [kw.get("keep_models") for kw in made], "kept between runs (#774)")


if __name__ == "__main__":
    unittest.main()

"""Let the models go sooner, and the owner's Unload models now (Activity page).

A server that kept ViT-H-14 and the face models on the graphics card for 30 minutes made
Lightroom crawl (owner, 2026-10-05). They are let go after five idle minutes, noticed within
a minute; the Activity page's button lets them go at once -- and the card with them -- but
never under a running Suggest. Fake models only; a test's own home and card folder.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

ELSEWHERE = {"REMOTE_ADDR": "192.168.1.20"}
PORTS = {"tagpup": 8090, "tuner": 8080}


class FakeModel:
    """A model that takes the card's turn when it loads, as ClipModel and FaceModel do."""

    def __init__(self, device="cuda"):
        self.device = device
        self.gpu = None
        self.on = False
        self.unloads = 0

    def use(self):
        if gpu.on_the_card(self.device):
            self.gpu.ensure("loading a fake model")
        self.on = True

    def loaded(self):
        return self.on

    def unload(self):
        if self.on:
            self.unloads += 1
        self.on = False


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    device = "cuda"

    def setUp(self):
        self.home = own_home.for_test(self, prefix="unload_now_")
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.harbour = Library(path)
        self.where = tempfile.mkdtemp(prefix="tagpup_gpu_")
        self.card = gpu.Card(self.where, poll=0.05)
        self.addCleanup(shutil.rmtree, self.where, True)
        self.addCleanup(self.card.close)
        self.clock = Clock()
        self.built = {"clip": [], "faces": []}
        self.runtime = Runtime(build_clip=lambda s: self.make("clip"), build_faces=lambda s: self.make("faces"),
                               idle_after=5 * 60, clock=self.clock, card=self.card, keep_models=True)
        self.addCleanup(self.runtime.forget, self.harbour)

    def make(self, kind):
        model = FakeModel(self.device)
        self.built[kind].append(model)
        return model

    def load_both(self):
        self.runtime.clip(self.harbour).use()
        self.runtime.faces(self.harbour).use()


class FiveMinutes(Base):
    def test_the_default_is_five_minutes(self):
        self.assertEqual(5, runtimes.RELEASE_MODELS_AFTER_MINUTES)

    def test_the_release_thread_looks_at_least_once_a_minute(self):
        task = runtimes.BACKGROUND["release idle caches"](self.runtime)
        self.assertLessEqual(task._seconds, 60)

    def test_an_idle_release_gives_up_the_card_too(self):
        self.load_both()
        self.assertTrue(self.card.holds())
        self.clock.now += 5 * 60 + 1
        self.assertEqual(["models"], self.runtime.release_idle())
        self.assertFalse(self.card.holds(), "the models went and the turn was kept")


class UnloadNow(Base):
    def test_what_is_loaded_and_since_when(self):
        self.assertEqual([], self.runtime.models_state()["loaded"])
        self.load_both()
        self.clock.now += 90
        state = self.runtime.models_state()
        self.assertEqual(["CLIP", "faces"], state["loaded"])
        self.assertEqual(90, state["last_used"])
        self.assertEqual(300, state["release_after"])

    def test_it_unloads_every_model_and_gives_up_the_card_at_once(self):
        self.load_both()
        done = self.runtime.unload_models_now()
        self.assertEqual({"unloaded": ["CLIP", "faces"], "busy": False}, done)
        self.assertEqual([1], [m.unloads for m in self.built["clip"]])
        self.assertEqual([1], [m.unloads for m in self.built["faces"]])
        self.assertFalse(self.card.holds())
        self.assertEqual([], self.runtime.models_state()["loaded"])

    def test_pressed_twice_the_second_finds_nothing(self):
        self.load_both()
        self.runtime.unload_models_now()
        self.assertEqual({"unloaded": [], "busy": False}, self.runtime.unload_models_now())
        self.assertEqual([1], [m.unloads for m in self.built["clip"]])

    def test_a_suggest_that_starts_straight_after_loads_them_again(self):
        self.load_both()
        self.runtime.unload_models_now()
        self.load_both()
        self.assertEqual(["CLIP", "faces"], self.runtime.models_state()["loaded"])
        self.assertTrue(self.card.holds())

    def test_never_under_a_running_suggest(self):
        with mock.patch("tagpup.services.suggester.model_for_run", lambda *args: object()):
            run = self.runtime.begin(self.harbour)
        self.load_both()
        done = self.runtime.unload_models_now()
        self.assertEqual({"unloaded": [], "busy": True}, done)
        self.assertEqual([0], [m.unloads for m in self.built["clip"]])
        self.assertEqual(["CLIP", "faces"], self.runtime.models_state()["loaded"])
        self.assertTrue(self.runtime.models_state()["in_use"])
        run.end()
        self.assertFalse(self.runtime.unload_models_now()["busy"])
        self.assertEqual([1], [m.unloads for m in self.built["clip"]])


class OnTheCpu(Base):
    device = "cpu"

    def test_a_cpu_only_machine_unloads_and_never_held_the_card(self):
        self.load_both()
        self.assertFalse(self.card.holds())
        self.assertEqual(["CLIP", "faces"], self.runtime.unload_models_now()["unloaded"])
        self.assertEqual([1], [m.unloads for m in self.built["clip"]])


class TheRoute(Base):
    def client(self, runtime="own"):
        app = web.create_app("tagpup", ports=PORTS, lifecycle=Lifecycle(),
                             runtime=self.runtime if runtime == "own" else runtime)
        app.testing = True
        return app.test_client()

    def test_from_another_machine_it_is_refused_and_nothing_unloaded(self):
        self.load_both()
        reply = self.client().post("/api/activity/models/unload", environ_base=ELSEWHERE)
        self.assertEqual(403, reply.status_code)
        self.assertEqual(["CLIP", "faces"], self.runtime.models_state()["loaded"])

    def test_it_unloads_and_says_what(self):
        self.load_both()
        reply = self.client().post("/api/activity/models/unload")
        body = reply.get_json()
        self.assertEqual(200, reply.status_code)
        self.assertEqual(["CLIP", "faces"], body["unloaded"])
        self.assertEqual("Not loaded; loads when Suggest or indexing needs it.", body["models"]["text"])
        again = self.client().post("/api/activity/models/unload").get_json()
        self.assertEqual("Nothing was loaded.", again["message"])

    def test_under_a_running_suggest_it_answers_a_sentence_and_does_nothing(self):
        with mock.patch("tagpup.services.suggester.model_for_run", lambda *args: object()):
            run = self.runtime.begin(self.harbour)
        self.addCleanup(run.end)
        self.load_both()
        reply = self.client().post("/api/activity/models/unload")
        self.assertEqual(409, reply.status_code)
        self.assertEqual("Suggest is using them: they are let go when it ends.", reply.get_json()["error"])
        self.assertEqual([0], [m.unloads for m in self.built["clip"]])

    def test_the_server_section_says_what_is_loaded(self):
        client = self.client()
        text = client.get("/api/activity/server").get_json()["models"]
        self.assertEqual([], text["loaded"])
        self.assertIn("Not loaded; loads when Suggest or indexing needs it", text["text"])
        self.load_both()
        self.clock.now += 120
        models = client.get("/api/activity/server").get_json()["models"]
        self.assertEqual(["CLIP", "faces"], models["loaded"])
        self.assertIn("Last used 2 minutes ago", models["text"])
        self.assertEqual(5, models["release_after_minutes"])

    def test_a_server_with_no_runtime_says_so(self):
        reply = self.client(runtime=None).post("/api/activity/models/unload")
        self.assertEqual(409, reply.status_code)
        self.assertIsNone(self.client(runtime=None).get("/api/activity/server").get_json()["models"])


if __name__ == "__main__":
    unittest.main()

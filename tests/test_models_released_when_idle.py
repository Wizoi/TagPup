"""The always-on process lets its models go after an idle period, and the next Suggest
loads them again (docs/ARCHITECTURE.md, phase 8, "Always on, from login"): ViT-H-14
keeps a few GB of GPU memory, and a process running all day would hold it all day for a
Suggest that may not come. A model a run holds is never let go under it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402

IDLE = 30 * 60


class FakeModel:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


class IdleModels(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="idle_models_")
        path = home.library("harbour.db")
        library_actions.create(path)
        self.harbour = Library(path)
        self.built = []
        self.clock = Clock()
        build = self.build
        self.runtime = Runtime(build_clip=build, build_faces=build, idle_after=IDLE, clock=self.clock)
        self.addCleanup(self.runtime.forget, self.harbour)

    def build(self, settings):
        model = FakeModel(settings)
        self.built.append(model)
        return model

    def test_are_let_go_after_the_idle_period_and_loaded_again_by_the_next_ask(self):
        clip, faces = self.runtime.clip(self.harbour), self.runtime.faces(self.harbour)
        self.clock.now += IDLE - 1
        self.assertEqual([], self.runtime.release_idle(), "let go before the idle period was over")
        self.assertEqual((0, 0), (clip.unloaded, faces.unloaded))
        self.clock.now += 2
        self.assertEqual(["models"], self.runtime.release_idle())
        self.assertEqual((1, 1), (clip.unloaded, faces.unloaded))
        again = self.runtime.clip(self.harbour)
        self.assertIsNot(again, clip, "the next ask got the model that was let go")
        self.assertEqual(3, len(self.built))

    def test_each_ask_starts_the_period_again(self):
        self.runtime.clip(self.harbour)
        self.clock.now += IDLE - 10
        self.runtime.clip(self.harbour)
        self.clock.now += IDLE - 10
        self.assertEqual([], self.runtime.release_idle())

    def test_a_model_a_run_holds_is_kept_however_long_it_runs(self):
        with mock.patch("tagpup.services.suggester.model_for_run", lambda *args: object()):
            run = self.runtime.begin(self.harbour)
        self.clock.now += IDLE * 3
        self.assertNotIn("models", self.runtime.release_idle(), "let go under a running Suggest")
        run.end()
        self.assertEqual([], self.runtime.release_idle(), "the run's end is a use")
        self.clock.now += IDLE + 1
        self.assertIn("models", self.runtime.release_idle())

    def test_a_runtime_without_an_idle_period_keeps_them(self):
        runtime = Runtime(build_clip=self.build, build_faces=self.build, clock=self.clock)
        runtime.clip(self.harbour)
        self.clock.now += IDLE * 100
        self.assertEqual([], runtime.release_idle())
        runtime.forget(self.harbour)


class TheWebServerLetsThemGo(unittest.TestCase):
    def test_on_a_background_task_given_its_period(self):
        with_period = runtimes.background(Runtime(idle_after=IDLE))
        self.assertIn("release idle caches", with_period.names())
        self.assertNotIn("release idle caches", runtimes.background(Runtime()).names())


if __name__ == "__main__":
    unittest.main()

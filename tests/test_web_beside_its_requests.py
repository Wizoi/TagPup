"""tagpup_web.py beside its requests: it runs the background tasks
(tagpup.runtime.BACKGROUND), and lets its models go after --release-models-after.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

import tagpup_web  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402

ARGS = ["--tagpup-port", "1", "--tuner-port", "2"]


def quiet():
    return [mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1", "TAGPUP_WEB_RELOADED": "1"}),
            mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")]


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

    def test_a_default_start_loads_no_model_and_warm_up_is_a_flag(self):
        # The owner's log: models on the card 15 s after a start, nobody asking (2026-10-05).
        warmed = []
        with mock.patch.dict(os.environ), mock.patch.object(tagpup_web.web, "serve"), \
                mock.patch.object(Runtime, "warm_up_in_background", side_effect=lambda libs: warmed.append(libs)):
            os.environ.pop("TAGPUP_WEB_NO_WARMUP", None)
            tagpup_web.main(ARGS)
            self.assertEqual([], warmed, "a default start warmed the models")
            tagpup_web.main(ARGS + ["--warm-up"])
            self.assertEqual(1, len(warmed))

    def test_it_lets_the_models_go_after_five_minutes_unless_told_otherwise(self):
        made = []
        with mock.patch.object(tagpup_web.web, "serve"), \
                mock.patch.object(tagpup_web, "Runtime", side_effect=lambda **kw: made.append(kw) or Runtime(**kw)):
            tagpup_web.main(ARGS)
            tagpup_web.main(ARGS + ["--release-models-after", "20"])
            tagpup_web.main(ARGS + ["--release-models-after", "0"])
        self.assertEqual([5 * 60, 20 * 60, None], [kw["idle_after"] for kw in made])
        # Kept between runs in each, "0" keeping them for good (#774).
        self.assertEqual([True, True, True], [kw.get("keep_models") for kw in made])


if __name__ == "__main__":
    unittest.main()

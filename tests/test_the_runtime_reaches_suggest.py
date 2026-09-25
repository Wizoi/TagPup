"""Suggest runs on the models the app was made with.

The models reached the suggestion runs through a module-level slot in
tagpup.jobs.suggestions that the web launcher filled from a script
(scripts/suggest_models.install, docs/findings.md, #112): an app made any other way --
a sandbox's, a test's -- had none, and nothing said which models a run would get. Now
the launcher builds one Runtime (tagpup.runtime) and hands it to the app, and the route
hands it to the run.
"""
import os
import sys
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402


class TheRuntimeReachesSuggest(unittest.TestCase):
    def setUp(self):
        self.runtime = mock.Mock()
        self.app, self.home = web_client.app_for(self, "tagpup", runtime=self.runtime)
        self.client = self.app.test_client()
        self.folder = os.path.join(self.home.root, "Photos", "Harbour Walk")
        os.makedirs(self.folder)
        Image.new("RGB", (8, 8)).save(os.path.join(self.folder, "jetty.jpg"), "JPEG")

    def test_the_app_keeps_it(self):
        self.assertIs(self.runtime, self.app.config["RUNTIME"])

    def test_a_run_started_by_the_page_is_given_it(self):
        started = []
        with mock.patch.object(suggestion_jobs.SuggestionRuns, "start",
                               lambda runs, folder, work: started.append(work) or "running"):
            reply = self.client.post("/library/api/folder/suggest-start", json={"folder_path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(1, len(started))
        started[0].begin()
        self.runtime.begin.assert_called_once_with(Library(self.home.library("library.db")))

    def test_an_app_made_without_one_says_so_when_a_run_begins(self):
        app, home = web_client.app_for(self, "tagpup")
        self.assertIsNone(app.config["RUNTIME"])
        work = suggestion_jobs.work_for(Library(home.library("library.db")), dict, None)
        with self.assertRaisesRegex(RuntimeError, "without a runtime"):
            work.begin()


def settings_with(**sections):
    """This home's settings, with `sections` ({section: {key: value}}) set over them."""
    settings = tagpup_config.load()
    for section, values in sections.items():
        for key, value in values.items():
            settings.set(section, key, value)
    return settings


class ARuntime(unittest.TestCase):
    def test_builds_no_model_until_one_is_asked_for(self):
        from tagpup.ml import clip, faces
        with mock.patch.object(clip, "ClipModel") as clip_model, \
                mock.patch.object(faces, "FaceModel") as face_model:
            runtime = Runtime(tagpup_config.load())
            clip_model.assert_not_called()
            face_model.assert_not_called()
            self.assertIs(runtime.clip, runtime.clip)
            self.assertIs(runtime.faces, runtime.faces)
        clip_model.assert_called_once_with(**runtime.embedder_settings)
        face_model.assert_called_once_with(**runtime.face_settings)

    def test_the_candidate_words_are_read_at_each_run(self):
        """The settings' candidate words were read when each Suggest run began; an
        edit reached the next run without a restart. Found in review of 5.5, where the
        runtime read them once, at start."""
        # The runtime reads once as it is made (the CLIP settings), then once a run.
        read = iter([settings_with(candidates={"tags": "Kayak"}),
                     settings_with(candidates={"tags": "Kayak"}),
                     settings_with(candidates={"tags": "Kayak, Canoe"})])
        runtime = Runtime(lambda: next(read), clip=object(), faces=object())
        asked = []
        with mock.patch("tagpup.services.suggester.model_for_run",
                        side_effect=lambda index, clip, faces, words: asked.append(words)), \
                mock.patch.object(runtime, "photo_index", return_value=object()):
            runtime.begin(Library("a.db"))
            runtime.begin(Library("a.db"))
        self.assertEqual([["Kayak"], ["Kayak", "Canoe"]], asked)

    def test_a_command_with_no_face_model_reads_no_face_settings(self):
        """stats, list and remove never touch a model; a malformed [faces] setting
        failed them once the runtime read every setting at its start. Found in review
        of 5.5."""
        runtime = Runtime(settings_with(faces={"min_face_size": "twenty"}))
        self.assertTrue(runtime.model_key)
        with self.assertRaises(ValueError):
            _ = runtime.faces

    def test_names_its_vectors_as_the_store_does(self):
        from tagpup.store import embeddings
        settings = tagpup_config.load()
        self.assertEqual(embeddings.model_key(**tagpup_config.embedder_settings(settings)),
                         Runtime(settings).model_key)

    def test_is_given_the_settings_and_reads_none_itself(self):
        settings = tagpup_config.load()
        settings.set("faces", "min_face_size", "33")
        settings.set("candidates", "tags", "Kayak, Lighthouse")
        runtime = Runtime(settings)
        self.assertEqual(33, runtime.face_settings["min_face_size"])
        self.assertEqual(["Kayak", "Lighthouse"], runtime.candidate_words)


if __name__ == "__main__":
    unittest.main()

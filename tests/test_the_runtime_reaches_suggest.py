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
import own_home  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402


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


class ARuntime(unittest.TestCase):
    """Each library's models, from the library's settings (tagpup.services.settings)."""

    def setUp(self):
        self.home = own_home.for_test(self)
        path = self.home.library("harbour.db")
        library_actions.create(path)
        self.library = Library(path)

    def test_builds_no_model_until_one_is_asked_for(self):
        from tagpup.ml import clip, faces
        with mock.patch.object(clip, "ClipModel") as clip_model, \
                mock.patch.object(faces, "FaceModel") as face_model:
            runtime = Runtime()
            clip_model.assert_not_called()
            face_model.assert_not_called()
            self.assertIs(runtime.clip(self.library), runtime.clip(self.library))
            self.assertIs(runtime.faces(self.library), runtime.faces(self.library))
        found = library_settings.of(self.library)
        clip_model.assert_called_once_with(**found.embedder)
        face_model.assert_called_once_with(**found.faces)

    def test_the_candidate_words_are_read_at_each_run(self):
        """The candidate words were read when each Suggest run began; an edit reached the
        next run without a restart. Found in review of 5.5, where the runtime read them
        once, at start. Now they are the library's, changed in its settings."""
        library_settings.change(self.library, {"candidates.tags": "Kayak"})
        runtime = Runtime(clip=object(), faces=object())
        self.addCleanup(runtime.forget, self.library)
        asked = []
        with mock.patch("tagpup.services.suggester.model_for_run",
                        side_effect=lambda index, clip, faces, words: asked.append(words)):
            runtime.begin(self.library).end()
            library_settings.change(self.library, {"candidates.tags": "Kayak, Canoe"})
            runtime.begin(self.library).end()
        self.assertEqual([["Kayak"], ["Kayak", "Canoe"]], asked)

    def test_a_command_with_no_face_model_reads_no_face_settings(self):
        """stats, list and remove never touch a model; a malformed face setting failed
        them once the runtime read every setting at its start. Found in review of 5.5.
        (A value the validator would refuse, as a later version might have left it.)"""
        conn = db.connect(self.library.path)
        try:
            conn.execute("UPDATE settings SET value = 'twenty' WHERE key = 'faces.min_face_size'")
            conn.commit()
        finally:
            conn.close()
        runtime = Runtime()
        self.assertTrue(runtime.model_key(self.library))
        with self.assertRaises(ValueError):
            runtime.faces(self.library)

    def test_names_its_vectors_as_the_store_does(self):
        from tagpup.store import embeddings
        self.assertEqual(embeddings.model_key(**library_settings.of(self.library).embedder),
                         Runtime().model_key(self.library))

    def test_reads_the_librarys_settings(self):
        library_settings.change(self.library, {"faces.min_face_size": "33", "candidates.tags": "Kayak, Lighthouse"},
                                acknowledged=["faces"])
        runtime = Runtime()
        self.assertEqual(33, runtime.settings(self.library).faces["min_face_size"])
        self.assertEqual(["Kayak", "Lighthouse"], runtime.candidate_words(self.library))


if __name__ == "__main__":
    unittest.main()

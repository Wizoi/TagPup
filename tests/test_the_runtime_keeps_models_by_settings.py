"""The runtime builds its models from a library's settings (docs/ARCHITECTURE.md, phase 7.6): two libraries on one
model share it, a library on another gets its own, a changed setting is seen by the next ask, and a model no library
uses is let go. The Views owner's (tagpup.runtime). A library's settings stamped, changed in the gear, undone in
History and read by the next run are the Data owner's scenario: tests/test_a_librarys_settings_are_its_own.py.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

def settings_rows(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


class ALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)

    def new_library(self, name="harbour.db"):
        """A library as the picker's Create makes it."""
        path = self.home.library(name)
        self.assertEqual(1, library_actions.create(path).changed)
        return Library(path)

    def library_in_use(self, name="harbour.db"):
        """A library from before the settings were in it: its tables brought up to date,
        and no settings."""
        path = self.home.library(name)
        schema.ensure(path)
        return Library(path)


class FakeModel:
    def __init__(self, settings):
        self.settings = dict(settings)


class TheRuntimeKeysModelsBySettings(ALibrary):
    def setUp(self):
        super().setUp()
        self.built = {"clip": [], "faces": []}
        self.runtime = Runtime(build_clip=lambda s: self.build("clip", s), build_faces=lambda s: self.build("faces", s))
        self.harbour = self.new_library("harbour.db")
        self.meadow = self.new_library("meadow.db")
        self.orchard = self.new_library("orchard.db")
        settings.change(self.meadow, {"model.name": "ViT-B-32", "model.pretrained": "openai",
                                      "faces.min_face_size": "40"}, acknowledged=["clip", "faces"])

    def build(self, kind, settings_given):
        model = FakeModel(settings_given)
        self.built[kind].append(model)
        return model

    def test_two_libraries_on_one_model_share_it(self):
        self.assertIs(self.runtime.clip(self.harbour), self.runtime.clip(self.orchard))
        self.assertIs(self.runtime.faces(self.harbour), self.runtime.faces(self.orchard))
        self.assertEqual(1, len(self.built["clip"]))
        self.assertEqual(1, len(self.built["faces"]))

    def test_a_library_on_another_gets_its_own(self):
        harbour, meadow = self.runtime.clip(self.harbour), self.runtime.clip(self.meadow)
        self.assertIsNot(harbour, meadow)
        self.assertEqual(harbour.settings["model_name"], "ViT-H-14")
        self.assertEqual(meadow.settings["model_name"], "ViT-B-32")
        self.assertEqual(self.runtime.faces(self.meadow).settings["min_face_size"], 40)
        self.assertEqual(self.runtime.faces(self.harbour).settings["min_face_size"], 20)
        self.assertNotEqual(self.runtime.model_key(self.harbour), self.runtime.model_key(self.meadow))

    def test_a_changed_setting_is_seen_by_the_next_ask(self):
        self.runtime.clip(self.harbour)
        settings.change(self.harbour, {"model.name": "ViT-L-14"}, acknowledged=["clip"])
        self.assertEqual(self.runtime.clip(self.harbour).settings["model_name"], "ViT-L-14")
        self.assertEqual(2, len(self.built["clip"]))

    def test_each_librarys_photo_index_is_under_its_model(self):
        self.assertEqual(self.runtime.photo_index(self.meadow).model, self.runtime.model_key(self.meadow))
        before = self.runtime.photo_index(self.harbour)
        self.assertEqual(before.model, self.runtime.model_key(self.harbour))
        settings.change(self.harbour, {"model.force_image_size": "224"}, acknowledged=["clip"])
        after = self.runtime.photo_index(self.harbour)
        self.assertEqual(after.model, self.runtime.model_key(self.harbour))
        self.assertNotEqual(before.model, after.model)
        after.close()
        self.runtime.photo_index(self.meadow).close()

    def test_a_run_gets_its_librarys_words_and_models(self):
        from unittest import mock
        settings.change(self.meadow, {"candidates.tags": "Kayak, Lighthouse"})
        with mock.patch("tagpup.services.suggester.model_for_run") as model_for_run:
            self.runtime.begin(self.meadow).end()
        photo_index, clip, faces, words = model_for_run.call_args.args
        self.assertEqual(words, ["Kayak", "Lighthouse"])
        self.assertEqual(clip.settings["model_name"], "ViT-B-32")
        self.assertEqual(faces.settings["min_face_size"], 40)
        self.runtime.forget(self.meadow)

    def warmable(self):
        FakeModel.load = lambda model: None
        FakeModel.embed_text = lambda model, text: None
        self.addCleanup(delattr, FakeModel, "load")
        self.addCleanup(delattr, FakeModel, "embed_text")

    def test_warming_up_loads_the_one_set_most_libraries_share_and_stamps_nothing(self):
        """It warmed one model per distinct set of settings in the data folder, used or
        not: each gigabytes on the GPU. Found in review of f127e47."""
        self.warmable()
        unstamped = self.library_in_use("quarry.db")
        self.runtime.warm_up([self.meadow, self.harbour, self.orchard, unstamped])
        self.assertEqual(["ViT-H-14"], [m.settings["model_name"] for m in self.built["clip"]])
        self.assertEqual([20], [m.settings["min_face_size"] for m in self.built["faces"]])
        self.assertEqual({}, settings_rows(unstamped))

    def test_warming_up_the_startup_library_loads_its_set_alone(self):
        self.warmable()
        self.runtime.warm_up([self.meadow])
        self.assertEqual(["ViT-B-32"], [m.settings["model_name"] for m in self.built["clip"]])
        self.assertEqual(1, len(self.built["faces"]))


class UnloadingModel(FakeModel):
    def __init__(self, settings):
        super().__init__(settings)
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


class FakeRun:
    """What model_for_run makes, for a run: it answers from the photo index it was given."""

    def __init__(self, photo_index, clip, faces, words, detect_max_side=None, remember=True):
        self.photo_index, self.clip, self.faces = photo_index, clip, faces
        self.model_key = photo_index.model
        self.answered, self.failed = [], []
        self.before_each = None

    def suggest(self, photo, meta):
        if self.before_each:
            self.before_each()
        try:
            self.answered.append(self.photo_index.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        except Exception as e:
            self.failed.append(repr(e))
            raise
        return {"suggested_tags": []}

    def offered(self, suggestion):
        return [], [], None


class TheRuntimeLetsGoOfWhatNoLibraryUses(ALibrary):
    """After a locked change the process kept the old model on the GPU beside the new one,
    one more for each change; and a model change closed the photo index a running Suggest
    was reading. Found in review of f127e47."""

    def setUp(self):
        super().setUp()
        self.built = {"clip": [], "faces": []}
        self.runtime = Runtime(build_clip=lambda s: self.build("clip", s), build_faces=lambda s: self.build("faces", s))
        self.harbour = self.new_library("harbour.db")
        self.orchard = self.new_library("orchard.db")
        self.addCleanup(self.runtime.forget, self.harbour)
        self.addCleanup(self.runtime.forget, self.orchard)

    def build(self, kind, settings_given):
        model = UnloadingModel(settings_given)
        self.built[kind].append(model)
        return model

    def change_model(self, library, name="ViT-L-14"):
        result = settings.change(library, {"model.name": name}, acknowledged=["clip"])
        self.assertEqual(1, result.changed, result.refused)

    def test_a_model_no_library_uses_is_unloaded_when_its_settings_change(self):
        old_clip, old_faces = self.runtime.clip(self.harbour), self.runtime.faces(self.harbour)
        self.change_model(self.harbour)
        self.runtime.settings_changed(self.harbour)
        self.assertEqual(1, old_clip.unloaded, "the old CLIP model stayed loaded")
        self.assertEqual(0, old_faces.unloaded, "the face models it still uses were unloaded")
        new_clip = self.runtime.clip(self.harbour)
        self.assertIsNot(new_clip, old_clip)
        self.assertEqual(new_clip.settings["model_name"], "ViT-L-14")
        self.assertEqual(0, new_clip.unloaded)

    def test_a_change_made_elsewhere_is_let_go_at_the_librarys_next_ask(self):
        old_clip = self.runtime.clip(self.harbour)
        self.change_model(self.harbour)   # by another process, say: nothing tells this one
        self.runtime.clip(self.harbour)
        self.assertEqual(1, old_clip.unloaded)
        self.assertEqual(2, len(self.built["clip"]))

    def test_a_model_another_library_uses_is_kept_until_it_is_forgotten(self):
        shared = self.runtime.clip(self.harbour)
        self.assertIs(shared, self.runtime.clip(self.orchard))
        self.change_model(self.harbour)
        self.runtime.settings_changed(self.harbour)
        self.assertEqual(0, shared.unloaded, "unloaded while orchard still uses it")
        self.assertIs(shared, self.runtime.clip(self.orchard))
        self.runtime.forget(self.orchard)
        self.assertEqual(1, shared.unloaded)

    def test_a_model_a_run_holds_is_kept_until_the_run_ends(self):
        with mock.patch("tagpup.services.suggester.model_for_run", FakeRun):
            run = self.runtime.begin(self.harbour)
        held = run.clip
        self.change_model(self.harbour)
        self.runtime.settings_changed(self.harbour)
        self.assertEqual(0, held.unloaded, "unloaded under a running Suggest")
        run.end()
        self.assertEqual(1, held.unloaded)
        run.end()
        self.assertEqual(1, held.unloaded, "ending twice let go twice")

    def test_a_run_holding_the_old_index_across_a_model_change_finishes(self):
        library = self.harbour
        folder = os.path.join(self.home.root, "Photos", "Harbour Walk")
        photos = {name: {"path": paths.stored(os.path.join(folder, name))} for name in ("jetty.jpg", "buoy.jpg")}
        runs = suggestion_jobs.SuggestionRuns(library.path)
        started = []

        def make_run(*args, **kwargs):
            run = FakeRun(*args, **kwargs)
            started.append(run)

            def meanwhile():
                # The owner changes the CLIP model, and another request asks for the
                # library's index, while the run is between photos.
                if len(run.answered) == 1:
                    self.change_model(library)
                    self.runtime.photo_index(library)
            run.before_each = meanwhile
            return run

        with mock.patch("tagpup.services.suggester.model_for_run", make_run), \
                mock.patch.object(suggestion_jobs, "WORKERS", 1):
            runs.run(folder, suggestion_jobs.work_for(library, lambda: photos, self.runtime))

        [run] = started
        self.assertEqual([], run.failed, "the run's index was closed under it")
        self.assertEqual(2, len(run.answered))
        self.assertEqual("completed", runs.statuses[paths.key(folder)]["status"])
        self.assertIsNone(run.photo_index.conn, "the old index was left open after the run")
        now = self.runtime.photo_index(library)
        self.assertIsNot(now, run.photo_index)
        self.assertEqual(now.model, self.runtime.model_key(library))
        self.assertIsNotNone(now.conn)


if __name__ == "__main__":
    unittest.main()

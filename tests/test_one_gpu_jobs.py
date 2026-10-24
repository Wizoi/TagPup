"""The jobs that use the graphics card take turns on it, and say so (docs/findings.md, #750).

Adding a folder started its index and Suggest on the same 165 photos at once, each loading
its own ViT-H-14; both sat at 0 of 165 for over half an hour. Now:

- Suggest of a folder this process is indexing, or has queued to index, waits for that
  index (tagpup.jobs.indexing.IndexQueue.indexing): the index writes the vectors and faces
  Suggest would otherwise make itself. Its status says so, and Cancel stops it at once.
- A Suggest run that needs a model waits for the process's turn on the card, its status
  naming who has it and since when; Cancel stops it at once, and nothing is kept.
- The indexer waits for its turn before its first photo, saying so on the progress line;
  the queue's Cancel stops it while it waits (a file it watches), having indexed nothing.
- Suggest in a library naming another CLIP model than a run under way in this process holds is refused, naming that run
  and both models, and runs once it has ended: one process holds one set of models (#761). Once it has ended, the
  other library's Suggest lets go of the models kept from it before it builds its own, and keeps those it shares.
  The startup warm-up is such a run while it loads (#1423), and a Suggest whose models cannot be made lets go of
  nothing it found kept (#1424).

Fictional names; stand-in models that say they are on "cuda", on a lock folder of the
test's own home. No real model is loaded.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stand_in_models  # noqa: E402
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import gpu_turns, paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import indexing as indexing_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import suggester as suggester_service  # noqa: E402

DEADLINE = 20


def until(condition, seconds=DEADLINE, step=0.02):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(step)
    return condition()


class _Library(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="one_gpu_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = paths.stored(os.path.join(self.home.root, "Regatta"))
        os.makedirs(self.folder)
        library_actions.record_added(self.library, [self.folder])
        self.photos = {paths.key(os.path.join(self.folder, "start %d.jpg" % n)):
                       {"path": paths.stored(os.path.join(self.folder, "start %d.jpg" % n))} for n in range(3)}
        self.runs = suggestion_jobs.runs_for(self.library)
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.addCleanup(indexing_jobs.forget, self.library)

    def status(self):
        return self.runs.status(self.folder)

    def wait_for(self, state):
        self.assertTrue(until(lambda: self.status().get("status") == state), self.status())


class _FakeModels:
    """A runtime: begin() records when it was called; each photo is suggested one tag."""

    def __init__(self):
        self.began = []

    def begin(self, library, **turn):
        self.began.append(time.monotonic())
        return _FakeModel()


class _FakeModel:
    model_key = "fake-model"

    def suggest(self, path, metadata):
        return {"path": path, "suggested_tags": [{"tag": "Activity/Sailing", "score": 0.9}]}

    def offered(self, suggestion):
        return [{"tag": "Activity/Sailing", "score": 0.9}], [], None

    def consensus(self, suggestions):
        return suggestions


class SuggestWaitsForTheFoldersIndex(_Library):
    def setUp(self):
        super().setUp()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.indexed_at = []

        def index(folder, report):
            report("Generating embeddings: 33% (1/3)", 30)
            self.release.wait(DEADLINE)
            self.indexed_at.append(time.monotonic())
            return Result(attempted=1, changed=1)
        self.queue = indexing_jobs.queue_for(self.library)
        self.queue.start([self.folder], index)
        self.assertTrue(until(lambda: self.queue.indexing(self.folder) is not None))

    def test_it_runs_once_the_index_is_done(self):
        models = _FakeModels()
        self.runs.start(self.folder, suggestion_jobs.work_for(self.library, lambda: self.photos, models))
        self.assertTrue(until(lambda: "indexed first" in (self.status().get("message") or "")), self.status())
        self.assertIn("Generating embeddings: 33% (1/3)", self.status()["message"])
        time.sleep(0.3)
        self.assertEqual([], models.began, "Suggest began while its folder was being indexed")
        self.release.set()
        self.wait_for("completed")
        self.assertEqual(1, len(models.began))
        self.assertGreaterEqual(models.began[0], self.indexed_at[0])

    def test_a_subfolder_waits_too(self):
        below = os.path.join(self.folder, "Morning")
        os.makedirs(below)
        self.assertEqual("running", self.queue.indexing(below)["status"])
        self.assertIsNone(self.queue.indexing(os.path.join(self.home.root, "Elsewhere")))

    def test_cancel_stops_the_wait_at_once(self):
        models = _FakeModels()
        self.runs.start(self.folder, suggestion_jobs.work_for(self.library, lambda: self.photos, models))
        self.assertTrue(until(lambda: "indexed first" in (self.status().get("message") or "")))
        asked = time.monotonic()
        self.assertTrue(self.runs.cancel(self.folder))
        self.wait_for("cancelled")
        self.assertLess(time.monotonic() - asked, 3.0)
        self.assertEqual([], models.began)
        self.assertEqual(0, suggestion_jobs.running_in(self.library.key))
        # Nothing to cancel now.
        self.assertFalse(self.runs.cancel(self.folder))


class _CudaClip:
    device = "cuda"

    def __init__(self):
        self.settings = library_settings.LibrarySettings(dict(library_settings.DEFAULTS)).embedder
        self.embedded = []

    def input_of(self, path, seen=None, load=True):
        self.embedded.append(path)
        return path

    def embed_input(self, image_input):
        return [0.0]

    def unload(self):
        pass


class _CudaFaces(stand_in_models.Faces):
    device = "cuda"

    def detect_and_embed_faces(self, path, *, max_side):
        return []


class _EmptyIndex:
    db_path = "no such library.db"
    conn = None
    model = None

    def __init__(self, db_path=None, model=None):
        self.model = model

    def reload_if_changed(self):
        return False

    def close(self):
        pass


class _Taxonomy:
    paths = []

    def __init__(self, db_path=None):
        pass

    def load(self):
        pass

    def people_roots(self):
        return {"people"}


class _Suggester:
    #: The roots the tree files people under, read once for the run as TagSuggester reads them (_Taxonomy's).
    face_roots = {"people"}

    def __init__(self, *args, **kwargs):
        pass

    def _precompute_candidates(self):
        pass

    def knows_named_faces(self):
        return False

    def suggest_for_photo(self, path, emb, k=15, min_sim=0.35, target_metadata=None):
        return {"path": path, "suggested_tags": [{"tag": "Activity/Sailing", "score": 0.7}]}

    def apply_folder_consensus(self, suggestions):
        return suggestions


class SuggestWaitsForTheCard(_Library):
    """The runtime's own run: its photos have no vector, so the first needs CLIP, on the card."""

    def setUp(self):
        super().setUp()
        for patched in (mock.patch("tagpup.runtime.search.PhotoIndex", _EmptyIndex),
                        mock.patch.object(suggester_service, "TagSuggester", _Suggester),
                        mock.patch.object(suggester_service, "TagTaxonomy", _Taxonomy)):
            patched.start()
            self.addCleanup(patched.stop)
        self.clip = _CudaClip()
        self.card = gpu.Card(poll=0.05)
        faces = _CudaFaces()
        self.runtime = Runtime(build_clip=lambda settings: self.clip, build_faces=lambda settings: faces, card=self.card)
        other = gpu.Card(poll=0.05)
        # Every turn ends before the home is deleted: an open card.lock cannot be (#775).
        self.addCleanup(self.card.close)
        self.addCleanup(other.close)
        self.other = other.hold("indexing Lighthouse (harbour)")
        self.addCleanup(self.other.release)

    def start(self):
        self.runs.start(self.folder, suggestion_jobs.work_for(self.library, lambda: self.photos, self.runtime))

    def test_its_status_says_who_has_the_card(self):
        self.start()
        self.assertTrue(until(lambda: gpu_turns.is_waiting(self.status().get("message"))), self.status())
        self.assertIn("in use by: indexing Lighthouse (harbour), since ", self.status()["message"])
        [run] = [each for each in suggestion_jobs.under_way() if each["folder"] == "Regatta"]
        self.assertTrue(gpu_turns.is_waiting(run["message"]), run)
        self.assertEqual([], self.clip.embedded)
        self.assertTrue(gpu.others_waiting(os.environ[gpu.ENV]), "it waits in the queue other processes see")
        self.other.release()
        self.wait_for("completed")
        self.assertEqual(3, len(self.clip.embedded))
        self.assertNotIn("message", self.status())
        self.assertFalse(self.card.holds(), "the run's turn outlived the run")

    def test_cancel_while_it_waits_keeps_nothing(self):
        self.start()
        self.assertTrue(until(lambda: gpu_turns.is_waiting(self.status().get("message"))))
        asked = time.monotonic()
        self.assertTrue(self.runs.cancel(self.folder))
        self.wait_for("cancelled")
        self.assertLess(time.monotonic() - asked, 3.0)
        self.assertEqual([], self.clip.embedded)
        self.assertEqual({}, self.status().get("suggestions") or {}, "a photo cancelled part-way was kept")
        self.assertFalse(gpu.others_waiting(os.environ[gpu.ENV]), "the cancelled run left its place in the queue")


#: A stand-in for tagpup_cli.py, run by tagpup.services.indexing as the indexer is: it waits
#: for the graphics card as the real one does -- saying so -- until its stop file appears.
FAKE_INDEXER = '''
import os, sys, time
stop = os.environ["TAGPUP_STOP_FILE"]
print("Scanning directory: " + sys.argv[2], flush=True)
print("Waiting for the graphics card (in use by: Suggest Regatta (harbour), since 15:41)...", flush=True)
while not os.path.exists(stop):
    time.sleep(0.05)
print("Cancelled while it waited for the graphics card; nothing was indexed.", flush=True)
sys.exit(76)
'''


class TheQueuesCancelStopsAnIndexThatWaits(_Library):
    def test_the_index_stops_and_says_cancelled(self):
        code = os.path.join(self.home.root, "code")
        os.makedirs(code)
        with open(os.path.join(code, "tagpup_cli.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_INDEXER)
        queue = indexing_jobs.queue_for(self.library)

        def index(folder, report):
            return indexing_service.index_folder(self.library, folder, code, report=report)
        queue.start([self.folder], index)
        self.assertTrue(until(lambda: gpu_turns.is_waiting(queue.status(self.folder).get("message"))),
                        queue.status(self.folder))
        self.assertIn("in use by: Suggest Regatta (harbour), since 15:41", queue.status(self.folder)["message"])
        outcome = queue.cancel(everything=True)
        self.assertEqual([self.folder], outcome.details["stopping"])
        queue.wait()
        status = queue.status(self.folder)
        self.assertEqual("cancelled", status["status"], status)
        self.assertEqual(indexing_service.CANCELLED_WAITING, status["message"])
        self.assertEqual("cancelled", queue.history()[0]["outcome"])

    def test_a_run_that_has_begun_is_not_stopped(self):
        queue = indexing_jobs.queue_for(self.library)
        release = threading.Event()
        self.addCleanup(release.set)

        def index(folder, report):
            report("Generating embeddings: 33% (1/3)", 30)
            report.stop_file = os.path.join(self.home.root, "stop")
            release.wait(DEADLINE)
            return Result(attempted=1, changed=1)
        queue.start([self.folder], index)
        self.assertTrue(until(lambda: "33%" in (queue.status(self.folder).get("message") or "")))
        self.assertEqual([], queue.cancel([self.folder]).details["stopping"])
        self.assertFalse(os.path.exists(os.path.join(self.home.root, "stop")))
        release.set()
        queue.wait()
        self.assertEqual("completed", queue.status(self.folder)["status"])


class TheIndexerWaitsBeforeItsFirstPhoto(_Library):
    """The CLI's `index`: stopped while it waits, it has indexed nothing and written nothing."""

    def test_stopped_while_it_waits_it_exits_cancelled(self):
        Image.new("RGB", (8, 8)).save(os.path.join(self.folder, "start 0.jpg"), "JPEG")
        stop = os.path.join(self.home.root, "stop")
        runtime = mock.Mock()
        runtime.settings.side_effect = lambda library, *a: library_settings.of(Library(self.db_path))
        runtime.model_key.return_value = None
        waited = []

        def gpu_turn(what, models, cancelled=None, report=None):
            waited.append(what)
            report(gpu_turns.waiting_line({"what": "Suggest Regatta (harbour)", "since": time.time()}))
            with open(stop, "w") as handle:
                handle.write("stop")
            until(cancelled, 5)
            raise gpu_turns.Cancelled("stopped")
        runtime.gpu_turn.side_effect = gpu_turn
        extractor = mock.Mock()
        extractor.return_value.batch_read.side_effect = lambda batch, people=None: [
            {"path": path, "mtime": 1.0, "size": 1, "tags": [], "people": [], "captions": []} for path in batch]
        writer = mock.Mock()
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime), \
                mock.patch.object(tagpup_cli, "MetadataExtractor", extractor), \
                mock.patch.object(tagpup_cli, "IdentityWriter", writer), \
                mock.patch.dict(os.environ, {indexing_service.STOP_FILE: stop}):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertEqual(indexing_service.EXIT_CANCELLED, result.exit_code, result.output)
        self.assertEqual(["indexing Regatta (harbour)"], waited)
        self.assertIn("Waiting for the graphics card (in use by: Suggest Regatta (harbour)", result.output)
        self.assertIn(indexing_service.CANCELLED_WAITING, result.output)
        writer.return_value.give.assert_not_called()
        runtime.embeddings.return_value.of.assert_not_called()


class _CpuModel:
    """A model a runtime builds from a library's settings, off the card: it takes no turn."""
    device = "cpu"

    def __init__(self, settings):
        self.settings = dict(settings)
        self.unloads = 0

    def load(self):
        pass

    def embed_text(self, text):
        return [0.0]

    def unload(self):
        self.unloads += 1


class TwoLibrariesNamingTwoModels(_Library):
    """harbour names the default CLIP model, cove another; Suggest in both at once in one server (#761)."""

    def setUp(self):
        super().setUp()
        self.cove = Library(self.home.library("cove.db"))
        library_actions.create(self.cove.path)
        self.jetty = paths.stored(os.path.join(self.home.root, "Jetty"))
        os.makedirs(self.jetty)
        library_actions.record_added(self.cove, [self.jetty])
        changed = library_settings.change(self.cove, {"model.name": "ViT-SO400M-14-SigLIP2", "model.pretrained": "webli", "model.force_image_size": ""}, acknowledged=["clip"])
        self.assertTrue(changed.changed, changed.refused)
        self.built = []
        #: The CLIP models that cannot be made (a download refused, say), by name.
        self.unmakeable = ()

        #: The CLIP models not let go each time a run begins to use its own: never two (#761).
        self.loaded_when_used = []

        def build(settings):
            if settings.get("model_name") in self.unmakeable:
                raise RuntimeError("%s cannot be made" % settings["model_name"])
            self.built.append(_CpuModel(settings))
            return self.built[-1]
        self.runtime = Runtime(build_clip=build, build_faces=build)
        self.addCleanup(self.runtime.forget, self.library)
        self.addCleanup(self.runtime.forget, self.cove)
        self.cove_runs = suggestion_jobs.runs_for(self.cove)
        self.addCleanup(suggestion_jobs.forget, self.cove)
        self.cove_photos = {paths.key(os.path.join(self.jetty, "bollard.jpg")):
                            {"path": paths.stored(os.path.join(self.jetty, "bollard.jpg"))}}

    def clips(self):
        return [model for model in self.built if "model_name" in model.settings]

    def model_for_run(self, *args, **kwargs):
        self.loaded_when_used.append([model.settings["model_name"] for model in self.clips() if not model.unloads])
        return _FakeModel()

    def suggest_in(self, runs, folder, library, photos):
        with mock.patch.object(suggester_service, "model_for_run", self.model_for_run):
            runs.run(folder, suggestion_jobs.work_for(library, lambda: photos, self.runtime))
        return runs.status(folder)

    def suggest_in_cove(self):
        return self.suggest_in(self.cove_runs, self.jetty, self.cove, self.cove_photos)

    def test_the_second_waits_for_the_first_and_is_told_why(self):
        with mock.patch.object(suggester_service, "model_for_run", lambda *args, **kwargs: _FakeModel()):
            harbours = self.runtime.begin(self.library, what="Suggest Regatta (harbour)")
        clips = [model for model in self.built if "model_name" in model.settings]
        self.assertEqual(1, len(clips))
        harbour_clip = clips[0].settings["model_name"]
        self.assertNotEqual("ViT-SO400M-14-SigLIP2", harbour_clip)

        refused = self.suggest_in_cove()
        self.assertEqual("error", refused["status"], refused)
        self.assertIn("Suggest Regatta (harbour) is using the CLIP model %s" % harbour_clip, refused["message"])
        self.assertIn("cove names ViT-SO400M-14-SigLIP2", refused["message"])
        self.assertIn("Nothing was suggested", refused["message"])
        self.assertEqual(1, len([model for model in self.built if "model_name" in model.settings]),
                         "cove's model was built beside harbour's")
        with self.assertRaises(gpu_turns.OtherModelsInUse):
            self.runtime.begin(self.cove)

        # A second run on harbour's models is no second set.
        with mock.patch.object(suggester_service, "model_for_run", lambda *args, **kwargs: _FakeModel()):
            self.runtime.begin(self.library, what="Suggest Breakwater (harbour)").end()

        harbours.end()
        self.assertEqual("completed", self.suggest_in_cove()["status"])
        self.assertEqual(["Activity/Sailing"], [each["tag"] for entry in self.cove_runs.status(self.jetty)["suggestions"].values()
                                                for each in entry["tags"]])

    def test_a_library_differing_only_in_an_image_setting_is_told_which(self):
        harbours = self.runtime.settings(self.library).embedder
        changed = library_settings.change(self.cove, {"model.name": harbours["model_name"],
                                                      "model.pretrained": harbours["pretrained"],
                                                      "model.force_image_size": "336"}, acknowledged=["clip"])
        self.assertEqual(3, changed.changed, changed.refused)
        with mock.patch.object(suggester_service, "model_for_run", lambda *args, **kwargs: _FakeModel()):
            harbours = self.runtime.begin(self.library, what="Suggest Regatta (harbour)")
        self.addCleanup(harbours.end)
        with self.assertLogs("tagpup.jobs.suggestions", "INFO") as logged:
            refused = self.suggest_in_cove()
        self.assertEqual("error", refused["status"], refused)
        size = self.runtime.settings(self.library).embedder["force_image_size"]
        self.assertNotEqual(336, size)
        self.assertIn("with force_image_size %s; cove names it with force_image_size 336" % size, refused["message"])
        self.assertFalse([line for line in logged.output if not line.startswith("INFO")], "the refusal was logged as a failure")
        self.assertFalse([record for record in logged.records if record.exc_info], "the refusal was logged with a traceback")

    def test_one_after_the_other_never_two_sets_at_once(self):
        # Suggest in harbour, to its end; its models are kept for the next Suggest on them.
        self.assertEqual("completed", self.suggest_in(self.runs, self.folder, self.library, self.photos)["status"])
        self.assertEqual("completed", self.suggest_in(self.runs, self.folder, self.library, self.photos)["status"])
        [harbours] = self.clips()
        self.assertEqual(0, harbours.unloads, "a model was let go between two runs on it")
        self.assertEqual(2, len(self.built), "harbour's second run built its models again")

        # Then in cove, which names ViT-SO400M-14-SigLIP2: harbour's CLIP model is let go before cove's is used.
        self.assertEqual("completed", self.suggest_in_cove()["status"])
        harbour_clip = harbours.settings["model_name"]
        self.assertEqual([[harbour_clip], [harbour_clip], ["ViT-SO400M-14-SigLIP2"]], self.loaded_when_used,
                         "two CLIP models were loaded at once")
        self.assertEqual(1, harbours.unloads)
        faces = [model for model in self.built if "model_name" not in model.settings]
        self.assertEqual(1, len(faces), "the face models the two share were built again")
        self.assertEqual(0, faces[0].unloads, "the face models the two share were let go")

    def test_a_suggest_on_other_models_waits_for_the_startup_warm_up(self):
        """The server starts and warms harbour's models; the owner clicks Suggest in cove, naming ViT-SO400M-14-SigLIP2, while they load.
        The warm-up held no run's place, so cove's run began, dropped harbour's models and loaded its own while the warm-up
        went on with harbour's: two sets at once (#1423). Now the warm-up is a run of its own while it loads."""
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow_load(model):
            started.set()
            release.wait(DEADLINE)
        with mock.patch.object(_CpuModel, "load", slow_load):
            warming = self.runtime.warm_up_in_background([self.library])
            self.assertTrue(started.wait(DEADLINE))
            refused = self.suggest_in_cove()
            self.assertEqual("error", refused["status"], refused)
            self.assertIn("the warm-up of Suggest's models (TagPup server) is using", refused["message"])
            [harbours] = self.clips()
            self.assertEqual(0, harbours.unloads, "the warm-up's models were let go under it")
            release.set()
            warming.join(DEADLINE)
        self.assertEqual("completed", self.suggest_in_cove()["status"])
        self.assertEqual([["ViT-SO400M-14-SigLIP2"]], self.loaded_when_used, "two CLIP models were loaded at once")

    def test_a_suggest_whose_models_cannot_be_made_loses_none_of_those_kept(self):
        """cove's CLIP model cannot be made. The run is refused, and harbour's models, kept between its runs, are still
        kept: they were taken off the runtime before cove's were made and never let go (#1424), and the next harbour run
        made them again beside them."""
        self.assertEqual("completed", self.suggest_in(self.runs, self.folder, self.library, self.photos)["status"])
        [harbours] = self.clips()
        self.unmakeable = ("ViT-SO400M-14-SigLIP2",)
        refused = self.suggest_in_cove()
        self.assertEqual("error", refused["status"], refused)
        self.assertIn("ViT-SO400M-14-SigLIP2 cannot be made", refused["message"])
        self.assertEqual("completed", self.suggest_in(self.runs, self.folder, self.library, self.photos)["status"])
        self.assertEqual([harbours], self.clips(), "harbour's CLIP model was made a second time")
        self.assertEqual(0, harbours.unloads)


if __name__ == "__main__":
    unittest.main()

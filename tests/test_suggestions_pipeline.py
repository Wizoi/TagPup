"""The folder-suggestions pipeline: its cache file, its failures and its finish line.

Four things went wrong here, each quietly:

- The cache file was written in place with open(..., "w") from four pool threads at
  once, after every photo. Two saves overlapping, or the process stopping mid-write,
  left a file that did not parse, and a file that does not parse restores nothing:
  every saved suggestion for every folder of the library was gone on the next start.
- A photo whose suggestion raised was stored as an empty suggestion. The next run
  skips photos that already have one, so it was never tried again.
- The run said "completed" before folder consensus rewrote its suggestions. The page
  stops polling on "completed", so it kept the scores consensus was about to change.
- Consensus looked only at the photos of this run, so resuming a folder with two
  photos left judged "what does this folder agree on" from those two.

Names here are fictional.
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import paths  # noqa: E402
import tagpup_server  # noqa: E402
from suggester import TagSuggester as RealSuggester  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler as Handler  # noqa: E402


class _LibraryFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sugg_pipeline_")
        self.db = os.path.join(self.dir, "library.db")
        self.cache = Handler._suggestions_cache_path(self.db)
        tagpup_server.set_active_db_path(self.db)
        Handler.suggest_status.clear()

    def tearDown(self):
        tagpup_server.set_active_db_path(self.db)
        Handler.suggest_status.clear()
        Handler.folder_cache.clear()
        Handler.shared_embedder = None
        tagpup_server.set_active_db_path(None)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.assertFalse(os.path.exists(self.dir), f"left behind: {self.dir}")

    def _folder(self, name, count):
        folder = paths.key(os.path.join(self.dir, "Meets", name))
        return folder, {
            f"{folder}/img_{i:03d}.jpg": {"path": f"{folder}/img_{i:03d}.jpg"}
            for i in range(count)
        }


class TheCacheFileIsNeverLeftHalfWritten(_LibraryFixture):
    def test_a_save_that_dies_mid_write_leaves_the_previous_file(self):
        folder, _ = self._folder("2025-11 Classic", 0)
        Handler.suggest_status[folder] = {
            "status": "completed", "completed": 1, "total": 1,
            "suggestions": {"a.jpg": {"tags": [{"tag": "Activity/Swim Meet", "score": 0.9}]}},
        }
        Handler.save_suggestions_cache(self.db)

        Handler.suggest_status[folder]["suggestions"]["b.jpg"] = {"tags": []}
        real_dump = json.dump

        def dies_halfway(obj, fp, *args, **kwargs):
            fp.write('{"half": ')
            raise OSError("the machine went to sleep")

        with mock.patch.object(json, "dump", dies_halfway):
            Handler.save_suggestions_cache(self.db)
        self.assertIs(json.dump, real_dump)

        with open(self.cache, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("a.jpg", saved[folder]["suggestions"])
        leftovers = [p for p in glob.glob(os.path.join(self.dir, "*")) if p != self.cache]
        self.assertEqual(leftovers, [], "a failed save left its temporary file behind")

    def test_concurrent_saves_never_leave_an_unparseable_file(self):
        folder, _ = self._folder("2025-10 Invitational", 0)
        Handler.suggest_status[folder] = {
            "status": "running", "completed": 0, "total": 400, "suggestions": {}}
        Handler.save_suggestions_cache(self.db)
        stop = threading.Event()
        unparseable = []

        def writer(n):
            tagpup_server.set_active_db_path(self.db)
            for i in range(60):
                with Handler.model_lock:
                    Handler.suggest_status[folder]["suggestions"][f"w{n}_{i}.jpg"] = {
                        "tags": [{"tag": f"Activity/Heat {i}", "score": 0.7}] * 20}
                Handler.save_suggestions_cache(self.db)

        def reader():
            while not stop.is_set():
                try:
                    with open(self.cache, encoding="utf-8") as f:
                        text = f.read()
                except (PermissionError, FileNotFoundError):
                    continue  # mid-rename on Windows; not a torn file
                try:
                    json.loads(text)
                except ValueError:
                    unparseable.append(len(text))
                # A reader that never lets go keeps Windows refusing the rename; the
                # real one reads once at startup.
                time.sleep(0.002)

        r = threading.Thread(target=reader)
        r.start()
        writers = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in writers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        r.join()

        self.assertEqual(unparseable, [], f"{len(unparseable)} reads found a torn file")
        with open(self.cache, encoding="utf-8") as f:
            final = json.load(f)
        self.assertEqual(len(final[folder]["suggestions"]), 240,
                         "the last save did not hold everything saved before it")

    def test_saves_during_a_run_are_throttled(self):
        folder, _ = self._folder("2025-09 Relays", 0)
        Handler.suggest_status[folder] = {"status": "running", "suggestions": {}}
        wrote = [Handler.save_suggestions_cache(self.db, min_interval=60) for _ in range(50)]
        self.assertEqual(wrote.count(True), 1, "every photo rewrote the whole file")
        self.assertTrue(Handler.save_suggestions_cache(self.db),
                        "an unthrottled save must always write")


class _FakeEmbedder:
    def __init__(self):
        self.photo_index = object()

    def embed_image(self, path):
        return [0.0]


def _fake_modules(suggester_cls):
    taxonomy = types.ModuleType("taxonomy")

    class TagTaxonomy:
        paths = []

        def __init__(self, file_path=None):
            pass

        def load(self):
            pass

    taxonomy.TagTaxonomy = TagTaxonomy
    suggester = types.ModuleType("suggester")
    suggester.TagSuggester = suggester_cls
    index = types.ModuleType("index")
    index.PhotoIndex = object
    embedder = types.ModuleType("embedder")
    embedder.ClipEmbedder = object
    return {"taxonomy": taxonomy, "suggester": suggester, "index": index, "embedder": embedder}


class _RunFixture(_LibraryFixture):
    """Runs the real suggestions thread with the model replaced by a script."""

    def setUp(self):
        super().setUp()
        self.fail_paths = set()
        self.asked = []
        self.consensus_calls = []
        test = self

        class ScriptedSuggester:
            def __init__(self, *args, **kwargs):
                pass

            def _precompute_candidates(self):
                pass

            def suggest_for_photo(self, path, emb, k=15, min_sim=0.35, target_metadata=None):
                test.asked.append(path)
                if path in test.fail_paths:
                    raise RuntimeError("could not read the image")
                return {"path": path, "suggested_tags": [
                    {"tag": "Activity/Swim Meet", "score": 0.7},
                    {"tag": "Rowan Thackeray", "score": 0.9, "has_face_match": True},
                ]}

            def apply_folder_consensus(self, suggestions):
                folder_key = paths.key(os.path.dirname(suggestions[0]["path"]))
                test.consensus_calls.append({
                    "paths": sorted(s["path"] for s in suggestions),
                    "status_then": Handler.suggest_status[folder_key]["status"],
                })
                return RealSuggester.apply_folder_consensus(self, suggestions)

        self.modules = _fake_modules(ScriptedSuggester)
        Handler.shared_embedder = _FakeEmbedder()

    def run_folder(self, folder, photos):
        Handler.folder_cache[folder] = photos
        Handler.suggest_status[folder] = {
            "status": "preparing", "completed": 0, "total": 0,
            "suggestions": (Handler.suggest_status.get(folder) or {}).get("suggestions", {}),
        }
        with mock.patch.dict(sys.modules, self.modules):
            Handler.run_folder_suggestions_thread(folder, self.db)
        tagpup_server.set_active_db_path(self.db)
        return Handler.suggest_status[folder]


class AFailedPhotoIsTriedAgain(_RunFixture):
    def test_a_photo_that_raised_is_marked_failed_and_retried_next_run(self):
        folder, photos = self._folder("2025-11 Classic", 3)
        broken = sorted(photos)[1]
        self.fail_paths = {paths.stored(broken)}
        status = self.run_folder(folder, photos)

        entry = status["suggestions"][paths.stored(broken)]
        self.assertIn("error", entry, "the failure was stored as an empty suggestion")
        self.assertEqual(entry["tags"], [])

        with open(self.cache, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("error", saved[folder]["suggestions"][paths.stored(broken)])

        self.fail_paths = set()
        self.asked.clear()
        status = self.run_folder(folder, photos)
        self.assertEqual(self.asked, [paths.stored(broken)],
                         "the failed photo was not tried again")
        entry = status["suggestions"][paths.stored(broken)]
        self.assertNotIn("error", entry)
        self.assertTrue(entry["tags"])
        self.assertEqual(status["completed"], 3)


class CompletedMeansConsensusIsDone(_RunFixture):
    def test_the_status_is_not_completed_while_consensus_is_running(self):
        folder, photos = self._folder("2025-10 Invitational", 3)
        status = self.run_folder(folder, photos)
        self.assertEqual(len(self.consensus_calls), 1)
        self.assertNotEqual(self.consensus_calls[0]["status_then"], "completed",
                            "the page could fetch suggestions consensus had not finished")
        self.assertEqual(status["status"], "completed")
        entry = status["suggestions"][paths.stored(sorted(photos)[0])]
        self.assertEqual(entry["tags"][0]["tag"], "Activity/Swim Meet")
        self.assertGreater(entry["tags"][0]["score"], 0.7, "consensus was not applied")

    def test_a_resumed_run_takes_consensus_over_the_whole_folder(self):
        folder, photos = self._folder("2025-09 Relays", 4)
        first_two = dict(sorted(photos.items())[:2])
        self.run_folder(folder, first_two)
        self.consensus_calls.clear()

        status = self.run_folder(folder, photos)
        self.assertEqual(len(self.consensus_calls), 1,
                         "consensus never ran for the rest of the folder")
        self.assertEqual(self.consensus_calls[0]["paths"],
                         sorted(paths.stored(p) for p in photos))
        # And it is not compounded: the photos from the first run are boosted once.
        for p in sorted(photos)[:2]:
            score = status["suggestions"][paths.stored(p)]["tags"][0]["score"]
            self.assertAlmostEqual(score, round(0.7 * 1.25, 2), places=2)


class OneFileOneOwner(unittest.TestCase):
    def test_only_one_function_names_the_suggestions_cache_file(self):
        owners = []
        for module in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
            with open(module, encoding="utf-8") as f:
                if "gui_suggestions_cache" in f.read():
                    owners.append(os.path.basename(module))
        self.assertEqual(owners, ["tagpup_server.py"],
                         "a second copy of the cache naming can drift or write over it")

    def test_the_main_library_keeps_its_existing_file(self):
        self.assertEqual(
            os.path.basename(Handler._suggestions_cache_path(os.path.join("data", "photo_index.db"))),
            "gui_suggestions_cache.json")


if __name__ == "__main__":
    unittest.main()

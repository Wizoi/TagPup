"""The folder-suggestions pipeline: its failures and its finish line.

Four things went wrong here, each quietly:

- The cache file was written in place with open(..., "w") from four pool threads at
  once, after every photo. Two saves overlapping, or the process stopping mid-write,
  left a file that did not parse, and a file that does not parse restores nothing:
  every saved suggestion for every folder of the library was gone on the next start.
  Suggestions are rows of the library now (migration 7), written through its write
  lock, and the file is only read, once, by that migration.
- A photo whose suggestion raised was stored as an empty suggestion. The next run
  skips photos that already have one, so it was never tried again.
- The run said "completed" before folder consensus rewrote its suggestions. The page
  stops polling on "completed", so it kept the scores consensus was about to change.
- Consensus looked only at the photos of this run, so resuming a folder with two
  photos left judged "what does this folder agree on" from those two.

The runs are tagpup.jobs.suggestions'; what they run is the work TagPup's route hands
them (work_for), with the runtime's models (tagpup.runtime) and the suggester replaced
by a script. Names here are fictional.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import python_sources  # noqa: E402

from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import suggester as suggester_service  # noqa: E402
from tagpup.services.suggester import TagSuggester as RealSuggester  # noqa: E402
from tagpup.store import db, schema  # noqa: E402


class _LibraryFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sugg_pipeline_")
        self.db = os.path.join(self.dir, "library.db")
        schema.ensure(self.db)
        self.library = Library(self.db)
        self.runs = suggestion_jobs.runs_for(self.library)

    def tearDown(self):
        suggestion_jobs.forget(self.library)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.assertFalse(os.path.exists(self.dir), f"left behind: {self.dir}")

    def _folder(self, name, count):
        folder = paths.key(os.path.join(self.dir, "Meets", name))
        return folder, {
            f"{folder}/img_{i:03d}.jpg": {"path": f"{folder}/img_{i:03d}.jpg"}
            for i in range(count)
        }


class _FakeIndex:
    """A library's photo index with nothing in it, and no connection to keep vectors on."""
    db_path = "no such library.db"
    conn = None


class _FakeClip:
    """CLIP, as the runtime's settings name it, that embeds everything alike."""

    def __init__(self):
        self.settings = library_settings.LibrarySettings(dict(library_settings.DEFAULTS)).embedder

    def embed_image(self, path):
        return [0.0]


class _FakeTaxonomy:
    paths = []

    def __init__(self, db_path=None):
        pass

    def load(self):
        pass

    def people_roots(self):
        return {"people", "family", "friends"}


class _RunFixture(_LibraryFixture):
    """Runs a folder through the real run and TagPup's work, the model a script."""

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
                    "status_then": test.runs.statuses[folder_key]["status"],
                })
                return RealSuggester.apply_folder_consensus(self, suggestions)

        # The models are the runtime's fakes, and this library's index an empty one.
        self.runtime = Runtime(clip=_FakeClip(), faces=object())
        for patched in (mock.patch.object(self.runtime, "photo_index", lambda library: _FakeIndex()),
                        mock.patch.object(suggester_service, "TagSuggester", ScriptedSuggester),
                        mock.patch.object(suggester_service, "TagTaxonomy", _FakeTaxonomy)):
            patched.start()
            self.addCleanup(patched.stop)

    def run_folder(self, folder, photos):
        self.runs.statuses[folder] = {"status": "preparing", "completed": 0, "total": 0}
        self.runs.run(folder, suggestion_jobs.work_for(self.library, lambda: photos, self.runtime))
        return self.runs.status(folder)


class AFailedPhotoIsTriedAgain(_RunFixture):
    def test_a_photo_that_raised_is_marked_failed_and_retried_next_run(self):
        folder, photos = self._folder("2025-11 Classic", 3)
        broken = sorted(photos)[1]
        self.fail_paths = {paths.stored(broken)}
        status = self.run_folder(folder, photos)

        entry = status["suggestions"][paths.stored(broken)]
        self.assertIn("error", entry, "the failure was stored as an empty suggestion")
        self.assertEqual(entry["tags"], [])

        # Kept in the library: what a process started afresh finds.
        saved = suggestion_jobs.SuggestionRuns(self.db).suggestions(folder)
        self.assertIn("error", saved[paths.stored(broken)])

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


class WithoutAModelProvider(_LibraryFixture):
    def test_a_run_says_it_has_no_models(self):
        """An app made without a runtime fails the run with a message, not a NameError
        three frames down; an empty folder never needs the model."""
        folder, photos = self._folder("2025-08 Sprints", 1)
        self.runs.run(folder, suggestion_jobs.work_for(self.library, lambda: photos, None))
        status = self.runs.status(folder)
        self.assertEqual(status["status"], "error")
        self.assertIn("without a runtime", status["message"])
        self.runs.run(folder, suggestion_jobs.work_for(self.library, lambda: {}, None))
        self.assertEqual(self.runs.status(folder)["message"], "No images found in this folder.")

    def test_there_is_no_slot_to_fill(self):
        """The models were a module-level slot a script filled (docs/findings.md, #112)."""
        self.assertFalse(hasattr(suggestion_jobs, "models"))


class OneFileOneOwner(unittest.TestCase):
    def test_only_one_function_names_the_suggestions_cache_file(self):
        owners = []
        for module in python_sources():
            with open(os.path.join(WORKSPACE_DIR, module), encoding="utf-8") as f:
                if "gui_suggestions_cache" in f.read():
                    owners.append(module.replace(os.sep, "/"))
        # Migration 7 reads it once, into the library; nothing else names it.
        self.assertEqual(owners, ["tagpup/store/schema.py"],
                         "a second copy of the cache naming can drift or write over it")

    def test_the_main_library_keeps_its_existing_file(self):
        folder = tempfile.mkdtemp(prefix="sugg_file_")
        self.addCleanup(shutil.rmtree, folder, True)
        conn = db.connect(os.path.join(folder, "photo_index.db"))
        try:
            self.assertEqual(os.path.basename(schema._suggestions_file(conn)), "gui_suggestions_cache.json")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""The review of analyse-only Suggest (findings #545-#547): what a look keeps and for how long.

A look is what Suggest finds in a folder the library does not hold, kept in this process's memory
(tagpup.jobs.suggestions.SuggestionRuns.looks). It is dropped when the folder becomes the library's;
an owner reviewing it is not stranded by the idle release; a Smart Rename that renames the files
renames what is kept; a person Suggest names is filed as the chip files them; and the year
prompts, which a held run keeps in the library, are kept in memory for a run that must not.
The models are test_analyse_only_suggest's stand-ins; the rest is real. Names are fictional.
"""
import os
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import library_dump  # noqa: E402
import test_analyse_only_suggest as base  # noqa: E402

from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import suggester  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

Base = base.Base


class FakeIndex:
    """The folder indexer the queue runs, stood in for. `gate`, when set, holds it until released."""

    def __init__(self, gate=None):
        self.folders, self.gate = [], gate

    def __call__(self, library, folder, code_folder, **_kwargs):
        if self.gate is not None:
            self.gate.wait(30)
        self.folders.append(folder)
        return Result(attempted=1, changed=1)


class WithIndexing(Base):
    def setUp(self):
        super().setUp()
        self.gate = None
        self.index = FakeIndex()
        patcher = mock.patch.object(tagpup_routes.indexing, "index_folder", self.index)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.runs = suggestion_jobs.runs_for(self.library)

    def add(self, folder):
        reply = self.post("/folder/add", {"folder_path": folder})
        self.assertEqual(200, reply.status_code, reply.data)


class TheLookIsLetGoWhenTheFolderBecomesTheLibrarys(WithIndexing):
    """#545: after Add the status went on answering completed/in_memory, so the page could not start the run
    that saves."""

    def test_adding_the_folder_drops_the_look_and_the_next_start_saves(self):
        self.start(self.cup)
        self.assertTrue(self.status(self.cup)["in_memory"])
        self.add(self.cup)
        indexing_jobs.queue_for(self.library).wait()
        found = self.status(self.cup)
        self.assertEqual("idle", found["status"])
        self.assertNotIn("in_memory", found)
        self.assertEqual({}, self.runs.looks)
        self.assertFalse(self.start(self.cup)["in_memory"], "the next start must be the run that saves")
        self.assertEqual(4, self.rows("SELECT COUNT(*) FROM suggestions s JOIN photos p ON p.id = s.photo_id "
                                      "WHERE p.path LIKE '%Harbour Cup%'")[0][0])

    def test_a_run_in_flight_when_the_folder_is_added_is_dropped_when_its_indexing_completes(self):
        self.index.gate = threading.Event()
        with mock.patch.object(suggestion_jobs, "threading",
                               types.SimpleNamespace(Lock=threading.Lock, Thread=base.Deferred)):
            self.start(self.cup)
            self.add(self.cup)
            base.Deferred.waiting[0].run()
        self.assertTrue(self.status(self.cup)["in_memory"], "the run finishes in memory")
        self.index.gate.set()
        indexing_jobs.queue_for(self.library).wait()
        self.assertEqual("idle", self.status(self.cup)["status"])
        self.assertEqual({}, self.runs.looks)

    def test_another_folders_look_is_left_alone(self):
        other = os.path.join(self.home.root, "Share", "Other")
        base.damaged_photos.whole_jpeg(os.path.join(other, "other_01.jpg"), seed=91)
        self.start(self.cup)
        self.start(other)
        self.add(self.cup)
        indexing_jobs.queue_for(self.library).wait()
        self.assertTrue(self.status(other)["in_memory"])


class AnOwnerReviewingIsNotStranded(WithIndexing):
    """#547 (a): a status poll, an Apply and a view of the folder are use."""

    def test_each_counts_as_use(self):
        self.start(self.cup)
        touched = []
        with mock.patch.object(suggestion_jobs, "on_looks_use", lambda: touched.append(1)):
            for what, call in (("a status poll", lambda: self.status(self.cup)),
                               ("an Apply", lambda: self.post("/folder/auto-apply",
                                                              {"folder_path": self.cup, "threshold": 0.0})),
                               ("a view of the folder",
                                lambda: self.client.get("/library/api/folder/scan", query_string={"path": self.cup}))):
                del touched[:]
                call()
                self.assertTrue(touched, "%s was not counted as use" % what)

    def test_nothing_is_touched_when_there_is_no_look(self):
        touched = []
        with mock.patch.object(suggestion_jobs, "on_looks_use", lambda: touched.append(1)):
            self.client.get("/library/api/folder/scan", query_string={"path": self.cup})
        self.assertEqual([], touched)


class ASmartRenameRenamesWhatIsKept(Base):
    """#547 (c)."""

    def test_the_look_follows_the_files(self):
        self.start(self.cup)
        seen = len(self.clip.images)
        reply = self.post("/folder/rename-photos", {"folder_path": self.cup, "photo_paths": self.loose,
                                                    "grouping": "Lighthouse"})
        self.assertEqual(200, reply.status_code, reply.data)
        names = sorted(os.path.basename(path) for path in self.status(self.cup)["suggestions"])
        self.assertEqual(["Lighthouse - 1.jpg", "Lighthouse - 2.jpg", "Lighthouse - 3.jpg", "cup_04.jpg"], names)
        found = self.status(self.cup)
        for path, entry in found["suggestions"].items():
            if "error" not in entry:
                self.assertEqual(os.path.basename(path), os.path.basename(entry["raw_suggestions"]["path"]))
        # Not analysed again, and Apply reaches the files under their new names.
        self.start(self.cup)
        self.assertEqual(seen, len(self.clip.images))
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        self.assertIn("Trips/Regatta", base.tags_in(os.path.join(self.cup, "Lighthouse - 1.jpg")))
        self.assert_library_unchanged()


class TheLibraryIsReadOutsideTheRunsLock(Base):
    """#547 (d)."""

    def test_what_the_library_lacks_is_asked_without_the_lock(self):
        runs = suggestion_jobs.runs_for(self.library)
        held = []
        real = suggester.SuggestionModel.what_it_lacks

        def asked(model):
            held.append(runs.lock.locked())
            return real(model)

        with mock.patch.object(suggester.SuggestionModel, "what_it_lacks", asked):
            self.start(self.cup)
        self.assertEqual([False], held)


class TheYearPromptsAreKeptInMemory(Base):
    """#547 (e): a held run keeps them in the library; a looking run may not, and re-embedded them every run."""

    def setUp(self):
        super().setUp()
        for path in self.loose:
            base.write_into(path, **{"EXIF:DateTimeOriginal": "2019:06:15 10:30:00"})
        self.texts = []
        original = self.clip.embed_text
        self.clip.embed_text = lambda prompt: self.texts.append(prompt) or original(prompt)
        self.addCleanup(suggester.forget_looking_text_embeddings, self.library.path)
        self.before = library_dump.dump(self.library.path)

    def year_prompts(self):
        return [text for text in self.texts if "2019" in text]

    def test_a_second_run_does_not_embed_them_again_and_nothing_is_saved(self):
        self.start(self.cup)
        first = len(self.year_prompts())
        self.assertGreater(first, 0, "the run embedded no year prompt: the test says nothing")
        suggestion_jobs.release_looks()
        self.start(self.cup)
        self.assertEqual(first, len(self.year_prompts()))
        self.assert_library_unchanged()

    def test_they_go_with_the_library(self):
        self.start(self.cup)
        first = len(self.year_prompts())
        suggestion_jobs.forget(self.library)
        self.start(self.cup)
        self.assertGreater(len(self.year_prompts()), first)

    def test_the_cache_is_bounded(self):
        cache = suggester.TextEmbeddingCache(limit=3)
        for number in range(5):
            cache.put(("prompt %d" % number, "m", "p"), [float(number)] * 4)
        self.assertEqual(3, len(cache))
        self.assertIsNone(cache.get(("prompt 0", "m", "p")))
        self.assertEqual([4.0] * 4, cache.get(("prompt 4", "m", "p")))


class APersonIsFiledAsTheChipFilesThem(Base):
    """#546: Apply All wrote a bare name for a named face whose node the tree lacks; a click on the chip
    files it under the one people root. Both now: People/<name>, no node made."""

    def know_wren(self):
        conn = db.connect(self.library.path)
        try:
            store_faces.insert(conn, self.held[1], [0, 0, 100, 100], base.WREN.tobytes(), name="Wren Okafor", prob=0.99)
            conn.commit()
        finally:
            conn.close()
        self.before = library_dump.dump(self.library.path)

    def test_not_held(self):
        self.know_wren()
        tree = base.tree_of(self.library.path)
        self.start(self.cup)
        self.assertEqual(["Wren Okafor"], [p["name"] for p in self.suggested(self.cup)["cup_03.jpg"]["people"]])
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        self.assertEqual(["Trips/Regatta", "People/Wren Okafor"], base.tags_in(self.loose[2]))
        self.assertEqual(tree, base.tree_of(self.library.path), "a node was made")
        self.assert_library_unchanged()

    def test_held(self):
        self.know_wren()
        self.assertFalse(self.start(self.regatta)["in_memory"])
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.regatta, "threshold": 0.0,
                                                               "photo_paths": [self.held[2]]}).status_code)
        self.assertIn("People/Wren Okafor", base.tags_in(self.held[2]))
        self.assertNotIn("Wren Okafor", base.tags_in(self.held[2]))

    def test_with_two_people_roots_the_name_is_left_as_it_was_for_the_page_to_ask(self):
        self.know_wren()
        conn = db.connect(self.library.path)
        try:
            store_taxonomy.add_node(conn, "Pets/Rex")
            conn.execute("UPDATE tag_taxonomy SET has_face = 1 WHERE tag = 'Pets'")   # a second people root
            conn.commit()
        finally:
            conn.close()
        self.before = library_dump.dump(self.library.path)
        self.start(self.cup)
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        self.assertIn("Wren Okafor", base.tags_in(self.loose[2]))
        self.assertNotIn("People/Wren Okafor", base.tags_in(self.loose[2]))

    def test_a_person_the_tree_holds_is_filed_where_the_tree_has_them(self):
        self.start(self.cup)
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        self.assertEqual(["Trips/Regatta", "People/Rowan Thackeray"], base.tags_in(self.loose[0]))


if __name__ == "__main__":
    unittest.main()

"""Suggest in a folder the library does not hold analyses the photos and adds nothing to it.

The owner (2026-10-03): "I thought previously we could do the photo analysis and get the
suggestions from the index -- but in this case we just do not want to add it to the index."
Suggest used to save its results as rows -- suggestions, faces, crops, vectors -- which is
exactly what adds a folder to the library, so it was refused (409) in a folder not held.
Now such a run compares each photo with what the library already knows (its vectors, its
named faces, its tag tree: all READ), keeps what it finds in this process's memory, and
the owner applies it to the photo FILES (tagpup.services.file_only). The library is
unchanged after: every table is dumped before and after (tests/library_dump.py) and its rows are the same.

The models are stood in for -- no download, no GPU -- by two small fakes that embed a photo
by its name and find faces by it; everything else is real: the app, its routes, the runtime,
the library's index and tables, the suggester, ExifTool and Pillow. The photos are noise
JPEGs made here. Names are fictional.
"""
import os
import sys
import threading
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import library_dump  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

ROWAN = np.array([0.6, 0.8, 0.0, 0.0], dtype=np.float32)
WREN = np.array([0.0, 0.0, 0.6, 0.8], dtype=np.float32)


def vector_of(path):
    """A photo's CLIP vector by its name: the photos of one set are near each other."""
    number = int("".join(c for c in os.path.basename(path) if c.isdigit()) or 0)
    vector = np.array([1.0, 0.02 * number, 0.0, 0.0], dtype=np.float32)
    return (vector / np.linalg.norm(vector)).tolist()


class FakeClip:
    """CLIP, as the runtime hands it out: the photo is decoded for real (a file that does not
    decode raises as the real model's does), the vector is made from its name."""

    model_name = "fake"
    pretrained = "fake"

    def __init__(self, settings):
        self.settings = settings
        self.images = []
        self.fail = None

    def embed_image(self, path, seen=None):
        if self.fail:
            raise RuntimeError(self.fail)
        images.opened(path, upright=True)
        self.images.append(path)   # only a photo that decoded was analysed
        return vector_of(path)

    def embed_text(self, prompt):
        return [0.0, 0.0, 0.0, 1.0]


class FakeFaces:
    """The face model: a face of Rowan in a photo whose name ends 01 or 02, one of Wren in 03."""

    def __init__(self):
        self.detected = []

    def detect_and_embed_faces(self, path):
        self.detected.append(path)
        name = os.path.splitext(os.path.basename(path))[0]
        if name.endswith(("01", "02")):
            return [{"box": [0, 0, 100, 100], "embedding": ROWAN.tolist(), "prob": 0.99}]
        if name.endswith("03"):
            return [{"box": [0, 0, 100, 100], "embedding": WREN.tolist(), "prob": 0.99}]
        return []


class Inline:
    """A thread that runs its target when started, here: no thread of a test's own."""

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class Deferred(Inline):
    """A thread that waits to be run: what a run in flight is, for a test that does something meanwhile."""

    waiting = []

    def start(self):
        Deferred.waiting.append(self)

    def run(self):
        self.target(*self.args)


def tags_in(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        return fields.field_values(et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject"))


def write_into(path, **tags):
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=tags, params=["-overwrite_original"])


def tree_of(db_path):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return sorted(store_taxonomy.tags(conn))
    finally:
        conn.close()


def files_under(folder, leave=()):
    """Every file under `folder`: names relative to it, but the journal side files of the database."""
    found = []
    for here, _folders, names in os.walk(folder):
        for name in names:
            if not name.endswith(("-wal", "-shm", "-journal")) and name not in leave:
                found.append(os.path.relpath(os.path.join(here, name), folder))
    return sorted(found)


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class Base(unittest.TestCase):
    """The library `library` holds Pictures/Regatta: three photos read as the indexer reads them,
    tagged Trips/Regatta, each with its vector, and in the first a named face of Rowan Thackeray.
    The folder Share/Harbour Cup is not the library's: three photos, a fourth cut short."""

    def setUp(self):
        self.app_home()
        self.regatta = os.path.join(self.home.root, "Pictures", "Regatta")
        self.cup = os.path.join(self.home.root, "Share", "Harbour Cup")
        self.held = [os.path.join(self.regatta, "regatta_0%d.jpg" % n) for n in (1, 2, 3)]
        self.loose = [os.path.join(self.cup, "cup_0%d.jpg" % n) for n in (1, 2, 3)]
        self.broken = os.path.join(self.cup, "cup_04.jpg")
        for n, path in enumerate(self.held + self.loose, start=1):
            damaged_photos.whole_jpeg(path, seed=n)
        damaged_photos.truncated(self.broken)
        self.seed_library(self.library)
        self.before = library_dump.dump(self.library.path)
        self.files_before = files_under(self.home.root)

    def app_home(self):
        self.clip = None
        self.faces = FakeFaces()
        self.runtime = Runtime(clip=None, faces=self.faces)
        self.app, self.home = web_client.app_for(self, "tagpup", runtime=self.runtime)
        self.library = Library(self.home.library("library.db"))
        self.clip = FakeClip(library_settings.of(self.library).embedder)
        self.runtime._clip = self.clip
        self.client = self.app.test_client()
        self.addCleanup(self.runtime.forget, self.library)
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.threads = mock.patch.object(suggestion_jobs, "threading",
                                         types.SimpleNamespace(Lock=threading.Lock, Thread=Inline))
        self.threads.start()
        self.addCleanup(self.threads.stop)
        Deferred.waiting = []

    def seed_library(self, library):
        model = store_embeddings.model_key(**self.clip.settings)
        conn = db.connect(library.path)
        try:
            for path in self.held:
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Trips/Regatta"]})
                stamp = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, model, stamp[0], stamp[1],
                                     np.array(vector_of(path), dtype=np.float32).tobytes())
            store_faces.insert(conn, self.held[0], [0, 0, 100, 100], ROWAN.tobytes(), name="Rowan Thackeray", prob=0.99)
            store_taxonomy.add_node(conn, "Trips/Regatta")
            store_taxonomy.add_node(conn, "People/Rowan Thackeray")
            conn.commit()
        finally:
            conn.close()

    # ---- helpers ------------------------------------------------------------------------

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def start(self, folder):
        reply = self.post("/folder/suggest-start", {"folder_path": folder})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def status(self, folder):
        return self.client.get("/library/api/folder/suggest-status", query_string={"path": folder}).get_json()

    def suggested(self, folder):
        found = self.status(folder)
        return {os.path.basename(path): entry for path, entry in found["suggestions"].items()}

    def assert_library_unchanged(self):
        after = library_dump.dump(self.library.path)
        self.assertEqual(sorted(self.before), sorted(after), "a table appeared or went")
        for table in self.before:
            self.assertEqual(self.before[table], after[table], "the library's %s changed" % table)

    def counts(self):
        """(photo rows, vectors) the library holds."""
        return (self.rows("SELECT COUNT(*) FROM photos")[0][0], self.rows("SELECT COUNT(*) FROM embeddings")[0][0])

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class ARunInAFolderNotHeldAddsNothing(Base):
    def test_every_table_is_the_same_after_the_run_and_after_applying_what_it_offered(self):
        self.assertEqual({"success": True, "status": "running", "in_memory": True}, self.start(self.cup))
        found = self.status(self.cup)
        self.assertEqual(("completed", True), (found["status"], found["in_memory"]))
        self.assertEqual(4, found["total"])
        offers = self.suggested(self.cup)
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_03.jpg", "cup_04.jpg"], sorted(offers))
        # What the library knows: its photos' tags from the neighbours, its named face by the face.
        self.assertEqual(["Trips/Regatta"], [t["tag"] for t in offers["cup_01.jpg"]["tags"]])
        self.assertEqual(["People/Rowan Thackeray"], [p["name"] for p in offers["cup_01.jpg"]["people"]])
        self.assertEqual(["People/Rowan Thackeray"], [p["name"] for p in offers["cup_02.jpg"]["people"]])
        self.assertEqual([], offers["cup_03.jpg"]["people"], "Wren is no one the library has named")
        # A photo that does not decode: said, with nothing recorded of it.
        self.assertIn("error", offers["cup_04.jpg"])
        self.assertEqual(1, found["unread"])
        self.assertEqual(["cup_01.jpg", "cup_02.jpg", "cup_03.jpg"], sorted(os.path.basename(p) for p in self.faces.detected))
        self.assert_library_unchanged()

        reply = self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((3, 0), (reply.get_json()["file_only"], reply.get_json()["with_rows"]))
        self.assertEqual(["Trips/Regatta", "People/Rowan Thackeray"], tags_in(self.loose[0]))
        self.assertEqual(["Trips/Regatta"], tags_in(self.loose[2]))
        self.assertEqual([], tags_in(self.broken))
        self.assert_library_unchanged()
        # No thumbnail, no cache folder, no file beside a photo.
        self.assertEqual(self.files_before, files_under(self.home.root))

    def test_one_photos_suggestions_are_applied_through_its_save_and_change_only_the_file(self):
        self.start(self.cup)
        offer = self.suggested(self.cup)["cup_01.jpg"]
        tags = [t["tag"] for t in offer["tags"]] + [p["name"] for p in offer["people"]]
        reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "", "tags": tags})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(1, reply.get_json()["file_only"])
        self.assertEqual(tags, tags_in(self.loose[0]))
        self.assert_library_unchanged()
        self.assertEqual(self.files_before, files_under(self.home.root))

    def test_the_candidate_words_and_vectors_are_computed_in_memory_not_kept_in_the_library(self):
        """A held folder's run keeps the candidate words' embeddings and the photos' vectors as rows
        (the contrast, in TheSameRunInAFolderHeld...); here they are only in memory."""
        self.start(self.cup)
        self.assertEqual(3, len([p for p in self.clip.images if p in self.loose]), "the photos were analysed")
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM tag_embeddings")[0][0])
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM embeddings")[0][0], "only the library's own")
        self.assert_library_unchanged()


class TheGuardDoesNotRestOnTheRefusalOfRows(Base):
    """For a folder nobody added, ensure_row refuses and the refusal is swallowed, so a looking run
    that kept a vector would pass the dump all the same (#544). These tests give the library every
    reason to accept rows -- the folder is held -- and force the run to look: nothing may be written."""

    def look_at_the_held_folder(self, folder):
        self.before = library_dump.dump(self.library.path)
        counts = self.counts()
        with mock.patch.object(library_actions, "suggest_how", return_value=(None, True)):
            reply = self.start(folder)
        self.assertTrue(reply["in_memory"])
        self.assertEqual("completed", self.status(folder)["status"])
        self.assertEqual(counts, self.counts(), "a photo row or a vector was made")
        self.assert_library_unchanged()

    def test_a_folder_the_library_would_accept_rows_for_gets_none_from_a_looking_run(self):
        library_actions.record_added(self.library, [self.cup])
        self.look_at_the_held_folder(self.cup)

    def test_a_partly_held_folder_whose_held_photo_has_no_vector_is_looked_at_whole_and_saved_nowhere(self):
        mixed = os.path.join(self.home.root, "Share", "Mixed")
        held, loose = os.path.join(mixed, "mix_01.jpg"), os.path.join(mixed, "Sub", "mix_02.jpg")
        damaged_photos.whole_jpeg(held, seed=71)
        damaged_photos.whole_jpeg(loose, seed=72)
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, held, {})   # a row, and no vector under the current model
            conn.commit()
        finally:
            conn.close()
        self.before = library_dump.dump(self.library.path)
        counts = self.counts()
        reply = self.start(mixed)
        self.assertTrue(reply["in_memory"], "a folder holding one the library does not is looked at")
        self.assertEqual(2, len(self.status(mixed)["suggestions"]))
        self.assertEqual(counts, self.counts(), "the held photo was given a vector")
        self.assert_library_unchanged()


class TheSameRunInAFolderHeldSavesAsItAlwaysDid(Base):
    def test_rows_are_made_for_a_held_folder(self):
        self.assertEqual({"success": True, "status": "running", "in_memory": False}, self.start(self.regatta))
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM suggestions")[0][0])
        self.assertGreater(self.rows("SELECT COUNT(*) FROM tag_embeddings")[0][0], 0)
        found = self.status(self.regatta)
        self.assertNotIn("in_memory", found)
        self.assertEqual("completed", found["status"])
        self.assertEqual(0, len(suggestion_jobs.runs_for(self.library).looks))


class TheFolderIsAddedWhileTheRunIsGoing(Base):
    def test_the_run_finishes_in_memory_and_the_next_start_saves(self):
        with mock.patch.object(suggestion_jobs, "threading",
                               types.SimpleNamespace(Lock=threading.Lock, Thread=Deferred)):
            # In flight: the page's start answered "running", and the thread has not run yet.
            self.assertEqual("running", self.start(self.cup)["status"])
            self.assertEqual(1, len(Deferred.waiting))
            library_actions.record_added(self.library, [self.cup])
            # What the library holds now, the folder being its own: the run saves none of it (#544).
            self.before = library_dump.dump(self.library.path)
            photos, vectors = self.counts()
            Deferred.waiting[0].run()
        found = self.status(self.cup)
        self.assertEqual(("completed", True), (found["status"], found["in_memory"]))
        self.assertEqual((photos, vectors), self.counts(), "a photo row or a vector was made")
        self.assert_library_unchanged()
        self.assertEqual([], self.rows("SELECT * FROM suggestions"), "the run was decided in memory at its start")
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM faces WHERE photo_id IN "
                                      "(SELECT id FROM photos WHERE path LIKE '%Harbour Cup%')")[0][0])
        # The folder is the library's now: the next start keeps its results in the library, and drops
        # what was in memory.
        self.assertEqual("running", self.start(self.cup)["status"])
        self.assertEqual(4, self.rows("SELECT COUNT(*) FROM suggestions s JOIN photos p ON p.id = s.photo_id "
                                      "WHERE p.path LIKE '%Harbour Cup%'")[0][0])
        again = self.status(self.cup)
        self.assertNotIn("in_memory", again)
        self.assertEqual({}, suggestion_jobs.runs_for(self.library).looks)


class TwoAtOnce(Base):
    def test_a_second_tab_starting_the_same_folder_starts_no_second_run(self):
        with mock.patch.object(suggestion_jobs, "threading",
                               types.SimpleNamespace(Lock=threading.Lock, Thread=Deferred)):
            self.assertEqual("running", self.start(self.cup)["status"])
            self.assertEqual("preparing", self.start(self.cup)["status"])
            self.assertEqual(1, len(Deferred.waiting))
            Deferred.waiting[0].run()
        self.assertEqual(4, self.status(self.cup)["total"])
        self.assertEqual(3, len(self.clip.images), "each photo was analysed once (the cut one raises before it is counted)")

    def test_two_folders_keep_their_own_results_and_the_oldest_is_dropped_past_the_limit(self):
        folders = []
        for n in range(suggestion_jobs.MAX_LOOKED_FOLDERS + 1):
            folder = os.path.join(self.home.root, "Share", "Set %d" % n)
            damaged_photos.whole_jpeg(os.path.join(folder, "set_0%d.jpg" % n), seed=40 + n)
            folders.append(folder)
            self.start(folder)
        runs = suggestion_jobs.runs_for(self.library)
        self.assertEqual(suggestion_jobs.MAX_LOOKED_FOLDERS, len(runs.looks))
        self.assertEqual("idle", self.status(folders[0])["status"], "the oldest was dropped")
        for each in folders[1:]:
            self.assertEqual(1, len(self.status(each)["suggestions"]))
        self.assert_library_unchanged()

    def test_the_memory_is_let_go_when_unused_and_when_the_library_is_forgotten(self):
        self.start(self.cup)
        self.assertEqual(1, suggestion_jobs.release_looks())
        self.assertEqual("idle", self.status(self.cup)["status"])
        self.start(self.cup)
        suggestion_jobs.forget(self.library)
        self.assertEqual("idle", self.status(self.cup)["status"])

    def test_a_run_whose_library_is_forgotten_meanwhile_finishes_into_nothing(self):
        with mock.patch.object(suggestion_jobs, "threading",
                               types.SimpleNamespace(Lock=threading.Lock, Thread=Deferred)):
            self.start(self.cup)
            suggestion_jobs.forget(self.library)
            Deferred.waiting[0].run()
        self.assertEqual("idle", self.status(self.cup)["status"])
        self.assert_library_unchanged()


class AFailureIsSaidAndLeavesNothingHalfRun(Base):
    def test_models_that_cannot_be_made_ready(self):
        with mock.patch.object(self.runtime, "begin", side_effect=RuntimeError("CUDA out of memory")):
            self.start(self.cup)
        found = self.status(self.cup)
        self.assertEqual("error", found["status"])
        self.assertIn("CUDA out of memory", found["message"])
        self.assertIn("nothing was changed", found["message"])
        self.assertEqual({}, found["suggestions"])
        self.assert_library_unchanged()

    def test_a_model_that_fails_on_every_photo_says_so_and_records_nothing(self):
        self.clip.fail = "CUDA out of memory"
        self.start(self.cup)
        found = self.status(self.cup)
        self.assertEqual("completed", found["status"])
        self.assertEqual(4, found["unread"])
        self.assertTrue(any("CUDA out of memory" in note for note in found["notes"]), found["notes"])
        self.assert_library_unchanged()
        self.assertEqual([], self.rows("SELECT * FROM damaged_files"))
        # Tried again by the next start, not skipped for good.
        self.clip.fail = None
        self.start(self.cup)
        self.assertEqual(3, sum(1 for entry in self.suggested(self.cup).values() if "error" not in entry))

    def test_a_library_that_cannot_answer_whether_it_holds_the_folder_runs_nothing(self):
        import sqlite3
        with mock.patch("tagpup.store.folders.holds", side_effect=sqlite3.OperationalError("database is locked")):
            reply = self.post("/folder/suggest-start", {"folder_path": self.cup})
        self.assertEqual(503, reply.status_code, reply.data)
        self.assertIn("nothing was started", reply.get_json()["error"])
        self.assertEqual("idle", self.status(self.cup)["status"])
        self.assertEqual([], self.clip.images)
        self.assert_library_unchanged()

    def test_an_ignored_folder_is_still_refused(self):
        settings = library_settings.of(self.library)
        self.assertIsNotNone(settings)
        self.assertTrue(library_settings.change(self.library, {library_settings.IGNORED: self.cup}).ok)
        self.before = library_dump.dump(self.library.path)
        reply = self.post("/folder/suggest-start", {"folder_path": self.cup})
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual([], self.clip.images)


class ALibraryWithNothingToCompareWith(Base):
    def test_a_new_library_suggests_nothing_and_says_why(self):
        fresh = os.path.join(self.home.root, "fresh_data")
        os.makedirs(fresh)
        path = self.home.library("fresh.db")
        library_actions.create(path)
        library = Library(path)
        self.addCleanup(self.runtime.forget, library)
        self.addCleanup(suggestion_jobs.forget, library)
        self.addCleanup(tagpup_routes.folders.forget, library)
        before = library_dump.dump(path)
        reply = self.client.post("/fresh/api/folder/suggest-start", json={"folder_path": self.cup})
        self.assertEqual(200, reply.status_code, reply.data)
        found = self.client.get("/fresh/api/folder/suggest-status", query_string={"path": self.cup}).get_json()
        self.assertEqual("completed", found["status"])
        self.assertEqual(2, len(found["notes"]) - (1 if found["unread"] else 0), found["notes"])
        self.assertTrue(any("no photo vectors" in note for note in found["notes"]))
        self.assertTrue(any("no named faces" in note for note in found["notes"]))
        for name, entry in found["suggestions"].items():
            if "error" not in entry:
                self.assertEqual(([], []), (entry["tags"], entry["people"]), name)
        self.assertEqual(before, library_dump.dump(path))


class ABigFolderIsCapped(Base):
    def test_only_so_many_photos_are_kept_and_the_status_says_which_were_left_out(self):
        with mock.patch.object(suggestion_jobs, "MAX_LOOKED_PHOTOS", 2):
            self.start(self.cup)
            found = self.status(self.cup)
            self.assertEqual(2, len(found["suggestions"]))
            self.assertEqual((2, 2), (found["total"], found["completed"]))
            self.assertTrue(any("left out" in note for note in found["notes"]), found["notes"])
            self.start(self.cup)
            self.assertEqual(2, len(self.status(self.cup)["suggestions"]), "a second start does not grow it")
        self.assert_library_unchanged()


class SuggestedTwice(Base):
    def test_the_second_run_analyses_nothing_again_and_applying_twice_writes_once(self):
        self.start(self.cup)
        seen = list(self.clip.images)
        self.start(self.cup)
        self.assertEqual(seen, self.clip.images, "no photo was analysed again")
        first = self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0})
        again = self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0})
        self.assertEqual((200, 200), (first.status_code, again.status_code))
        self.assertEqual((3, 0), (first.get_json()["file_only"], again.get_json()["file_only"]))
        self.assertEqual(["Trips/Regatta", "People/Rowan Thackeray"], tags_in(self.loose[0]))
        self.assert_library_unchanged()


class WhatIsAppliedIsWhatTheFileHoldsNow(Base):
    def test_a_tag_another_program_added_meanwhile_stays(self):
        self.start(self.cup)
        write_into(self.loose[1], **{"XMP:Subject": ["From Elsewhere"]})
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        self.assertEqual(["From Elsewhere", "Trips/Regatta", "People/Rowan Thackeray"], tags_in(self.loose[1]))
        self.assert_library_unchanged()

    def test_a_person_the_tree_does_not_hold_is_filed_under_the_people_root_and_the_tree_is_untouched(self):
        conn = db.connect(self.library.path)
        try:
            store_faces.insert(conn, self.held[1], [0, 0, 100, 100], WREN.tobytes(), name="Wren Okafor", prob=0.99)
            conn.commit()
        finally:
            conn.close()
        self.before = library_dump.dump(self.library.path)
        tree = tree_of(self.library.path)
        self.start(self.cup)
        offer = self.suggested(self.cup)["cup_03.jpg"]
        self.assertEqual(["Wren Okafor"], [p["name"] for p in offer["people"]])
        self.assertEqual(200, self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0}).status_code)
        # As a click on the chip files her (#546): under the one people root, never bare.
        self.assertEqual(["Trips/Regatta", "People/Wren Okafor"], tags_in(self.loose[2]))
        self.assertEqual(tree, tree_of(self.library.path))
        self.assert_library_unchanged()

    def test_a_folder_added_after_the_run_is_applied_with_its_rows_as_a_held_folder_is(self):
        self.start(self.cup)
        library_actions.record_added(self.library, [self.cup])
        reply = self.post("/folder/auto-apply", {"folder_path": self.cup, "threshold": 0.0})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((0, 3), (reply.get_json()["file_only"], reply.get_json()["with_rows"]))


class WhatStaysRefused(Base):
    def test_auto_apply_with_nothing_suggested_is_still_400(self):
        reply = self.post("/folder/auto-apply", {"folder_path": self.cup})
        self.assertEqual(400, reply.status_code)
        self.assert_library_unchanged()


if __name__ == "__main__":
    unittest.main()

"""The owner retires the photo vectors of the model a library is no longer on (TagPup CLI: retire-photo-vectors,
tagpup.services.retire_photo_vectors), after the new model's vectors were made (embed-photos) and the library switched
to it (set-photo-model): photo_index holds about 244 MB of them that nothing reads. What the owner lives through:

- The dry run names the library's own key, how many photos hold a vector under it, and each other key's vectors and
  words that would go; nothing is written.
- Applied, the vectors under every other key go, and the words Suggest cached under the old model; the own key's
  vectors and the own model's words stay byte for byte. Each key is one change of the journal, its undo refused
  (retire_faces.VECTORS_RETIRED), and the undo of the switch away from the old model is refused in History with the
  switch's own sentence: no photo holds a vector under it any more.
- Stopped after a chunk (Ctrl+C), or by an app started during the run, the chunks written stand; a run again continues
  as the same change, the words gone once.
- A switch back beside the run (an undo of the switch from History) ends it at the next chunk, nothing of it written.
- A photo removed meanwhile (a sync) takes its vectors with it, and the run counts only what it deleted.
- A library whose own model does not hold its photos' vectors (rotated since, each rotation taking the vectors away) is
  refused, nothing written: the vectors that would go are the most complete it holds.

Fictional names; a library in a home of the test's own; rows as the indexer stores them; real small JPEGs; vectors as
the photo models' writers keep them (store.embeddings.put_by_id).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

import numpy as np  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photo_reembed  # noqa: E402
from tagpup.services import retire_faces  # noqa: E402
from tagpup.services import retire_photo_vectors  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402

OLD = ("ViT-H-14", "laion2b_s32b_b79k")
NEW = ("ViT-SO400M-14-SigLIP2", "webli")
OLD_KEY = store_embeddings.model_key(*OLD, True, 1.4, 512)
NEW_KEY = store_embeddings.model_key(*NEW, True, 1.4, None)
THIRD_KEY = store_embeddings.model_key("ViT-B-32", "openai", False, 1.0, 224)


def vector(dim, seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tobytes()


def rows_of(db_path):
    """Every row of every table of the library, read-only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                  if not name.startswith("sqlite_")]
        return {table: sorted(map(repr, conn.execute('SELECT * FROM "%s"' % table))) for table in tables}
    finally:
        conn.close()


def read(db_path, sql, params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def said(result):
    return " ".join(result.output.split())


class _Library(unittest.TestCase):
    """A library of ten photos read by the index, each holding ViT-H's vector and SigLIP 2's, three a third model's;
    Suggest's words cached under ViT-H (two words, two prompts each) and SigLIP 2 (one); switched to SigLIP 2."""

    switch = True

    def setUp(self):
        self.home = own_home.for_test(self, prefix="retire_photo_vectors_")
        self.library = Library(self.home.library("harbour.db"))
        self.assertEqual(1, library_actions.create(self.library.path).changed)
        self.folder = paths.stored(os.path.join(self.home.root, "Heronry"))
        os.makedirs(self.folder)
        library_actions.record_added(self.library, [self.folder])
        self.photos = []
        conn = db.connect(self.library.path)
        try:
            for n in range(10):
                path = os.path.join(self.folder, "heron %02d.jpg" % n)
                Image.new("RGB", (48, 32), (5 * n % 255, 90, 140)).save(path, "JPEG")
                photo_id = photo_rows.add_read(conn, path, {"XMP:Subject": ["Birds/Heron"]})
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put_by_id(conn, photo_id, OLD_KEY, mtime, size, vector(1024, n))
                store_embeddings.put_by_id(conn, photo_id, NEW_KEY, mtime, size, vector(1152, n))
                if n < 3:
                    store_embeddings.put_by_id(conn, photo_id, THIRD_KEY, mtime, size, vector(512, n))
                self.photos.append(path)
            for word in ("Heron", "Harbour"):
                for prompt in ("a photo of %s" % word, "a %s, 1998" % word):
                    store_taxonomy.keep_tag_embedding(conn, word, prompt, *OLD, vector(1024, 3))
            store_taxonomy.keep_tag_embedding(conn, "Heron", "a photo of Heron", *NEW, vector(1152, 3))
            conn.commit()
        finally:
            conn.close()
        self.switch_change = None
        if self.switch:
            before = self.changes()
            switched = photo_reembed.switch(self.library, NEW, apply=True)
            self.assertFalse(switched.refused, switched.refused)
            (self.switch_change,) = [change for change in self.changes() if change not in before]

    def changes(self):
        return [change for (change,) in read(self.library.path, "SELECT id FROM changes ORDER BY id")]

    def vectors(self, key):
        return read(self.library.path, "SELECT photo_id, mtime, size, vector FROM embeddings WHERE model = ?"
                                       " ORDER BY photo_id", (key,))

    def words(self, model):
        return read(self.library.path, "SELECT tag, prompt, embedding FROM tag_embeddings WHERE model_name = ? AND"
                                       " pretrained = ? ORDER BY tag, prompt", model)

    def plans(self):
        own, held, plans = retire_photo_vectors.plan(self.library)
        self.assertEqual(NEW_KEY, own)
        return held, plans

    def retiring_changes(self):
        return {key: [change for (change,) in read(self.library.path, "SELECT id FROM changes WHERE operation = ?"
                                                                      " ORDER BY id", (retire_faces.VECTORS_OPERATION % key,))]
                for key in (OLD_KEY, THIRD_KEY)}

    def cli(self, *args):
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 4):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "retire-photo-vectors"] + list(args))


class TheDryRun(_Library):

    def test_the_dry_run_names_the_own_key_and_what_goes_and_writes_nothing(self):
        before = rows_of(self.library.path)
        ran = self.cli()
        self.assertEqual(0, ran.exit_code, ran.output)
        text = said(ran)
        self.assertIn("The library's photo model: %s. %s" % (NEW_KEY, library_settings.PHOTOS_HELD % (10, 10, NEW_KEY, 0)),
                      text)
        self.assertIn("3 vectors under %s" % THIRD_KEY, text)
        self.assertIn("10 vectors under %s" % OLD_KEY, text)
        self.assertIn("2 words Suggest cached under %s:%s (4 rows) are deleted." % OLD, text)
        self.assertIn(" ".join(tagpup_cli.VECTORS_DRY_RUN.split()), text)
        self.assertEqual(before, rows_of(self.library.path), "the dry run wrote")


class RetiredWhole(_Library):

    def test_the_other_keys_go_the_own_stays_byte_for_byte_and_neither_undo_stands(self):
        own_vectors, own_words = self.vectors(NEW_KEY), self.words(NEW)
        _held, plans = self.plans()
        self.assertEqual([THIRD_KEY, OLD_KEY], [planned.key for planned in plans])
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 4):
            retired = [retire_photo_vectors.apply(self.library, planned) for planned in plans]
        self.assertEqual([3, 10], [done.deleted for done in retired])
        self.assertEqual([0, 4], [done.word_rows for done in retired])
        self.assertIn("10 vectors under %s deleted" % OLD_KEY, retired[1].said())
        self.assertIn("4 rows of words cached under %s:%s deleted." % OLD, retired[1].said())
        self.assertEqual([], self.vectors(OLD_KEY) + self.vectors(THIRD_KEY) + self.words(OLD))
        self.assertEqual(own_vectors, self.vectors(NEW_KEY), "the own key's vectors moved")
        self.assertEqual(own_words, self.words(NEW), "the own model's words moved")
        changes = self.retiring_changes()
        self.assertEqual({OLD_KEY: [retired[1].change_id], THIRD_KEY: [retired[0].change_id]}, changes)
        (summary,) = read(self.library.path, "SELECT summary FROM changes WHERE id = ?", (retired[1].change_id,))
        self.assertIn('"deleted": 10', summary[0])
        self.assertIn('"words": 4', summary[0])
        self.assertEqual(retired[1].change_id, max(self.changes()), "a change recorded with no write")

        listed = {entry["id"]: entry["why_not"]
                  for entry in journal_service.history(self.library, limit=10)["changes"]}
        self.assertEqual(retire_faces.VECTORS_RETIRED % OLD_KEY, listed[retired[1].change_id])
        self.assertEqual(library_settings.TOO_FEW_PHOTO_VECTORS % (library_settings.PHOTOS_HELD % (0, 10, OLD_KEY, 10), 5),
                         listed[self.switch_change])
        before = rows_of(self.library.path)
        for change in (retired[1].change_id, self.switch_change):
            undone = journal_service.undo(self.library, change, apply=True)
            self.assertTrue(undone.refused, "change %d was undone" % change)
        self.assertEqual(before, rows_of(self.library.path), "a refused undo wrote")


class StoppedAndRunAgain(_Library):

    def test_a_run_stopped_after_a_chunk_continues_as_the_same_change(self):
        _held, plans = self.plans()
        old = plans[1]
        heard = []
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 4):
            first = retire_photo_vectors.apply(self.library, old, stop=lambda: bool(heard),
                                               heard=lambda chunk, chunks: heard.append((chunk.deleted, chunks)))
        self.assertEqual([(4, 3)], heard)
        self.assertTrue(first.stopped)
        self.assertEqual((4, 4), (first.deleted, first.word_rows))
        self.assertEqual(6, len(self.vectors(OLD_KEY)))
        self.assertEqual([], self.words(OLD))
        _held, plans = self.plans()
        self.assertEqual(first.change_id, plans[1].continues)
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 4):
            second = retire_photo_vectors.apply(self.library, plans[1])
        self.assertEqual((6, 0, False), (second.deleted, second.word_rows, second.stopped))
        self.assertEqual(first.change_id, second.change_id)
        self.assertEqual([first.change_id], self.retiring_changes()[OLD_KEY])
        (summary,) = read(self.library.path, "SELECT summary FROM changes WHERE id = ?", (first.change_id,))
        self.assertIn('"deleted": 10', summary[0])
        self.assertIn('"words": 4', summary[0])

    def test_from_the_command_line_an_app_started_during_the_run_stops_it_and_a_run_again_finishes(self):
        deleting = store_embeddings.delete_between
        started = []

        def the_app_starts_in_the_old_keys_first_chunk(conn, key, after, last):
            if key == OLD_KEY and not started:
                started.append(True)
                launcher.say_where({"main": 5918}, "test", "token")   # this process, as a live server records itself
                self.addCleanup(launcher.forget, {"main": 5918})
            return deleting(conn, key, after, last)

        with mock.patch.object(store_embeddings, "delete_between", the_app_starts_in_the_old_keys_first_chunk):
            first = self.cli("--apply")
        self.assertEqual(1, first.exit_code, first.output)
        self.assertIn("Chunk 1 of 1: 3 vectors deleted.", first.output)
        self.assertIn("Chunk 1 of 3: 4 vectors deleted.", first.output)
        self.assertNotIn("Chunk 2 of 3", first.output)
        self.assertIn(" ".join((tagpup_cli.APP_STARTED % 1).split()), said(first))
        self.assertNotIn("no longer possible", said(first))
        self.assertEqual(6, len(self.vectors(OLD_KEY)), "the next chunk was begun")
        launcher.forget({"main": 5918})
        second = self.cli("--apply")
        self.assertEqual(0, second.exit_code, second.output)
        self.assertIn("Continuing change %d" % self.retiring_changes()[OLD_KEY][0], said(second))
        self.assertEqual([], self.vectors(OLD_KEY) + self.vectors(THIRD_KEY) + self.words(OLD))
        self.assertEqual({OLD_KEY: 1, THIRD_KEY: 1}, {key: len(found) for key, found in self.retiring_changes().items()})
        self.assertIn("The switch back to %s is no longer possible" % OLD_KEY, said(second))


class BesideTheRun(_Library):

    def test_a_switch_back_between_two_chunks_ends_the_run_nothing_of_the_next_written(self):
        _held, plans = self.plans()

        def undone_from_history(chunk, chunks):
            undone = journal_service.undo(self.library, self.switch_change, apply=True)
            self.assertFalse(undone.refused, undone.refused)

        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 2):
            retired = retire_photo_vectors.apply(self.library, plans[0], heard=undone_from_history)
        self.assertEqual((retire_faces.MODEL_MOVED, 2), (retired.error, retired.deleted))
        self.assertIn(retire_faces.SWITCH_BACK % NEW_KEY, retired.said())
        self.assertEqual(1, len(self.vectors(THIRD_KEY)), "the next chunk was written")

    def test_a_photo_removed_meanwhile_takes_its_vectors_and_the_run_counts_what_it_deleted(self):
        _held, plans = self.plans()
        removed = self.photos[9]

        def a_sync_removes_it(chunk, chunks):
            if chunk.deleted and not getattr(a_sync_removes_it, "done", False):
                a_sync_removes_it.done = True
                self.assertEqual(1, store_photos.forget_photo(self.library.path, removed)["photos"])

        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 4):
            retired = retire_photo_vectors.apply(self.library, plans[1], heard=a_sync_removes_it)
        self.assertEqual((9, None), (retired.deleted, retired.error))
        self.assertIn("9 vectors under %s deleted" % OLD_KEY, retired.said())
        self.assertEqual([], self.vectors(OLD_KEY))
        self.assertEqual(9, len(self.vectors(NEW_KEY)))


class NotOnItsOwnModel(_Library):

    switch = False

    def test_a_library_whose_own_model_does_not_hold_its_photos_vectors_is_refused(self):
        # Six photos rotated since their vectors were made: each rotation takes the photo's vectors away.
        conn = db.connect(self.library.path)
        try:
            for path in self.photos[4:]:
                store_embeddings.forget(conn, path)
            conn.commit()
        finally:
            conn.close()
        before = rows_of(self.library.path)
        own, held, plans = retire_photo_vectors.plan(self.library)
        self.assertEqual((OLD_KEY, (10, 4)), (own, held))
        refusal = retire_photo_vectors.NOT_COVERED % (library_settings.PHOTOS_HELD % (4, 10, OLD_KEY, 6), 5)
        self.assertEqual([refusal, refusal], [planned.refused for planned in plans])
        ran = self.cli("--apply")
        self.assertEqual(1, ran.exit_code, ran.output)
        self.assertIn(" ".join(refusal.split()), said(ran))
        for planned in plans:
            retire_photo_vectors.apply(self.library, planned)
        self.assertEqual(before, rows_of(self.library.path), "a refused retiring wrote")


if __name__ == "__main__":
    unittest.main()

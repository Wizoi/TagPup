"""A library switches to SigLIP 2 SO400M (model.name ViT-SO400M-14-SigLIP2, weights webli; docs/AI_PIPELINE.md, phase 5)
once the new model's vectors are made for its photos, and each library then searches by its own model's vectors alone.

Every photo vector the three libraries held was ViT-H-14's; the new model's are written beside them, each kept under its
settings' key (tagpup.store.embeddings.model_key), and every reader reads the library's key alone. A switch before the
new vectors are made would leave Suggest and search with nothing to compare, so tagpup.services.settings.change refuses
it, saying the counts (photo_switch_on), as it refuses the face model's switch (tests/test_switching_a_librarys_face_
model.py). What the owner lives through:

- (s) The switch in the gear: a library 40% of whose photos hold the new model's vector is refused, with the counts and
  what to do, nothing written, nor in a dry run; at 97% and at 100% it switches. SO400M with Image size still 512 (the
  default) is refused: the model is shown photos at its own size only. A model name typed that the table does not hold
  (a sibling of SO400M) is refused, saying the pairs it holds, even with every photo holding a vector under it; the page
  is told the counts of a switch it allows (photo_vectors).
- (u) The switch undone from History: refused while the photos added since hold the new model's vector alone, too many
  to go back (the same share), in the listing, the rehearsal and the undo alike, nothing written; allowed when they are
  few enough.
- (r) Two libraries on two models in one server: each searches its own vectors (1024 long under ViT-H, 1152 under
  SO400M), never the other's; a switch while a Suggest holds its index leaves the run on the model it began with, and
  the next ask is on the new one.
- (t) SigLIP 2's tokenizer is read from its tokenizer.json; one not in the cache, with no network, is one sentence;
  ViT-H-14's is still open_clip's own.

The word cut each model's words are held to is the suggester's (tests/test_suggester_scoring.py). Fictional names;
libraries in a home of the test's own; rows as the indexer stores them; stand-in models, no weights.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

import web_client  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402

OLD = ("ViT-H-14", "laion2b_s32b_b79k")
NEW = ("ViT-SO400M-14-SigLIP2", "webli")
OLD_KEY = store_embeddings.model_key(*OLD, True, 1.4, 512)
NEW_KEY = store_embeddings.model_key(*NEW, True, 1.4, None)
#: How long each model's vectors are (a 1024-d ViT-H, a 1152-d SO400M).
DIMS = {OLD[0]: 1024, NEW[0]: 1152}
#: The gear's switch to SO400M: Image size emptied with it.
TO_NEW = {"model.name": NEW[0], "model.pretrained": NEW[1], "model.force_image_size": ""}


def rows_of(db_path):
    """Every row of every table of the library, read-only: what a refused write must leave as it was."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                  if not name.startswith("sqlite_")]
        return {table: sorted(map(repr, conn.execute('SELECT * FROM "%s"' % table))) for table in tables}
    finally:
        conn.close()


def held(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


def vector(dim, seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def switch(library, putting=TO_NEW, **kwargs):
    return library_settings.change(library, dict(putting), acknowledged=["clip"], **kwargs)


def refused_for_few(holds, photos, key):
    return library_settings.TOO_FEW_PHOTO_VECTORS % (library_settings.PHOTOS_HELD % (holds, photos, key, photos - holds),
                                                     5)


class _Libraries(unittest.TestCase):
    """Libraries as the picker's Create makes them, each a folder added in the gear and photos read by the index, every
    one holding ViT-H's vector; the new model's given to as many as a test says."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="new_photo_model_")
        self.seed = 0

    def library(self, file_name, folder_name, count):
        library = Library(self.home.library(file_name))
        self.assertEqual(1, library_actions.create(library.path).changed)
        folder = paths.stored(os.path.join(self.home.root, folder_name))
        os.makedirs(folder)
        library_actions.record_added(library, [folder])
        library.photo_folder = folder
        library.photos = []
        self.add_photos(library, count, old=True)
        return library

    def add_photos(self, library, count, old=True, new=False):
        """`count` photos more in the library's folder, read by the index; each given ViT-H's vector with `old`, and
        SO400M's with `new`, as PhotoEmbeddings.keep stores them (stamped with the file)."""
        added = []
        conn = db.connect(library.path)
        try:
            for _ in range(count):
                n = len(library.photos)
                path = os.path.join(library.photo_folder, "heron %02d.jpg" % n)
                Image.new("RGB", (48, 32), (5 * n % 255, 90, 140)).save(path, "JPEG")
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Birds/Heron"]})
                library.photos.append(path)
                added.append(path)
            conn.commit()
        finally:
            conn.close()
        if old:
            self.give(library, added, OLD)
        if new:
            self.give(library, added, NEW)
        return added

    def give(self, library, photos, model):
        key = OLD_KEY if model == OLD else NEW_KEY
        conn = db.connect(library.path)
        try:
            for path in photos:
                self.seed += 1
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, key, mtime, size, vector(DIMS[model[0]], self.seed).tobytes())
            conn.commit()
        finally:
            conn.close()


class TheSwitchWaitsForTheVectors(_Libraries):
    """(s) The owner switches a library to SO400M in the gear (settings.change, as the dialog's route calls it)."""

    def test_a_library_forty_percent_of_whose_photos_hold_the_new_vector_is_refused_and_nothing_written(self):
        harbour = self.library("harbour.db", "Heronry", 30)
        self.give(harbour, harbour.photos[:12], NEW)
        before = rows_of(harbour.path)
        for apply in (False, True):
            result = switch(harbour, apply=apply)
            self.assertEqual(refused_for_few(12, 30, NEW_KEY), result.refused)
            self.assertEqual("Nothing was written: 12 of the library's 30 photos hold a vector under %s; 18 hold none. A "
                             "switch allows at most 5%% of them without one: make the new model's vectors first."
                             % NEW_KEY, result.refused)
            self.assertEqual({"key": NEW_KEY, "holds": 12, "without": 18}, {
                k: result.details["photo_vectors"][k] for k in ("key", "holds", "without")})
            self.assertEqual(before, rows_of(harbour.path), "a refused switch wrote")
        self.assertEqual(OLD[0], held(harbour)["model.name"])

    def test_a_library_nearly_or_wholly_covered_switches(self):
        for name, holding in (("harbour.db", 29), ("cove.db", 30)):   # 97% and 100%
            library = self.library(name, name[:-3].title(), 30)
            self.give(library, library.photos[:holding], NEW)
            result = switch(library)
            self.assertFalse(result.refused, result.refused)
            self.assertEqual({"key": NEW_KEY, "holds": holding, "without": 30 - holding}, {
                k: result.details["photo_vectors"][k] for k in ("key", "holds", "without")})
            settings_now = held(library)
            self.assertEqual((NEW[0], NEW[1], ""), (settings_now["model.name"], settings_now["model.pretrained"],
                                                     settings_now["model.force_image_size"]))
            self.assertEqual(NEW_KEY, store_embeddings.model_key(
                **library_settings.LibrarySettings(library_settings.DEFAULTS | settings_now).embedder))

    def test_so400m_with_image_size_still_512_is_refused(self):
        harbour = self.library("harbour.db", "Heronry", 3)
        self.give(harbour, harbour.photos, NEW)
        before = rows_of(harbour.path)
        for apply in (False, True):
            result = switch(harbour, {"model.name": NEW[0], "model.pretrained": NEW[1]}, apply=apply)
            self.assertEqual("Nothing was written: ViT-SO400M-14-SigLIP2 (weights webli) is shown photos at its own size "
                             "only: leave Image size empty.", result.refused)
            self.assertEqual(before, rows_of(harbour.path))


    def test_a_model_the_table_does_not_hold_is_refused_saying_which_it_holds(self):
        harbour = self.library("harbour.db", "Heronry", 3)
        sibling = ("ViT-SO400M-14-SigLIP2-378", "webli")
        key = store_embeddings.model_key(*sibling, True, 1.4, None)
        conn = db.connect(harbour.path)
        try:
            for path in harbour.photos:
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, key, mtime, size, vector(1152, 7).tobytes())
            conn.commit()
        finally:
            conn.close()
        before = rows_of(harbour.path)
        for apply in (False, True):
            result = switch(harbour, {"model.name": sibling[0], "model.pretrained": sibling[1],
                                      "model.force_image_size": ""}, apply=apply)
            self.assertEqual("Nothing was written: ViT-SO400M-14-SigLIP2-378 (weights webli) is not a photo model TagPup "
                             "holds the word cut of. It holds: ViT-H-14 (laion2b_s32b_b79k), ViT-SO400M-14-SigLIP2 "
                             "(webli).", result.refused)
            self.assertEqual(before, rows_of(harbour.path))

    def test_the_page_is_told_the_counts_of_a_switch_it_is_allowed(self):
        app, home = web_client.app_for(self, "tuner", startup="harbour.db")
        reply = app.test_client().post("/harbour/api/settings", json={"values": TO_NEW, "acknowledged": ["clip"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        counts = reply.get_json()["photo_vectors"]
        self.assertEqual({"key": NEW_KEY, "holds": 0, "without": 0}, {k: counts[k] for k in ("key", "holds", "without")})
        self.assertIn(NEW_KEY, counts["said"])


class TheUndoOfASwitchIsASwitch(_Libraries):
    """(u) The owner switched harbour to SO400M with every photo holding both vectors, indexed photos since (under the
    new model alone), and clicks Undo on the switch in History."""

    def setUp(self):
        super().setUp()
        self.harbour = self.library("harbour.db", "Heronry", 30)
        self.give(self.harbour, self.harbour.photos, NEW)
        result = switch(self.harbour)
        self.assertFalse(result.refused, result.refused)
        self.change = journal_service.history(self.harbour, limit=1)["changes"][0]["id"]

    def why_not(self):
        return journal_service.history(self.harbour, change_id=self.change)["changes"][0]["why_not"]

    def test_refused_while_too_many_photos_since_hold_the_new_vector_alone(self):
        self.add_photos(self.harbour, 2, old=False, new=True)   # 2 of 32 hold no ViT-H vector: over 5%
        before = rows_of(self.harbour.path)
        said = refused_for_few(30, 32, OLD_KEY)
        self.assertEqual(said, self.why_not())
        self.assertEqual(said, journal_service.rehearse(self.harbour, self.change).refused)
        self.assertEqual(said, journal_service.undo(self.harbour, self.change, apply=True).refused)
        self.assertEqual(before, rows_of(self.harbour.path), "a refused undo wrote")
        self.assertEqual(NEW[0], held(self.harbour)["model.name"])

    def test_the_listing_reads_the_journal_once_for_each_row_of_history(self):
        with mock.patch("tagpup.services.settings.journal.old_values", wraps=library_settings.journal.old_values) as read:
            self.why_not()
        self.assertEqual(1, read.call_count)

    def test_allowed_when_few_enough_hold_the_new_vector_alone(self):
        self.add_photos(self.harbour, 1, old=False, new=True)   # 1 of 31: inside 5%
        self.assertIsNone(self.why_not())
        undone = journal_service.undo(self.harbour, self.change, apply=True)
        self.assertFalse(undone.refused, undone.refused)
        settings_now = held(self.harbour)
        self.assertEqual((OLD[0], OLD[1], "512"), (settings_now["model.name"], settings_now["model.pretrained"],
                                                    settings_now["model.force_image_size"]))


class _Clip:
    """The photo model the runtime builds from a library's settings: a word embedded to a vector as long as the model's."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.model_name, self.pretrained = self.settings["model_name"], self.settings["pretrained"]
        self.dim = DIMS[self.model_name]

    def embed_text(self, text):
        return vector(self.dim, len(text)).tolist()

    def unload(self):
        pass


class _Run:
    """What model_for_run makes for a run: what it was handed."""

    def __init__(self, photo_index, clip, faces, words, **kwargs):
        self.photo_index, self.clip = photo_index, clip
        self.model_key = photo_index.model


class EachLibrarySearchesItsOwnVectors(_Libraries):
    """(r) harbour on ViT-H-14 and cove switched to SO400M, in one server."""

    def setUp(self):
        super().setUp()
        self.harbour = self.library("harbour.db", "Heronry", 4)
        self.cove = self.library("cove.db", "Egrets", 3)
        self.give(self.cove, self.cove.photos, NEW)
        self.assertFalse(switch(self.cove).refused)
        self.runtime = Runtime(build_clip=_Clip, build_faces=lambda settings: mock.Mock())
        for library in (self.harbour, self.cove):
            self.addCleanup(self.runtime.forget, library)

    def test_each_library_is_searched_by_its_own_models_vectors(self):
        for library, key, dim in ((self.harbour, OLD_KEY, 1024), (self.cove, NEW_KEY, 1152)):
            index = self.runtime.photo_index(library)
            self.assertEqual((key, dim, len(library.photos), len(library.photos)),
                             (index.model, index.dim, index.indexed, index.count))
            embeddings = self.runtime.embeddings(library, index)
            self.assertEqual(key, embeddings.model_key)
            self.assertEqual(dim, len(embeddings.cached(library.photos[0])))
            found = index.search(vector(dim, 99).tolist(), k=10)
            self.assertEqual(sorted(paths.key(p) for p in library.photos),
                             sorted(paths.key(record["path"]) for _sim, record in found))

    def test_a_switch_while_a_run_holds_its_index_leaves_the_run_on_its_model(self):
        self.give(self.harbour, self.harbour.photos, NEW)
        with mock.patch("tagpup.services.suggester.model_for_run", _Run):
            run = self.runtime.begin(self.harbour)
        try:
            self.assertFalse(switch(self.harbour).refused)
            self.runtime.settings_changed(self.harbour)
            self.assertEqual((OLD_KEY, 1024), (run.photo_index.model, run.photo_index.dim))
            self.assertEqual(OLD[0], run.clip.settings["model_name"])
        finally:
            run.end()
        now = self.runtime.photo_index(self.harbour)
        self.assertEqual((NEW_KEY, 1152, 4), (now.model, now.dim, now.indexed))
        self.assertEqual(NEW[0], self.runtime.clip(self.harbour).settings["model_name"])


class SigLIP2sTokenizer(unittest.TestCase):
    """(t) The tokenizer a photo model reads its words with (tagpup.ml.clip.tokenizer_of)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="photo_tokenizer_")

    def tiny_tokenizer(self):
        """A tokenizer.json and special_tokens_map.json of a few words, as the Hugging Face cache holds SigLIP 2's."""
        from tokenizers import Tokenizer, models, pre_tokenizers
        words = {"<pad>": 0, "<unk>": 1, "a": 2, "photo": 3, "of": 4, "heron": 5}
        tokenizer = Tokenizer(models.WordLevel(words, unk_token="<unk>"))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        folder = os.path.join(self.home.root, "hub")
        os.makedirs(folder)
        made = {"tokenizer.json": os.path.join(folder, "tokenizer.json"),
                "special_tokens_map.json": os.path.join(folder, "special_tokens_map.json")}
        tokenizer.save(made["tokenizer.json"])
        with open(made["special_tokens_map.json"], "w", encoding="utf-8") as handle:
            json.dump({"pad_token": {"content": "<pad>"}}, handle)
        return made

    def test_siglip2_reads_its_tokenizer_json(self):
        from tagpup.ml import clip
        made = self.tiny_tokenizer()
        with mock.patch("huggingface_hub.hf_hub_download", side_effect=lambda repo, name: made[name]) as download:
            tokenizer = clip.tokenizer_of(NEW[0])
        self.assertEqual({"timm/ViT-SO400M-14-SigLIP2"}, {call.args[0] for call in download.call_args_list})
        ids = tokenizer(["a photo of a heron"])
        self.assertEqual((1, 64), tuple(ids.shape))
        self.assertEqual([2, 3, 4, 2, 5, 0], ids[0][:6].tolist())

    def test_a_tokenizer_not_in_the_cache_offline_is_one_sentence(self):
        from tagpup.ml import clip
        with mock.patch("huggingface_hub.hf_hub_download", side_effect=OSError("offline")), \
                self.assertLogs("tagpup_cli.embedder", "ERROR"):
            with self.assertRaises(RuntimeError) as raised:
                clip.tokenizer_of(NEW[0])
        self.assertEqual("The tokenizer of ViT-SO400M-14-SigLIP2 is not in the Hugging Face cache and could not be "
                         "downloaded from timm/ViT-SO400M-14-SigLIP2 (OSError): connect to the internet once, or copy "
                         "the cache from a machine that has it.", str(raised.exception))

    def test_vit_h_keeps_open_clips_tokenizer(self):
        import open_clip
        from tagpup.ml import clip
        with mock.patch("huggingface_hub.hf_hub_download") as download:
            tokenizer = clip.tokenizer_of(OLD[0])
        download.assert_not_called()
        self.assertIsInstance(tokenizer, open_clip.tokenizer.SimpleTokenizer)


if __name__ == "__main__":
    unittest.main()

"""The owner makes SigLIP 2 SO400M's vectors for a library (TagPup CLI: embed-photos --model ViT-SO400M-14-SigLIP2:webli,
tagpup.services.photo_reembed) before switching it in the gear (tests/test_switching_a_librarys_photo_model.py, whose
gate this run is made to pass). About 40 minutes for photo_index, so it is stopped and run again, and the library is in
use while it runs. What the owner lives through:

- The dry run counts the photos, those holding the new vector, those to embed and those with no file, and the time;
  nothing is written.
- A run stopped after a batch keeps what it made; the next makes only the rest, nothing twice. While it runs the
  library's model, key and settings are as they were, and a reader of the library's key finds no new vector.
- A photo removed while the run is under way is skipped and never given a row back.
- A file that does not decode is counted, and tried again by the next run.
- At 40% the gear's switch is still refused; after a full run, from the command line, it passes.
- A run waits for its turn on the graphics card before it reads what is left: one that waited behind another run finds
  what that run made.

Fictional names; a library in a home of the test's own; rows as the indexer stores them; real small JPEGs; a stand-in
photo model (1152-long vectors), no weights.
"""
import os
import sys
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

import numpy as np  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photo_reembed  # noqa: E402
from tagpup.services import search  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

OLD = ("ViT-H-14", "laion2b_s32b_b79k")
NEW = ("ViT-SO400M-14-SigLIP2", "webli")
OLD_KEY = store_embeddings.model_key(*OLD, True, 1.4, 512)
NEW_KEY = store_embeddings.model_key(*NEW, True, 1.4, None)
DIMS = {OLD[0]: 1024, NEW[0]: 1152}
TO_NEW = {"model.name": NEW[0], "model.pretrained": NEW[1], "model.force_image_size": ""}


def vector(dim, seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


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


class _Clip:
    """The photo model the runtime builds from settings: it decodes the photo as the real one does (images.opened, which
    raises images.Unreadable), and embeds it to a vector as long as the model's. Each forward and the load are noted."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.device = "cpu"
        self.dim = DIMS[self.settings["model_name"]]
        self.events, self.forwards = [], []
        self.on_forward = None

    def load(self):
        self.events.append("load")

    def unload(self):
        pass

    def input_of(self, path, seen=None, load=True):
        assert not load, "a read ahead never loads the model"
        images.opened(path, upright=True).close()
        return path

    def embed_input(self, path):
        self.forwards.append(paths.key(path))
        if self.on_forward:
            self.on_forward(len(self.forwards))
        return vector(self.dim, zlib.crc32(paths.key(path).encode())).tolist()


class _Library(unittest.TestCase):
    """A library as the picker's Create makes it, a folder added in the gear and its photos read by the index, each
    holding ViT-H's vector."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="embed_photos_")
        self.library = Library(self.home.library("harbour.db"))
        self.assertEqual(1, library_actions.create(self.library.path).changed)
        self.folder = paths.stored(os.path.join(self.home.root, "Heronry"))
        os.makedirs(self.folder)
        library_actions.record_added(self.library, [self.folder])
        self.photos = []
        self.add_photos(10)
        self.clips = []
        self.runtime = Runtime(build_clip=self.build_clip, build_faces=lambda settings: mock.Mock())
        self.addCleanup(self.runtime.forget, self.library)

    def build_clip(self, settings):
        clip = _Clip(settings)
        self.clips.append(clip)
        return clip

    def add_photos(self, count):
        conn = db.connect(self.library.path)
        try:
            for _ in range(count):
                n = len(self.photos)
                path = os.path.join(self.folder, "heron %02d.jpg" % n)
                Image.new("RGB", (48, 32), (5 * n % 255, 90, 140)).save(path, "JPEG")
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Birds/Heron"]})
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, OLD_KEY, mtime, size, vector(1024, n).tobytes())
                self.photos.append(path)
            conn.commit()
        finally:
            conn.close()

    def give_new(self, photos):
        conn = db.connect(self.library.path)
        try:
            for path in photos:
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, NEW_KEY, mtime, size, vector(1152, 7).tobytes())
            conn.commit()
        finally:
            conn.close()

    def rows(self):
        """{paths.key(path): holds a vector under the new key} of every photo's row."""
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return {paths.key(path): bool(held) for _id, path, _m, _s, held in store_photos.stamps(conn, with_vectors=NEW_KEY)}
        finally:
            conn.close()

    def new_vectors(self):
        """The paths.key of each photo holding a vector under the new key (one at most: keyed by photo and model)."""
        return {key for key, held in self.rows().items() if held}

    def embed(self, **kwargs):
        return photo_reembed.run(self.library, self.runtime, NEW, **kwargs)

    def settings_held(self):
        return dict(read(self.library.path, "SELECT key, value FROM settings"))

    def cli(self, *args):
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=self.runtime):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "embed-photos", "--model",
                                                       "%s:%s" % NEW] + list(args))


class TheDryRun(_Library):

    def test_the_dry_run_counts_what_it_would_make_and_writes_nothing(self):
        self.give_new(self.photos[:3])
        os.remove(self.photos[9])   # deleted outside TagPup; its row stays until a sync
        before = rows_of(self.library.path)
        result = self.cli()
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("harbour: 10 photo(s), 3 hold a vector under %s already, 7 hold none (1 of them have no file)."
                      % NEW_KEY, " ".join(result.output.split()))
        self.assertIn("--apply would embed 6, about 0 minute(s). Nothing was written.", " ".join(result.output.split()))
        self.assertEqual(before, rows_of(self.library.path), "a dry run wrote")
        self.assertEqual([], [clip.forwards for clip in self.clips if clip.forwards])

    def test_the_librarys_own_model_and_an_unknown_one_are_refused(self):
        before = rows_of(self.library.path)
        for model in ("%s:%s" % OLD, "ViT-Q-99:nowhere"):
            with mock.patch.object(tagpup_cli, "get_runtime", return_value=self.runtime):
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "embed-photos", "--model",
                                                             model, "--apply"])
            self.assertEqual(1, result.exit_code, result.output)
            self.assertIn("Not run:", result.output)
        self.assertEqual(before, rows_of(self.library.path))


class AStoppedRunResumes(_Library):

    def test_a_run_stopped_after_a_batch_resumes_and_makes_only_the_rest(self):
        settings_before = self.settings_held()
        seen = []

        def progress(counts):
            # While it runs: the library's model and settings as they were, and its key's reader sees only its own.
            self.assertEqual(settings_before, self.settings_held())
            index = search.PhotoIndex(self.library.path, OLD_KEY)
            index.load()
            try:
                self.assertEqual((OLD_KEY, 1024, 10, 10), (index.model, index.dim, index.indexed, index.count))
            finally:
                index.close()
            seen.append(counts["kept"])

        with mock.patch.object(photo_reembed, "BATCH", 4):
            first = self.embed(progress=progress, should_stop=lambda: len(seen) >= 1)
            self.assertEqual((True, 4, 4, 0), (first["stopped"], first["embedded"], first["kept"], first["not_kept"]))
            self.assertEqual(4, len(self.new_vectors()))
            second = self.embed(progress=progress)
        self.assertEqual((False, 6, 6, 4), (second["stopped"], second["to_embed"], second["kept"], second["held"]))
        self.assertEqual({paths.key(path) for path in self.photos}, self.new_vectors())
        self.assertEqual(1, len(self.clips), "the runtime keeps one model for both runs")
        made = self.clips[-1].forwards
        self.assertEqual(10, len(made), "a photo was embedded twice")
        self.assertEqual(sorted(paths.key(path) for path in self.photos), sorted(made))
        self.assertEqual(settings_before, self.settings_held())


class APhotoGoneMeanwhile(_Library):

    def test_a_photo_removed_mid_run_is_skipped_and_given_no_row_back(self):
        removed = self.photos[9]

        def a_sync_removes_it(done):
            if done == 1:
                self.assertEqual(1, store_photos.forget_photo(self.library.path, removed)["photos"])

        self.runtime.clip(None, photo_reembed.wanted(self.runtime.settings(self.library), NEW)).on_forward = \
            a_sync_removes_it
        counts = self.embed()
        self.assertEqual((10, 9, 1), (counts["embedded"], counts["kept"], counts["not_kept"]))
        rows = self.rows()
        self.assertNotIn(paths.key(removed), rows, "the photo removed was given a row back")
        self.assertEqual({paths.key(path) for path in self.photos[:9]}, set(rows))
        self.assertEqual(set(rows), self.new_vectors())


class AFileThatDoesNotDecode(_Library):

    def test_a_file_that_does_not_decode_is_counted_and_tried_again_by_the_next_run(self):
        damaged = self.photos[4]
        with open(damaged, "wb") as handle:
            handle.write(b"\xff\xd8\xff\xe0 not the rest of a photo")
        with self.assertLogs("tagpup_cli.embedder", "WARNING"):
            first = self.embed()
        self.assertEqual((9, {"Unreadable": 1}), (first["kept"], first["unread"]))
        with self.assertLogs("tagpup_cli.embedder", "WARNING"):
            second = self.embed()
        self.assertEqual((1, 0, {"Unreadable": 1}), (second["to_embed"], second["kept"], second["unread"]))
        Image.new("RGB", (48, 32), (1, 2, 3)).save(damaged, "JPEG")   # the owner puts the photo back from a copy
        third = self.embed()
        self.assertEqual((1, 1, {}), (third["to_embed"], third["kept"], third["unread"]))
        self.assertEqual(10, len(self.new_vectors()))


class TheSwitchWaitsForTheRun(_Library):

    def test_at_forty_percent_the_switch_is_refused_and_after_a_full_run_it_passes(self):
        with mock.patch.object(photo_reembed, "BATCH", 4):
            self.embed(should_stop=lambda: len(self.clips[-1].forwards) >= 4)
        result = library_settings.change(self.library, dict(TO_NEW), acknowledged=["clip"], apply=False)
        self.assertEqual(library_settings.TOO_FEW_PHOTO_VECTORS % (
            library_settings.PHOTOS_HELD % (4, 10, NEW_KEY, 6), 5), result.refused)
        ran = self.cli("--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        said = " ".join(ran.output.split())
        self.assertIn("Embedded 6 of 6: 6 kept, 0 not kept", said)
        self.assertIn("10 of the library's 10 photos now hold a vector under %s." % NEW_KEY, said)
        before = rows_of(self.library.path)
        result = library_settings.change(self.library, dict(TO_NEW), acknowledged=["clip"], apply=False)
        self.assertFalse(result.refused, result.refused)
        self.assertEqual({"key": NEW_KEY, "holds": 10, "without": 0}, {
            k: result.details["photo_vectors"][k] for k in ("key", "holds", "without")})
        self.assertEqual(before, rows_of(self.library.path), "the gear's dry run wrote")
        self.assertEqual(OLD[0], self.settings_held()["model.name"])


class ARunWaitsForTheCard(_Library):

    def test_the_run_takes_the_card_before_it_reads_what_is_left(self):
        asked = []

        class Turn:
            def __init__(turn, what, models, cancelled=None, report=None):
                asked.append((what, cancelled))
                turn.clip = models[0]

            def __enter__(turn):
                turn.clip.events.append("turn")
                # Another run held the card meanwhile, and made three of the vectors.
                self.give_new(self.photos[:3])
                return turn

            def __exit__(turn, *exc):
                turn.clip.events.append("turn given back")

        stop = mock.Mock(return_value=False)
        with mock.patch.object(self.runtime, "gpu_turn", Turn):
            counts = self.embed(should_stop=stop)
        self.assertEqual([("making the vectors of %s:%s (harbour)" % NEW, stop)], asked)
        self.assertEqual(["turn", "load", "turn given back"], self.clips[-1].events)
        self.assertEqual((3, 7, 7), (counts["held"], counts["to_embed"], counts["kept"]))
        self.assertEqual(sorted(paths.key(path) for path in self.photos[3:]), sorted(self.clips[-1].forwards))


if __name__ == "__main__":
    unittest.main()

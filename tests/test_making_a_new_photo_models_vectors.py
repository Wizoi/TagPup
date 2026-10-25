"""The owner makes SigLIP 2 SO400M's vectors for a library (TagPup CLI: embed-photos --model ViT-SO400M-14-SigLIP2:webli,
tagpup.services.photo_reembed) before switching it in the gear (tests/test_switching_a_librarys_photo_model.py, whose
gate this run is made to pass). About 40 minutes for photo_index, so it is stopped and run again, and the library is in
use while it runs. What the owner lives through:

- The dry run counts the photos, those holding the new vector, those to embed and those with no file, and the time;
  nothing is written.
- A run stopped after a batch keeps what it made; the next makes only the rest, nothing twice. While it runs the
  library's model, key and settings are as they were, and a reader of the library's key finds no new vector.
- A photo removed while the run is under way is skipped and never given a row back; the batch it was in is one write
  (one connection, one take of the write lock, one commit), the others of it kept (#1839).
- A file that does not decode is counted, and tried again by the next run.
- A photo the owner edits while it is embedded keeps a vector stamped with the file as it was opened, so the next look
  at it (Suggest, the index) finds the vector stale and makes it again (#1840).
- Two libraries holding one folder: each is embedded by its own run, and neither run writes the other library (#1840).
- The drive is not connected (every file not found): the run stops past 1% (and 20) with one sentence asking whether
  the drive or share is connected, exits 1, and what an earlier run kept stays kept; one file really gone of a hundred
  is counted and the run goes on (#1838).
- At 40% the switch is refused, from the command line (set-photo-model) as in the gear, with the counts; after a full
  run its dry run passes and writes nothing, and says how many words Suggest makes again under the new model; applied it
  is one change of the journal, and its undo puts the old model back.
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
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402

OLD = ("ViT-H-14", "laion2b_s32b_b79k")
NEW = ("ViT-SO400M-14-SigLIP2", "webli")
OLD_KEY = store_embeddings.model_key(*OLD, True, 1.4, 512)
NEW_KEY = store_embeddings.model_key(*NEW, True, 1.4, None)
DIMS = {OLD[0]: 1024, NEW[0]: 1152}


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
        self.on_forward = self.on_read = None

    def load(self):
        self.events.append("load")

    def unload(self):
        pass

    def input_of(self, path, seen=None, load=True):
        assert not load, "a read ahead never loads the model"
        if self.on_read:
            self.on_read(path)
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


class ABatchIsOneWrite(_Library):

    def test_a_batch_of_256_is_one_write_and_a_photo_removed_in_it_is_the_one_not_kept(self):
        self.add_photos(photo_reembed.BATCH - 10)
        removed = self.photos[200]
        writes, syncing = [], []
        write_with_connection = db.write_with_connection

        def counted(target, operation, label="database write"):
            if not syncing:
                writes.append(label)
            return write_with_connection(target, operation, label=label)

        def a_sync_removes_it(done):
            if done == 100:
                syncing.append(True)
                self.assertEqual(1, store_photos.forget_photo(self.library.path, removed)["photos"])
                syncing.clear()

        self.runtime.clip(None, photo_reembed.wanted(self.runtime.settings(self.library), NEW)).on_forward = \
            a_sync_removes_it
        with mock.patch.object(db, "write_with_connection", counted):
            counts = self.embed()
        self.assertEqual((256, 255, 1), (counts["embedded"], counts["kept"], counts["not_kept"]))
        self.assertEqual(1, len(writes), "a batch was not one write: %d" % len(writes))
        self.assertEqual({paths.key(path) for path in self.photos if path != removed}, self.new_vectors())
        self.assertNotIn(paths.key(removed), self.rows(), "the photo removed was given a row back")


class APhotoEditedWhileItIsEmbedded(_Library):

    def test_a_photo_edited_while_it_is_embedded_keeps_a_vector_of_before_and_the_next_look_makes_it_again(self):
        edited = self.photos[3]
        opened = store_embeddings.stamp_of(edited)
        wanted = photo_reembed.wanted(self.runtime.settings(self.library), NEW)

        def the_owner_edits_it(path):
            # Between the stamp the pass took and the decode: another program saves the photo larger.
            if paths.key(path) == paths.key(edited):
                Image.new("RGB", (64, 48), (200, 10, 10)).save(edited, "JPEG")
                os.utime(edited, (opened[0] + 60, opened[0] + 60))

        self.runtime.clip(None, wanted).on_read = the_owner_edits_it
        counts = self.embed()
        self.assertEqual((10, 10), (counts["embedded"], counts["kept"]))
        held = read(self.library.path, "SELECT e.mtime, e.size FROM embeddings e JOIN photos p ON p.id = e.photo_id"
                    " WHERE p.id = ? AND e.model = ?", (store_photos_id(self.library.path, edited), NEW_KEY))
        self.assertEqual([opened], held, "the vector was not stamped with the file as it was opened")
        index = search.PhotoIndex(self.library.path, NEW_KEY)
        index.open()
        try:
            looked = search.PhotoEmbeddings(self.runtime.clip(None, wanted), index)
            self.assertIsNone(looked.cached(edited), "the photo edited meanwhile was taken as embedded")
            self.assertIsNotNone(looked.cached(self.photos[4]))
        finally:
            index.close()


class TwoLibrariesHoldingOneFolder(_Library):

    def test_each_library_is_embedded_by_its_own_run_and_neither_writes_the_other(self):
        other = Library(self.home.library("lagoon.db"))
        self.assertEqual(1, library_actions.create(other.path).changed)
        library_actions.record_added(other, [self.folder])
        self.addCleanup(self.runtime.forget, other)
        conn = db.connect(other.path)
        try:
            for n, path in enumerate(self.photos):
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Birds/Heron"]})
                mtime, size = store_embeddings.stamp_of(path)
                store_embeddings.put(conn, path, OLD_KEY, mtime, size, vector(1024, n).tobytes())
            conn.commit()
        finally:
            conn.close()

        others_before = rows_of(other.path)
        first = self.embed()
        self.assertEqual((10, 10), (first["to_embed"], first["kept"]))
        self.assertEqual(others_before, rows_of(other.path), "the run of harbour wrote lagoon")

        mine_before = rows_of(self.library.path)
        second = photo_reembed.run(other, self.runtime, NEW)
        self.assertEqual((0, 10, 10), (second["held"], second["to_embed"], second["kept"]))
        self.assertEqual(mine_before, rows_of(self.library.path), "the run of lagoon wrote harbour")
        self.assertEqual(10, read(other.path, "SELECT COUNT(*) FROM embeddings WHERE model = ?", (NEW_KEY,))[0][0])


def store_photos_id(db_path, path):
    """The id of the photo's row, as the pass reads the rows (store.photos.stamps)."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return next(photo_id for photo_id, held_path, _m, _s, _held in store_photos.stamps(conn, with_vectors=NEW_KEY)
                    if paths.key(held_path) == paths.key(path))
    finally:
        conn.close()


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


class TheDriveIsNotConnected(_Library):

    def test_a_run_on_a_drive_not_connected_stops_with_a_sentence_and_keeps_what_was_kept(self):
        self.add_photos(30)   # 40 photos: the stop comes at the 21st not found
        with mock.patch.object(photo_reembed, "BATCH", 4):
            self.embed(should_stop=lambda: len(self.clips[-1].forwards) >= 4)
        self.assertEqual(4, len(self.new_vectors()))
        os.rename(self.folder, self.folder + " (drive away)")   # the drive unmounted: every path is not found
        before = rows_of(self.library.path)
        ran = self.cli("--apply")
        self.assertEqual(1, ran.exit_code, ran.output)
        said = " ".join(ran.output.split())
        self.assertIn("21 of 36 photo files were not found or could not be read: is the drive or share connected?",
                      said)
        self.assertIn("Embedded 0 of 36: 0 kept, 0 not kept", said)
        self.assertIn("21 file(s) gone, 0 could not be read", said)
        self.assertNotIn("now hold a vector", said)
        self.assertEqual(before, rows_of(self.library.path), "the stopped run wrote")
        self.assertEqual(4, len(self.new_vectors()), "what the earlier run kept was not kept")

    def test_one_file_gone_of_a_hundred_is_counted_and_the_run_goes_on(self):
        self.add_photos(90)
        os.remove(self.photos[50])   # deleted outside TagPup; its row stays until a sync
        ran = self.cli("--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        said = " ".join(ran.output.split())
        self.assertIn("Embedded 99 of 100: 99 kept, 0 not kept", said)
        self.assertIn("1 file(s) gone, 0 could not be read", said)
        self.assertIn("99 of the library's 100 photos now hold a vector under %s." % NEW_KEY, said)


class TheSwitchWaitsForTheRun(_Library):

    def set_model(self, *args):
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=self.runtime):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "set-photo-model", "%s:%s" % NEW]
                                      + list(args))

    def changes(self):
        return [change for (change,) in read(self.library.path, "SELECT id FROM changes ORDER BY id")]

    def test_at_forty_percent_the_switch_is_refused_and_after_a_full_run_it_passes_and_is_undone(self):
        # Suggest made two words under the library's own model (each under two prompts), one of them under the new.
        conn = db.connect(self.library.path)
        try:
            for word in ("Heron", "Harbour"):
                for prompt in ("a photo of %s" % word, "a %s, 1998" % word):
                    store_taxonomy.keep_tag_embedding(conn, word, prompt, *OLD, vector(1024, 3).tobytes())
            store_taxonomy.keep_tag_embedding(conn, "Heron", "a photo of Heron", *NEW, vector(1152, 3).tobytes())
            conn.commit()
        finally:
            conn.close()
        with mock.patch.object(photo_reembed, "BATCH", 4):
            self.embed(should_stop=lambda: len(self.clips[-1].forwards) >= 4)
        before, settings_before, changes_before = rows_of(self.library.path), self.settings_held(), self.changes()
        refused = self.set_model("--apply")
        self.assertEqual(1, refused.exit_code, refused.output)
        self.assertIn(library_settings.TOO_FEW_PHOTO_VECTORS % (library_settings.PHOTOS_HELD % (4, 10, NEW_KEY, 6), 5),
                      " ".join(refused.output.split()))
        self.assertEqual(before, rows_of(self.library.path), "a refused switch wrote")

        ran = self.cli("--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        said = " ".join(ran.output.split())
        self.assertIn("Embedded 6 of 6: 6 kept, 0 not kept", said)
        self.assertIn("10 of the library's 10 photos now hold a vector under %s." % NEW_KEY, said)

        before = rows_of(self.library.path)
        dry = self.set_model()
        self.assertEqual(0, dry.exit_code, dry.output)
        said = " ".join(dry.output.split())
        self.assertIn(library_settings.PHOTOS_HELD % (10, 10, NEW_KEY, 0), said)
        self.assertIn("Suggest holds 1 word(s) (1 row(s)) made under %s:%s, of the 2 it holds under the library's "
                      "model: it makes the other 1 at its first run after the switch." % NEW, said)
        self.assertIn("--apply would switch it. Nothing was written.", said)
        self.assertEqual(before, rows_of(self.library.path), "the dry run wrote")

        switched = self.set_model("--apply")
        self.assertEqual(0, switched.exit_code, switched.output)
        made = [change for change in self.changes() if change not in changes_before]
        self.assertEqual(1, len(made), "the switch is one change of the journal")
        self.assertIn("change %d. Undo with `undo %d --apply`" % (made[0], made[0]), " ".join(switched.output.split()))
        held = self.settings_held()
        self.assertEqual((NEW[0], NEW[1], ""), (held["model.name"], held["model.pretrained"],
                                               held["model.force_image_size"]))
        self.assertEqual(NEW_KEY, self.runtime.model_key(self.library))

        undone = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "undo", str(made[0]), "--apply"])
        self.assertEqual(0, undone.exit_code, undone.output)
        self.assertEqual(settings_before, self.settings_held(), "the undo did not put the old model back")
        self.assertEqual(OLD_KEY, self.runtime.model_key(self.library))


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

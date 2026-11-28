"""A folder is copied to a client with no graphics card (docs/PLAN_KEEP_IN_STEP.md, scenario 6; cut 2).

What the owner lives through, indexing it there with the CLI's index (the queue runs the same command):

* a photo whose bake is current is adopted from its file: no model works it out, and no turn on the graphics card is
  taken -- there is none to take;
* a photo with no bake is worked out on the processor, and the index's closing line says "on the processor", not the card;
* the index then writes the bake of the photo it worked out (tagpup.services.bake_after_index), and the next index of the
  folder writes nothing;
* a bake whose picture changed since it was baked (repainted in an editor, its XMP kept) is never taken as current: the
  photo is worked out again from the picture it holds now, and its file baked again with that picture's fingerprint.

The models are stood in for (tests/stand_in_models.py) and reported off the card (runtime.on_the_card), as on a machine
whose models run on the processor; a turn on the card (gpu.Card.hold) fails the test. Pillow and ExifTool are real; the
copied bake is written through the one writer (tests/baked_files.py); the library is made by the picker's Create, in a
home of its own. Fictional folder names.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baked_files  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import NewFaces, RefusableFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake, images  # noqa: E402
from tagpup.ml import clip, gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, bake_reading, baking  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
FACES = validation.SCRFD_ADAFACE
SIZE = (640, 480)
DETECTOR = face_service.detector_of(NewFaces())
FACE = [300, 200, 400, 330]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AMachineWithoutACard(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="machine_without_a_card_")
        self.db_path = self.home.library("lighthouse.db")
        self.assertEqual(1, library_actions.create(self.db_path).changed)
        self.photo_key = store_embeddings.model_key(**library_settings.read(Library(self.db_path)).embedder)
        self.folder = paths.stored(os.path.join(self.home.root, "Lighthouse"))
        os.makedirs(self.folder)
        self.computed = baked_files.unit(bake.PHOTO_DIM, 99).tolist()
        self.embedded = []
        self.detector = RefusableFaces({"unbaked.jpg": [(FACE, 0.99, 1)]}, vector=baked_files.face_vector)

        def embed(*args, **kwargs):
            self.embedded.append(args)
            return self.computed

        def no_card(*args, **kwargs):
            raise AssertionError("a turn on the graphics card was taken on a machine with none")
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", return_value="input"),
                        mock.patch.object(clip.ClipModel, "embed_input", side_effect=embed),
                        mock.patch.object(runtimes, "on_the_card", return_value=False),
                        mock.patch.object(gpu.Card, "hold", side_effect=no_card),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def picture(self, name, colour):
        path = os.path.join(self.folder, name)
        Image.new("RGB", SIZE, colour).save(path, "JPEG")
        return path

    def copied_with_its_bake(self, name, colour):
        """A photo baked on the desktop under the library's keys and copied here: its bake current for its picture."""
        path = self.picture(name, colour)
        upright = images.opened(path, upright=True)
        held = baked_files.baked(baked_files.unit(bake.PHOTO_DIM, 7), [(baked_files.area(0), baked_files.unit(bake.FACE_DIM, 70))],
                                 upright.size, photo_model=self.photo_key, face_model=FACES,
                                 fingerprint=images.fingerprint(upright))
        baked_files.write(path, held)
        return path

    def repaint(self, path, colour):
        """An editor's save of a new picture into the file, its XMP (the bake with it) kept as it was."""
        with Image.open(path) as held:
            xmp = held.info["xmp"]
        Image.new("RGB", SIZE, colour).save(path, "JPEG", xmp=xmp)

    def index(self):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        if isinstance(result.exception, BaseException) and not isinstance(result.exception, SystemExit):
            raise result.exception
        self.assertEqual(0, result.exit_code, result.output)
        return " ".join(result.output.split())

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def vector(self, path):
        (vector,) = [np.frombuffer(blob, dtype=np.float32) for path_of, blob in self.query(
            "SELECT p.path, e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id WHERE e.model = ?",
            (self.photo_key,)) if paths.key(path_of) == paths.key(path)]
        return vector

    def fingerprint_row(self, path):
        return [(key, bytes(hash)) for path_of, key, hash in self.query(
            "SELECT p.path, f.key, f.hash FROM photo_fingerprints f JOIN photos p ON p.id = f.photo_id")
            if paths.key(path_of) == paths.key(path)]

    def assertBaked(self, path):
        """The file holds the bake its row makes (planned with its decode: Current), its fingerprint its picture's now."""
        self.assertIsInstance(baked_files.plan(self.db_path, path, DETECTOR), baking.Current, path)
        held = baked_files.held(path)
        self.assertEqual([(held.fingerprint_key, held.fingerprint)], self.fingerprint_row(path))
        self.assertEqual(bytes(images.fingerprint(images.opened(path, upright=True))), held.fingerprint, path)

    def test_a_current_bake_is_adopted_one_without_is_worked_out_on_the_processor_and_baked_and_a_stale_one_is_not_taken(self):
        copied = self.copied_with_its_bake("copied.jpg", (40, 90, 200))
        unbaked = self.picture("unbaked.jpg", (200, 160, 40))

        said = self.index()
        self.assertIn(bake_reading.SAID + "1 photo(s) read from their file, 1 worked out on the processor;", said)
        self.assertNotIn("graphics card", said)
        self.assertEqual(1, len(self.embedded), "the copied photo's vector is its bake's, the other's worked out")
        self.assertEqual(["unbaked.jpg"], [os.path.basename(path) for path in self.detector.asked])
        self.assertTrue(np.allclose(baked_files.unit(bake.PHOTO_DIM, 7), self.vector(copied), atol=1e-3))
        # The copied photo holds its bake and no identity: its bake is not rewritten, its identity written alone.
        self.assertIn(bake_after_index.SAID + "2 file(s) written, 0 already held it.", said)
        self.assertBaked(copied)
        self.assertBaked(unbaked)

        stamps = {path: os.stat(path).st_mtime_ns for path in (copied, unbaked)}
        again = self.index()
        self.assertIn("Index is current.", again)
        self.assertNotIn(bake_after_index.SAID, again, "a folder with nothing to index has nothing to bake")
        self.assertEqual(stamps, {path: os.stat(path).st_mtime_ns for path in (copied, unbaked)})

        # Repainted in an editor, its XMP kept: the bake's fingerprint is of the picture before.
        self.repaint(copied, (10, 200, 120))
        del self.embedded[:]
        changed = self.index()
        self.assertIn("1 bake(s) stale (the picture changed after it was baked)", changed)
        self.assertEqual(1, len(self.embedded), "a stale bake is not taken: the new picture is worked out")
        self.assertTrue(np.allclose(self.computed, self.vector(copied), atol=1e-6))
        self.assertBaked(copied)


if __name__ == "__main__":
    unittest.main()

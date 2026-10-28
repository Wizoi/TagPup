"""Scenario P10: the owner works in a library on its face model (faces.model scrfd-adaface, every library's: the old
model, FaceNet, went 2026-10-28, and no library is switched to it, tests/test_switching_a_librarys_face_model.py, V11).

What the owner lives through, in one library:

- Name faces from tags, asked first (a dry run), reads the model's vectors by its own cuts: a photo with one face and
  one person whose face is 0.6 like the person's named face is like them enough to name (AdaFace names unasked from
  0.520).
- A new photo is indexed (the CLI's `index`, as the server's queue runs it): the model finds two faces, one 60 px and
  one 30 px; the 30 px one is under the floor of a new face (scrfd_adaface.NEW_FACE_FLOOR) and is not made. The face made
  holds a vector under the model, a crop, and the photo is recorded as detected under {"model": "scrfd-adaface"}.
- Suggest (Runtime.begin) on a photo with no face on file, in a folder not indexed, detects it with the model, records
  the face (its vector the model's, a crop), and offers the person whose named face it is 0.45 like: AdaFace offers from
  0.310. The panel (SuggestionModel.offered, what Apply All and the writers take) shows that offer: the model's cut
  decided it, and the tag floor (suggesting.OFFER_A_TAG, 0.60) is not a face's.

(Until 2026-10-28 the library was switched here from FaceNet, and a switch committed while a Suggest run or
cluster-faces ran was read by the model the run began with; with one face model no library is switched, and those went
with FaceNet's cuts: docs/OLD_MODEL_VESTIGES.md row 8.)

Fictional names; a library in a home of the test's own; the model as tagpup.runtime builds it, its weights not loaded
and its two networks stood in for (`_detected`, `embed`): the picture, the floor, the crop and the detector name are the
model's own. CLIP stood in for, its weights not loaded.
"""
import json
import math
import os
import sys
import unittest
from unittest import mock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402
import web_client  # noqa: E402
from face_rows import add_face  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import identify as identify_jobs  # noqa: E402
from tagpup.ml import clip, scrfd_adaface  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import faces_from_tags  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, taxonomy  # noqa: E402

NEW = validation.SCRFD_ADAFACE
PERSON = "People/Wren Calloway"
CLIP_DIM = 8


def unit(vector):
    return vector / np.linalg.norm(vector)


def alike(base, likeness, seed):
    """A unit vector whose cosine with the unit vector `base` is `likeness`."""
    other = np.random.default_rng(seed).normal(size=base.shape)
    other = unit(other - other.dot(base) * base)
    return (likeness * base + math.sqrt(1 - likeness * likeness) * other).astype(np.float32)


def landmarks(box):
    """Five points (the eyes, the nose, the corners of the mouth) inside `box`, where a face's are."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return [[x1 + fx * w, y1 + fy * h] for fx, fy in ((0.3, 0.4), (0.7, 0.4), (0.5, 0.55), (0.35, 0.75), (0.65, 0.75))]


def photo(path):
    """A real JPEG of 200 x 150, as a photo in a folder is."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pixels = (np.indices((150, 200)).sum(axis=0) % 256).astype(np.uint8)
    Image.fromarray(np.stack([pixels, pixels // 2, 255 - pixels], axis=-1)).save(path, "JPEG")
    return path


class TheNewModelStoodIn:
    """The two networks of tagpup.ml.scrfd_adaface.ScrfdAdaFace stood in for: the detector finds the faces `boxes`,
    the recogniser gives each the vector `vectors` holds for it; what the model does around them is its own."""

    def __init__(self):
        self.boxes, self.vectors = [], []

    def _detected(self, model, detectors, picture):
        return [{"box": list(box), "prob": 0.95, "landmarks": landmarks(box)} for box in self.boxes]

    def embed(self, model, recogniser, crops):
        return [(list(map(float, vector)), 21.0) for vector in self.vectors[:len(crops)]]

    def patches(self):
        stand_in = self
        return (mock.patch("torch.cuda.is_available", return_value=False),
                mock.patch.object(scrfd_adaface.ScrfdAdaFace, "_init_models"),
                mock.patch.object(scrfd_adaface.ScrfdAdaFace, "_detected",
                                  lambda model, detectors, picture: stand_in._detected(model, detectors, picture)),
                mock.patch.object(scrfd_adaface.ScrfdAdaFace, "embed",
                                  lambda model, recogniser, crops: stand_in.embed(model, recogniser, crops)))


@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class ALibraryOnItsFaceModel(unittest.TestCase):
    """lakeside, made as the picker's Create makes one (on the face model, the setting's default): one folder; Wren
    Calloway named by hand on one photo, and a photo whose keyword names her holding one face nobody named."""

    def setUp(self):
        self.runtime = Runtime(build_clip=lambda settings: stand_in_models.Clip(CLIP_DIM))
        app, self.home = web_client.app_for(self, "tuner", startup=None, runtime=self.runtime)
        self.client = app.test_client()
        self.library = Library(self.home.library("lakeside.db"))
        library_actions.create(self.library.path)
        self.addCleanup(self.runtime.forget, self.library)
        self.folder = paths.stored(os.path.join(self.home.root, "Lakeside"))
        os.makedirs(self.folder)
        library_actions.record_added(self.library, [self.folder])
        self.adaface_wren = unit(np.random.default_rng(2).normal(size=512)).astype(np.float32)
        conn = db.connect(self.library.path)
        try:
            taxonomy.add_path(conn, "People", root_has_face=1)
            taxonomy.add_path(conn, PERSON)
            known = photo(os.path.join(self.folder, "jetty.jpg"))
            photo_rows.add_read(conn, known, {"XMP:Subject": [PERSON]})
            self.named = add_face(conn, known, (40, 30, 120, 110), self.adaface_wren.tobytes(), prob=0.99,
                                  name="Wren Calloway", name_source="manual")
            self.shore = photo(os.path.join(self.folder, "shore.jpg"))
            photo_rows.add_read(conn, self.shore, {"XMP:Subject": [PERSON]})
            self.unnamed = add_face(conn, self.shore, (50, 20, 130, 100), alike(self.adaface_wren, 0.6, 4).tobytes(),
                                    prob=0.99)
            conn.commit()
        finally:
            conn.close()
        self.model = TheNewModelStoodIn()
        for patch in self.model.patches():
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, sql, *args):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def named_vector(self, cache):
        """Wren's named face's vector in Identify's named faces, as the server's cache answers them."""
        ids, _people, matrix = identify_jobs.named_faces(self.library, cache)
        return matrix[list(ids).index(self.named)]

    def index(self, folder):
        """`tagpup_cli.py index` of `folder`, as the server's queue runs it; CLIP stood in for."""
        with mock.patch.object(clip.ClipModel, "_init_model"), \
                mock.patch.object(clip.ClipModel, "picture_input"), \
                mock.patch.object(clip.ClipModel, "embed_input", return_value=[0.1] * CLIP_DIM):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "index", folder])
        self.assertEqual(0, result.exit_code, result.output)

    def faces_of(self, path):
        """(id, box) of the faces of the photo at `path`."""
        return [(face_id, json.loads(box)) for face_id, box in self.read(
            "SELECT f.id, f.box FROM faces f JOIN photos p ON p.id = f.photo_id WHERE p.path = ? ORDER BY f.id",
            paths.stored(path))]

    def made_by_the_new_model(self, path, detected=True):
        """The faces of `path`: one, as the model made it -- its vector under the model, a crop, and, when `detected` (a
        photo with a row when it was detected), the photo recorded as detected under the model's name."""
        [(face_id, box)] = self.faces_of(path)
        self.assertEqual([NEW], [model for (model,) in self.read(
            "SELECT model FROM face_embeddings WHERE face_id = ?", face_id)], "a vector of another model was made")
        self.assertEqual([(1,)], self.read("SELECT COUNT(*) FROM face_crops WHERE face_id = ?", face_id), "no crop")
        self.assertEqual([{"model": NEW}] if detected else [], [json.loads(name) for (name,) in self.read(
            "SELECT d.detector FROM faces_detected d JOIN photos p ON p.id = d.photo_id WHERE p.path = ?",
            paths.stored(path))])
        return face_id, box

    def test_the_owner_names_from_tags_indexes_and_suggests_by_the_models_cuts(self):
        self.assertEqual(NEW, library_settings.read(self.library)["faces.model"])
        np.testing.assert_allclose(self.adaface_wren, self.named_vector(identify_jobs.GridCache()), atol=1e-6)

        # Name faces from tags, asked first: shore's face is 0.6 like Wren's named face, by the new model's vectors.
        planned = faces_from_tags.plan(self.library)
        self.assertEqual(1, planned.counts["one_face_one_person"], planned.counts)
        self.assertEqual(1, planned.counts["like_them_enough_to_name"], planned.counts)
        self.assertEqual([self.unnamed], planned.ids["faces"])

        # A new photo is indexed: of the two faces the model finds, the one under the floor is not made.
        arrival = photo(os.path.join(self.folder, "Arrivals", "slipway.jpg"))
        small = 30
        self.assertLess(small, scrfd_adaface.NEW_FACE_FLOOR)
        self.model.boxes = [(20, 20, 80, 80), (120, 40, 120 + small, 40 + small)]
        self.model.vectors = [alike(self.adaface_wren, 0.2, 5), alike(self.adaface_wren, 0.1, 6)]
        self.index(os.path.dirname(arrival))
        _face, box = self.made_by_the_new_model(arrival)
        self.assertEqual([20, 20, 80, 80], box)

        # Suggest, on a photo with no face on file: detected by the new model, recorded, and Wren offered at 0.45.
        visitor = photo(os.path.join(self.folder, "Visitors", "pier.jpg"))
        self.model.boxes = [(60, 30, 140, 110)]
        self.model.vectors = [alike(self.adaface_wren, 0.45, 7)]
        run = self.runtime.begin(self.library, what="Suggest Visitors (lakeside)")
        try:
            suggested = run.suggester.suggest_for_photo(visitor, [0.1] * CLIP_DIM)
            _tags, people, _title = run.offered(suggested)
        finally:
            run.end()
        [offer] = [t for t in suggested["suggested_tags"] if t.get("has_face_match")]
        self.assertEqual((PERSON, 0.45), (offer["tag"], offer["score"]))
        self.assertEqual([{"name": PERSON, "score": 0.45}], people, "the panel hid the offer the model's cut made")
        self.made_by_the_new_model(visitor, detected=False)


if __name__ == "__main__":
    unittest.main()

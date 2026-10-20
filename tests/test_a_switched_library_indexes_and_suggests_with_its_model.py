"""The owner switches a library its pass has reached to the new face model (faces.model scrfd-adaface; docs/AI_PIPELINE.md,
phase 3, merge 3 item 3), keeps working in it, and switches back.

What the owner lives through, in one library:

- The switch is allowed: every face is in a photo the pass reached (tagpup.services.settings.change).
- Name faces from tags, asked first (a dry run), reads the new model's vectors by its own cuts: a photo with one face and
  one person whose face is 0.6 like the person's named face is like them enough to name (AdaFace names unasked from
  0.520; by FaceNet's 0.80 the same number would not be).
- A new photo is indexed (the CLI's `index`, as the server's queue runs it): the new model finds two faces, one 60 px and
  one 30 px; the 30 px one is under the floor of a new face (scrfd_adaface.NEW_FACE_FLOOR) and is not made. The face made
  holds a vector under the new model alone, a crop, and the photo is recorded as detected under {"model": "scrfd-adaface"}.
- Suggest (Runtime.begin) on a photo with no face on file, in a folder not indexed, detects it with the new model, records
  the face (its vector the new model's alone, a crop), and offers the person whose named face it is 0.45 like: AdaFace
  offers from 0.310, FaceNet's 0.70 would not. (The panel shows a suggestion from 0.60, suggesting.OFFER_A_TAG, a floor
  read on FaceNet's likenesses: what it shows of such an offer is an open finding of this branch.)
- Switching back to FaceNet is allowed, and FaceNet reads its own vectors again; neither model's vectors were written by
  either switch.

Fictional names; a library in a home of the test's own; the new model as tagpup.runtime builds it, its weights not
loaded and its two networks stood in for (`_detected`, `embed`): the picture, the floor, the crop and the detector name
are the model's own. CLIP stood in for as tests/test_faces_found_at_the_librarys_cap.py does.
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
from face_rows import FACENET, add_face, add_vector_of_face  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import clustering, paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml import clip, scrfd_adaface  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import faces_from_tags, identify  # noqa: E402
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
class ALibrarySwitchedToTheNewModel(unittest.TestCase):
    """lakeside: one folder; Wren Calloway named by hand on one photo, and a photo whose keyword names her holding one
    face nobody named; every face holding FaceNet's vector and the new model's (its pass done)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="switched_model_")
        self.library = Library(self.home.library("lakeside.db"))
        self.assertEqual(1, library_actions.create(self.library.path).changed)
        self.folder = paths.stored(os.path.join(self.home.root, "Lakeside"))
        os.makedirs(self.folder)
        library_actions.record_added(self.library, [self.folder])
        self.facenet_wren = unit(np.random.default_rng(1).normal(size=512)).astype(np.float32)
        self.adaface_wren = unit(np.random.default_rng(2).normal(size=512)).astype(np.float32)
        conn = db.connect(self.library.path)
        try:
            taxonomy.add_path(conn, "People", root_has_face=1)
            taxonomy.add_path(conn, PERSON)
            known = photo(os.path.join(self.folder, "jetty.jpg"))
            photo_rows.add_read(conn, known, {"XMP:Subject": [PERSON]})
            self.named = add_face(conn, known, (40, 30, 120, 110), self.facenet_wren.tobytes(), model=FACENET,
                                  prob=0.99, name="Wren Calloway", name_source="manual")
            add_vector_of_face(conn, self.named, self.adaface_wren.tobytes(), model=NEW)
            self.shore = photo(os.path.join(self.folder, "shore.jpg"))
            photo_rows.add_read(conn, self.shore, {"XMP:Subject": [PERSON]})
            self.unnamed = add_face(conn, self.shore, (50, 20, 130, 100), alike(self.facenet_wren, 0.75, 3).tobytes(),
                                    model=FACENET, prob=0.99)
            add_vector_of_face(conn, self.unnamed, alike(self.adaface_wren, 0.6, 4).tobytes(), model=NEW)
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

    def vectors_by_model(self):
        return dict(self.read("SELECT model, COUNT(*) FROM face_embeddings GROUP BY model"))

    def switch(self, model):
        changed = library_settings.change(self.library, {"faces.model": model}, acknowledged=["face-model"])
        self.assertEqual(1, changed.changed, changed.refused)

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
        """The faces of `path`: one, as the new model made it -- its vector under the new model alone, a crop, and, when
        `detected` (a photo with a row when it was detected), the photo recorded as detected under the new model's name."""
        [(face_id, box)] = self.faces_of(path)
        self.assertEqual([NEW], [model for (model,) in self.read(
            "SELECT model FROM face_embeddings WHERE face_id = ?", face_id)], "a FaceNet vector was made")
        self.assertEqual([(1,)], self.read("SELECT COUNT(*) FROM face_crops WHERE face_id = ?", face_id), "no crop")
        self.assertEqual([{"model": NEW}] if detected else [], [json.loads(name) for (name,) in self.read(
            "SELECT d.detector FROM faces_detected d JOIN photos p ON p.id = d.photo_id WHERE p.path = ?",
            paths.stored(path))])
        return face_id, box

    def test_the_owner_switches_works_in_it_and_switches_back(self):
        before = self.vectors_by_model()
        self.assertEqual({FACENET: 2, NEW: 2}, before)
        self.switch(NEW)

        # Name faces from tags, asked first: shore's face is 0.6 like Wren's named face, by the new model's vectors.
        planned = faces_from_tags.plan(self.library)
        self.assertEqual(1, planned.counts["one_face_one_person"], planned.counts)
        self.assertEqual(1, planned.counts["like_them_enough_to_name"], planned.counts)
        self.assertEqual([self.unnamed], planned.ids["faces"])
        self.assertTrue(clustering.ADAFACE_CUTS.names_unasked(0.6, math.inf))
        self.assertFalse(clustering.FACENET_CUTS.names_unasked(0.6, math.inf), "the case is not one FaceNet's cuts name")

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
        runtime = Runtime(build_clip=lambda settings: stand_in_models.Clip(CLIP_DIM))
        self.addCleanup(runtime.forget, self.library)
        run = runtime.begin(self.library, what="Suggest Visitors (lakeside)")
        try:
            suggested = run.suggester.suggest_for_photo(visitor, [0.1] * CLIP_DIM)
        finally:
            run.end()
        [offer] = [t for t in suggested["suggested_tags"] if t.get("has_face_match")]
        self.assertEqual((PERSON, 0.45), (offer["tag"], offer["score"]))
        self.assertFalse(clustering.FACENET_CUTS.is_offered(0.45, math.inf), "the case is not one FaceNet's cuts offer")
        self.made_by_the_new_model(visitor, detected=False)

        # Switched back: FaceNet reads its own vectors again, and no switch wrote a vector.
        during = self.vectors_by_model()
        self.assertEqual({FACENET: 2, NEW: 4}, during)
        self.switch(FACENET)
        self.assertEqual(during, self.vectors_by_model())
        ids, _people, matrix = identify.named_faces(self.library)[1]
        self.assertIn(self.named, list(ids))
        np.testing.assert_allclose(self.facenet_wren, matrix[list(ids).index(self.named)], atol=1e-6)


if __name__ == "__main__":
    unittest.main()

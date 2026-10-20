"""The owner switches a library its pass has reached to the new face model (faces.model scrfd-adaface; docs/AI_PIPELINE.md,
phase 3, merge 3 item 3), keeps working in it, and switches back.

What the owner lives through, in one library:

- The switch, in the gear's dialog (POST /api/settings), is allowed: every face is in a photo the pass reached
  (tagpup.services.settings.change), and the reply says what the faces hold under the new model, for the dialog to show.
  Identify's named faces, cached against the faces fingerprint (which holds the model), are read again by the new model.
- Name faces from tags, asked first (a dry run), reads the new model's vectors by its own cuts: a photo with one face and
  one person whose face is 0.6 like the person's named face is like them enough to name (AdaFace names unasked from
  0.520; by FaceNet's 0.80 the same number would not be).
- A new photo is indexed (the CLI's `index`, as the server's queue runs it): the new model finds two faces, one 60 px and
  one 30 px; the 30 px one is under the floor of a new face (scrfd_adaface.NEW_FACE_FLOOR) and is not made. The face made
  holds a vector under the new model alone, a crop, and the photo is recorded as detected under {"model": "scrfd-adaface"}.
- Suggest (Runtime.begin) on a photo with no face on file, in a folder not indexed, detects it with the new model, records
  the face (its vector the new model's alone, a crop), and offers the person whose named face it is 0.45 like: AdaFace
  offers from 0.310, FaceNet's 0.70 would not. The panel (SuggestionModel.offered, what Apply All and the writers take)
  shows that offer: the model's cut decided it, and the tag floor (suggesting.OFFER_A_TAG, 0.60) is not a face's.
- The switch is in the library's history, and undone there: the library reads by FaceNet again, Identify's cached named
  faces by its vectors, and neither model's vectors were written by the switch or its undo.
- Switched back to FaceNet (never refused), the reply says only what is true of the faces under FaceNet: a face the new
  model made while the library was on it holds no FaceNet vector and takes no part; no face is "in a photo the pass has
  not reached", which is the new model's pass and no part of a switch back.
- Switched back to FaceNet, the owner indexes a new folder under it: its faces hold FaceNet's vector alone. The switch
  back cannot be undone in History now: the undo would put the new model back with those faces in photos its pass has not
  reached, which the switch itself is refused for (settings.change's check, UNPASSED_SHARE_ALLOWED). History lists it
  with the switch's sentence and counts, and the rehearsal and the undo refuse it so, writing nothing.
- A switch back the owner undid already, asked to be undone again, is refused as undone (the journal's own account comes
  first), not with the switch's sentence, though putting the new model back would be refused too.
- A Suggest run or an index begun before a switch keeps the model it began with to its end (Runtime.begin's lease, the
  CLI index's one `runtime.faces`); the next one reads the library's model (tests/test_the_runtime_keeps_models_by_settings.py).

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
import web_client  # noqa: E402
from face_rows import FACENET, add_face, add_vector_of_face  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import clustering, paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import identify as identify_jobs  # noqa: E402
from tagpup.ml import clip, scrfd_adaface  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import faces_from_tags  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
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
    """lakeside, open in TagTuner: one folder; Wren Calloway named by hand on one photo, and a photo whose keyword names
    her holding one face nobody named; every face holding FaceNet's vector and the new model's (its pass done)."""

    def setUp(self):
        self.runtime = Runtime(build_clip=lambda settings: stand_in_models.Clip(CLIP_DIM))
        app, self.home = web_client.app_for(self, "tuner", startup="lakeside.db", runtime=self.runtime)
        self.client = app.test_client()
        self.library = Library(self.home.library("lakeside.db"))
        self.addCleanup(self.runtime.forget, self.library)
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
        """Switched in the gear's dialog, its consequences ticked: the reply."""
        reply = self.client.post("/lakeside/api/settings", json={"values": {"faces.model": model},
                                                                  "acknowledged": ["face-model"]})
        self.assertEqual(200, reply.status_code, reply.data)
        answer = reply.get_json()
        self.assertEqual((1, True), (answer["changed"], answer["locked"]), answer)
        return answer

    def undo(self, change):
        """The change undone in History, rehearsed first."""
        url = "/lakeside/api/history/%d/undo" % change
        self.assertTrue(self.client.post(url, json={}).get_json()["dry_run"])
        reply = self.client.post(url, json={"apply": True})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

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

    def test_the_owner_switches_works_in_it_and_undoes_the_switch(self):
        before = self.vectors_by_model()
        self.assertEqual({FACENET: 2, NEW: 2}, before)
        grids = identify_jobs.GridCache()
        np.testing.assert_allclose(self.facenet_wren, self.named_vector(grids), atol=1e-6)
        switched = self.switch(NEW)
        counts = switched["face_vectors"]
        self.assertEqual({"faces": 2, "without": 0, "named_without": 0, "not_passed": 0},
                         {k: v for k, v in counts.items() if k != "said"})
        self.assertIn("2 of the library's 2 faces hold a vector under %s" % NEW, " ".join(counts["said"].split()))
        np.testing.assert_allclose(self.adaface_wren, self.named_vector(grids), atol=1e-6)

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
        run = self.runtime.begin(self.library, what="Suggest Visitors (lakeside)")
        try:
            suggested = run.suggester.suggest_for_photo(visitor, [0.1] * CLIP_DIM)
            _tags, people, _title = run.offered(suggested)
        finally:
            run.end()
        [offer] = [t for t in suggested["suggested_tags"] if t.get("has_face_match")]
        self.assertEqual((PERSON, 0.45), (offer["tag"], offer["score"]))
        self.assertEqual([{"name": PERSON, "score": 0.45}], people, "the panel hid the offer the model's cut made")
        self.assertFalse(clustering.FACENET_CUTS.is_offered(0.45, math.inf), "the case is not one FaceNet's cuts offer")
        self.made_by_the_new_model(visitor, detected=False)

        # The switch is in History, and undone there: FaceNet reads its own vectors again, and no vector was written.
        during = self.vectors_by_model()
        self.assertEqual({FACENET: 2, NEW: 4}, during)
        [newest] = journal_service.history(self.library, limit=1)["changes"]
        self.assertEqual((switched["change"], library_settings.CHANGE), (newest["id"], newest["operation"]))
        self.assertEqual(1, self.undo(switched["change"])["changed"])
        self.assertEqual(FACENET, library_settings.read(self.library)["faces.model"])
        self.assertEqual(during, self.vectors_by_model())
        np.testing.assert_allclose(self.facenet_wren, self.named_vector(grids), atol=1e-6)

    def test_the_switch_back_is_not_undone_onto_faces_the_pass_has_not_reached(self):
        self.switch(NEW)
        # Indexed while on the new model: its photo's face holds the new model's vector alone, as the indexer files it.
        conn = db.connect(self.library.path)
        try:
            dock = photo(os.path.join(self.folder, "Dock", "bollard.jpg"))
            photo_rows.add_read(conn, dock, {})
            add_face(conn, dock, (20, 20, 90, 90), alike(self.adaface_wren, 0.2, 9).tobytes(), model=NEW, prob=0.95)
            conn.commit()
        finally:
            conn.close()
        back = self.switch(FACENET)
        self.assertEqual(FACENET, library_settings.read(self.library)["faces.model"])
        counts = back["face_vectors"]
        self.assertEqual({"faces": 3, "without": 1, "named_without": 0, "not_passed": 0},
                         {k: v for k, v in counts.items() if k != "said"}, "a pass's count read for a switch back")
        self.assertEqual("1 of the library's 3 faces holds no vector under %s and takes no part in comparing, clustering"
                         " or the gallery of named faces while the library is on it." % FACENET,
                         " ".join(counts["said"].split()))

        # A new folder indexed under FaceNet: its photo's face holds FaceNet's vector alone, as the indexer files it.
        conn = db.connect(self.library.path)
        try:
            later = photo(os.path.join(self.folder, "Later", "harbour.jpg"))
            photo_rows.add_read(conn, later, {})
            add_face(conn, later, (30, 30, 110, 110), alike(self.facenet_wren, 0.3, 8).tobytes(), model=FACENET, prob=0.99)
            conn.commit()
        finally:
            conn.close()

        said = ("Nothing was written: 3 of the library's 4 faces hold a vector under %s; 0 named faces hold none, and would"
                " leave the named faces Identify compares with until the pass reaches them; 1 face is in photos the pass"
                " has not reached. A switch allows at most 5%% of the faces" % NEW)
        listed = self.client.get("/lakeside/api/history").get_json()["changes"]
        [entry] = [entry for entry in listed if entry["id"] == back["change"]]
        self.assertFalse(entry["undoable"], entry)
        self.assertIn(said, " ".join(entry["why_not"].split()))
        url = "/lakeside/api/history/%d/undo" % back["change"]
        for body in ({}, {"apply": True}):
            reply = self.client.post(url, json=body)
            self.assertEqual(400, reply.status_code, reply.data)
            self.assertIn(said, " ".join(reply.get_json()["refused"].split()))
        self.assertEqual(FACENET, library_settings.read(self.library)["faces.model"])

    def test_a_switch_back_undone_already_is_refused_as_undone(self):
        self.switch(NEW)
        back = self.switch(FACENET)
        self.undo(back["change"])
        self.assertEqual(NEW, library_settings.read(self.library)["faces.model"])
        self.switch(FACENET)
        conn = db.connect(self.library.path)
        try:
            later = photo(os.path.join(self.folder, "Later", "harbour.jpg"))
            photo_rows.add_read(conn, later, {})
            add_face(conn, later, (30, 30, 110, 110), alike(self.facenet_wren, 0.3, 8).tobytes(), model=FACENET, prob=0.99)
            conn.commit()
        finally:
            conn.close()
        self.assertIn(back["change"], library_settings.undo_refusals(self.library, [back["change"]]),
                      "the case is not one the switch's check refuses")
        reply = self.client.post("/lakeside/api/history/%d/undo" % back["change"], json={})
        self.assertEqual(400, reply.status_code, reply.data)
        refused = reply.get_json()["refused"]
        self.assertIn("change %d is undone" % back["change"], refused)
        self.assertNotIn("in photos the pass has not reached", refused)


if __name__ == "__main__":
    unittest.main()

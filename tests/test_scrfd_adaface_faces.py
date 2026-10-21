"""The phase 3 face model (tagpup.ml.scrfd_adaface; docs/AI_PIPELINE.md, phase 3) on the photos an index meets: a
wide photo with a crowd at the back and a face filling the front, found at the two squares and merged; a photo with no
face; one that does not decode, or is gone; and an install where the weights were never made. The models are stood in
for -- no weights load under test --: the detector answers with SCRFD's nine outputs for the square it is handed, as
the traced files do, and the recogniser is a small fixed network.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.ml import scrfd_adaface  # noqa: E402
from tagpup.ml.faces import SMALLEST_FACE, NotDetected  # noqa: E402
from tagpup.ml.scrfd_adaface import SCALES, STRIDES, TEMPLATE, ScrfdAdaFace  # noqa: E402


def landmarks_in(box):
    """Five landmarks where TEMPLATE puts them, in `box` (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = box
    return [(x1 + (x2 - x1) * x / 112, y1 + (y2 - y1) * y / 112) for x, y in TEMPLATE]


def detector_seeing(faces, side):
    """A stand-in for the traced detector at `side`: SCRFD's outputs (score, box distances, landmarks for each stride)
    holding `faces`, each (box in the square, score), found at the stride-8 place nearest its centre."""
    def detect(blob):
        assert tuple(blob.shape) == (1, 3, side, side), blob.shape
        outputs = [None] * 9
        for level, stride in enumerate(STRIDES):
            count = (side // stride) ** 2 * 2
            outputs[level] = torch.zeros(count, 1)
            outputs[level + 3] = torch.zeros(count, 4)
            outputs[level + 6] = torch.zeros(count, 10)
        stride = STRIDES[0]
        places = side // stride
        for (x1, y1, x2, y2), score in faces:
            col, row = round((x1 + x2) / 2 / stride), round((y1 + y2) / 2 / stride)
            cx, cy = col * stride, row * stride
            at = (row * places + col) * 2
            outputs[0][at, 0] = score
            outputs[3][at] = torch.tensor([cx - x1, cy - y1, x2 - cx, y2 - cy]) / stride
            marks = [v for point in landmarks_in((x1, y1, x2, y2)) for v in (point[0] - cx, point[1] - cy)]
            outputs[6][at] = torch.tensor(marks) / stride
        return outputs
    return detect


def stand_in_recogniser():
    torch.manual_seed(5)
    return torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(4), torch.nn.Flatten(), torch.nn.Linear(48, 16)).eval()


class TheNewFaceModel(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="tagpup-scrfd-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.weights = os.path.join(self.folder, "checkpoints")
        os.makedirs(self.weights)

    def photo(self, name, size=(2560, 1280)):
        path = os.path.join(self.folder, name)
        img = Image.new("RGB", size, (90, 110, 130))
        ImageDraw.Draw(img).ellipse((1700, 300, 2300, 900), fill=(220, 180, 160))
        img.save(path, quality=90)
        return path

    def model(self, at_640=(), at_1280=()):
        """The model with stand-ins loaded: faces given in the picture as stored, each scale seeing its own."""
        model = ScrfdAdaFace(device="cpu", folder=self.weights)
        width = 2560   # the photo's long side: the square's scale back is width / side

        def in_square(faces, side):
            return [(tuple(v * side / width for v in box), score) for box, score in faces]
        model.detectors = {640: detector_seeing(in_square(at_640, 640), 640),
                           1280: detector_seeing(in_square(at_1280, 1280), 1280)}
        model.recogniser = stand_in_recogniser()
        return model

    def test_a_crowd_at_the_back_and_a_face_at_the_front_are_found_at_their_own_squares(self):
        small = ((400, 160, 432, 192), 0.81)        # 16 px in the 1280 square: found there only
        noise = ((900, 160, 910, 170), 0.9)         # under SMALLEST_FACE in the picture as stored: dropped
        large = ((1712, 304, 2288, 880), 0.93)       # found at 640 only (too large for 1280's strides)
        both_640 = ((1000, 400, 1192, 592), 0.7)    # one face, seen at both: kept once, at its higher score
        both_1280 = ((1004, 404, 1196, 596), 0.88)
        edge = ((2480, 1200, 2592, 1312), 0.6)      # half outside the photo: clamped to it
        model = self.model(at_640=[large, both_640], at_1280=[small, noise, both_1280, edge])
        faces = model.detect_and_embed(model.picture(self.photo("wide.jpg"), max_side=None))

        self.assertNotIsInstance(faces, NotDetected)
        self.assertEqual([0.93, 0.88, 0.81, 0.6], [round(face["prob"], 2) for face in faces])
        boxes = [face["box"] for face in faces]
        self.assertEqual([1712, 304, 2288, 880], boxes[0])
        self.assertEqual([1004, 404, 1196, 596], boxes[1])
        self.assertEqual([400, 160, 432, 192], boxes[2])
        self.assertEqual([2480, 1200, 2560, 1280], boxes[3])
        for face in faces:
            x1, y1, x2, y2 = face["box"]
            self.assertGreaterEqual(min(x2 - x1, y2 - y1), SMALLEST_FACE)
            self.assertEqual(5, len(face["landmarks"]))
            self.assertAlmostEqual(1.0, float(np.linalg.norm(face["embedding"])), places=5)
            self.assertGreater(face["norm"], 0)
        np.testing.assert_allclose(landmarks_in((1712, 304, 2288, 880)), faces[0]["landmarks"], atol=0.5)

    def test_a_photo_with_no_face_is_found_to_have_none(self):
        model = self.model()
        model.recogniser = mock.Mock(side_effect=AssertionError("no face to make a vector of"))
        faces = model.detect_and_embed_faces(self.photo("empty.jpg"), max_side=None)
        self.assertEqual([], faces)
        self.assertNotIsInstance(faces, NotDetected)   # "none found" is recorded; "not detected" is tried again

    def test_a_photo_that_does_not_decode_or_is_gone_is_not_detected_and_loads_nothing(self):
        broken = os.path.join(self.folder, "broken.jpg")
        with open(broken, "wb") as handle:
            handle.write(b"not a picture at all")
        model = ScrfdAdaFace(device="cpu", folder=self.weights)   # no weights: a load would raise
        for path in (broken, os.path.join(self.folder, "gone.jpg")):
            found = model.detect_and_embed_faces(path, max_side=None)
            self.assertIsInstance(found, NotDetected)
            self.assertTrue(found.failed)
        self.assertFalse(model.loaded())

    def test_a_model_failing_part_way_is_not_detected(self):
        model = self.model()
        model.detectors[1280] = mock.Mock(side_effect=RuntimeError("CUDA out of memory"))
        self.assertIsInstance(model.detect_and_embed_faces(self.photo("one.jpg"), max_side=None), NotDetected)

    def test_weights_never_made_are_named_with_how_to_make_them(self):
        model = ScrfdAdaFace(device="cpu", folder=self.weights)
        with self.assertRaises(FileNotFoundError) as raised:
            model.load()
        sentence = str(raised.exception)
        for side in SCALES:
            self.assertIn(os.path.join(self.weights, "scrfd_10g_%d.pt" % side), sentence)
        self.assertIn(self.weights, sentence)
        self.assertIn(scrfd_adaface.CONVERTER, sentence)
        self.assertIn("adaface_ir101_webface12m.safetensors", sentence)
        self.assertIn("minchul/cvlface_adaface_ir101_webface12m", sentence)
        self.assertFalse(model.loaded())

        for side in SCALES:   # the detector made, the recogniser not yet downloaded
            open(os.path.join(self.weights, "scrfd_10g_%d.pt" % side), "wb").close()
        sentence = scrfd_adaface.missing_weights(self.weights)
        self.assertNotIn("scrfd_10g", sentence.split("Missing", 1)[1])
        self.assertIn("adaface_ir101_webface12m.safetensors", sentence)

        open(os.path.join(self.weights, scrfd_adaface.RECOGNISER_FILE), "wb").close()
        self.assertIsNone(scrfd_adaface.missing_weights(self.weights))
        with self.assertRaisesRegex(RuntimeError, "under test"):   # all there: only a test run stops it now
            model.load()

    def test_faces_in_chunks_get_the_vectors_one_batch_gives(self):
        model = ScrfdAdaFace(device="cpu", folder=self.weights)
        recogniser = stand_in_recogniser()
        rng = np.random.default_rng(3)
        crops = [rng.integers(0, 256, (112, 112, 3), dtype=np.uint8) for _ in range(11)]
        with mock.patch.object(scrfd_adaface, "CHUNK", 4):
            chunked = model.embed(recogniser, crops)
        with mock.patch.object(scrfd_adaface, "CHUNK", 64):
            whole = model.embed(recogniser, crops)
        self.assertEqual(11, len(chunked))
        np.testing.assert_allclose([v for v, _n in whole], [v for v, _n in chunked], atol=1e-5)
        np.testing.assert_allclose([n for _v, n in whole], [n for _v, n in chunked], rtol=1e-5)

    def test_a_turned_and_scaled_face_is_aligned_onto_the_template(self):
        turn = np.radians(30)
        rotation = 3 * np.array([[np.cos(turn), -np.sin(turn)], [np.sin(turn), np.cos(turn)]])
        landmarks = np.asarray(TEMPLATE) @ rotation.T + [200, 150]
        matrix = scrfd_adaface.similarity(landmarks, TEMPLATE)
        np.testing.assert_allclose(TEMPLATE, landmarks @ matrix[:, :2].T + matrix[:, 2], atol=1e-6)

        img = Image.new("RGB", (600, 600))
        nose = landmarks[2]
        ImageDraw.Draw(img).ellipse((nose[0] - 9, nose[1] - 9, nose[0] + 9, nose[1] + 9), fill=(255, 255, 255))
        face = scrfd_adaface.aligned(img, landmarks)
        self.assertEqual((112, 112, 3), face.shape)
        self.assertGreater(face[72, 56].min(), 200)   # the nose where the template puts it (x 56.0, y 71.7)
        self.assertEqual(0, face[20, 20].max())


if __name__ == "__main__":
    unittest.main()

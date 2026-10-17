"""A model used while the idle release lets it go still answers, and runs without gradients being tracked.

`ClipModel.embed_text` read the tokenizer before it took the model's lock and the weights inside it, and `unload`
drops both under that lock: a text embedded in the moment between raised "'NoneType' object is not callable" from
a search box. The same for the face models. Inference runs in `torch.inference_mode`, which tracks nothing at all
(`no_grad` still builds the version counters). The weights are stand-ins; nothing loads (tests/test_no_test_loads_a_model.py).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.ml import clip, faces  # noqa: E402


class Weights:
    """encode_text / encode_image / the embedder, noting whether it ran in inference mode."""

    def __init__(self, size=4):
        self.modes = []
        self.size = size

    def encode_text(self, tokens):
        self.modes.append(torch.is_inference_mode_enabled())
        return torch.ones(1, self.size)

    def encode_image(self, tensor):
        self.modes.append(torch.is_inference_mode_enabled())
        return torch.ones(1, self.size)

    def __call__(self, batch):
        self.modes.append(torch.is_inference_mode_enabled())
        return torch.ones(len(batch), 512)


def a_clip(weights):
    model = clip.ClipModel("ViT-B-32", "openai", True, 1.4, None, device="cpu")

    def load():
        if model.model is None:
            model.model, model.preprocess = weights, lambda picture: torch.zeros(3, 2, 2)
            model.tokenizer = lambda texts: torch.zeros(1, 3)

    return model, load


class TheTextModelLetGoBetweenTheCheckAndTheLock(unittest.TestCase):
    def test_a_text_embedded_while_the_model_is_let_go_loads_it_again(self):
        weights = Weights()
        model, load = a_clip(weights)
        calls = []

        def init():
            calls.append(1)
            load()
            if len(calls) == 1:
                model.unload()          # the idle release, after the check and before the lock

        with mock.patch.object(model, "_init_model", init), mock.patch.object(clip, "free_device_memory"):
            vector = model.embed_text("a harbour at dusk")
        self.assertEqual(4, len(vector))
        self.assertEqual(2, len(calls))

    def test_a_text_and_a_picture_are_embedded_without_tracking_gradients(self):
        weights = Weights()
        model, load = a_clip(weights)
        with mock.patch.object(model, "_init_model", load):
            model.embed_text("a harbour at dusk")
            model.embed_picture(Image.new("RGB", (8, 8)))
        self.assertEqual([True, True], weights.modes)

    def test_a_model_let_go_every_time_it_is_loaded_says_so(self):
        model, load = a_clip(Weights())

        def init():
            load()
            model.unload()

        with mock.patch.object(model, "_init_model", init), mock.patch.object(clip, "free_device_memory"):
            with self.assertRaisesRegex(RuntimeError, "let go"):
                model.embed_text("a harbour at dusk")


class TheFaceModelsLetGoMidPhoto(unittest.TestCase):
    def test_a_photo_detected_while_the_models_are_let_go_finishes_with_the_ones_it_had(self):
        weights = Weights()
        model = faces.FaceModel(40, 0.9, [0.6, 0.7, 0.7], device="cpu")

        class Detector:
            def detect(self, picture):
                model.unload()          # the idle release, while this photo is in the detector
                return np.array([[0.0, 0.0, 60.0, 60.0]]), [0.99]

        def init():
            model.mtcnn, model.resnet = Detector(), weights

        with tempfile.TemporaryDirectory() as folder:
            photo = os.path.join(folder, "one.jpg")
            Image.new("RGB", (80, 80), "navy").save(photo, "JPEG")
            with mock.patch.object(model, "_init_models", init), mock.patch.object(faces, "free_device_memory"):
                found = model.detect_and_embed_faces(photo, max_side=None)
        self.assertEqual(1, len(found))
        self.assertEqual(512, len(found[0]["embedding"]))
        self.assertEqual([True], weights.modes)


if __name__ == "__main__":
    unittest.main()

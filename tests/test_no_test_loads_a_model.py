"""No test loads real model weights: both loaders refuse under the tests package's flag.

One test loaded ViT-H-14 and the face models for a whole port without failing: its
patches named classes the CLI had stopped using, and still applied (docs/findings.md).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup import ml  # noqa: E402


class TheLoadersRefuse(unittest.TestCase):
    def test_a_test_run_is_known_without_the_flag(self):
        # `unittest discover -s tests` never imports the tests package, which sets it.
        with mock.patch.dict(os.environ):
            os.environ.pop(ml.NO_WEIGHTS, None)
            self.assertTrue(ml.under_test())

    def test_clip_refuses(self):
        from tagpup.ml.clip import ClipModel
        model = ClipModel("ViT-B-32", "openai", True, 1.4, None, device="cpu")
        with self.assertRaisesRegex(RuntimeError, "under test"):
            model.load()

    def test_the_face_models_refuse(self):
        from tagpup.ml.faces import FaceModel
        model = FaceModel(40, 0.9, [0.6, 0.7, 0.7], device="cpu")
        with self.assertRaisesRegex(RuntimeError, "under test"):
            model.load()


class TheModelsLetGo(unittest.TestCase):
    """unload(): the runtime drops a model no library it serves uses, and a model dropped
    kept its weights on the GPU. The weights here are stand-ins; nothing loads."""

    def unloaded(self, model, weights):
        import torch
        with mock.patch.object(torch.cuda, "is_available", return_value=True), \
                mock.patch.object(torch.cuda, "empty_cache") as empty_cache:
            model.unload()
        for name in weights:
            self.assertIsNone(getattr(model, name), name)
        empty_cache.assert_called_once_with()

    def test_clip_lets_its_weights_and_the_gpu_memory_go(self):
        from tagpup.ml.clip import ClipModel
        model = ClipModel("ViT-B-32", "openai", True, 1.4, None, device="cpu")
        model.model, model.preprocess, model.tokenizer = object(), object(), object()
        self.unloaded(model, ("model", "preprocess", "tokenizer"))
        with self.assertRaisesRegex(RuntimeError, "under test"):
            model.load()   # used again, it would load again

    def test_the_face_models_let_their_weights_and_the_gpu_memory_go(self):
        from tagpup.ml.faces import FaceModel
        model = FaceModel(40, 0.9, [0.6, 0.7, 0.7], device="cpu")
        model.mtcnn, model.resnet = object(), object()
        self.unloaded(model, ("mtcnn", "resnet"))
        with self.assertRaisesRegex(RuntimeError, "under test"):
            model.load()

    def test_a_model_never_loaded_has_nothing_to_let_go(self):
        import torch
        from tagpup.ml.clip import ClipModel
        with mock.patch.object(torch.cuda, "empty_cache") as empty_cache:
            ClipModel("ViT-B-32", "openai", True, 1.4, None, device="cpu").unload()
        empty_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()

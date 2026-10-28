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


def face_model(test):
    """The face model on the processor, its weight files there (empty: nothing reads them before the refusal)."""
    import tempfile
    from tagpup.ml import scrfd_adaface
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    for name in [scrfd_adaface.DETECTOR_FILE % side for side in scrfd_adaface.SCALES] + [scrfd_adaface.RECOGNISER_FILE]:
        open(os.path.join(folder.name, name), "wb").close()
    return scrfd_adaface.ScrfdAdaFace(device="cpu", folder=folder.name)


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
        with self.assertRaisesRegex(RuntimeError, "under test"):
            face_model(self).load()


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
        model = face_model(self)
        model.detectors, model.recogniser = {640: object()}, object()
        self.unloaded(model, ("detectors", "recogniser"))
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

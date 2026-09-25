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


if __name__ == "__main__":
    unittest.main()

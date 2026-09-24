"""CLIP is shown a photo the right way up.

A camera held on its side stores the pixels sideways and says so in the EXIF
Orientation tag, and Rotate now does the same rather than re-encode the photo. The
embedder opened the file and used the stored pixels as they were, so CLIP described a
sideways picture, and suggestions and search were made from that.
"""
import os
import shutil
import sys
import tempfile
import unittest

import torch
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from embedder import ClipEmbedder  # noqa: E402


class FakeModel:
    def encode_image(self, batch):
        return torch.ones((1, 4))


class ClipSeesPhotosUpright(unittest.TestCase):
    def test_a_photo_stored_sideways_is_embedded_upright(self):
        tmp = tempfile.mkdtemp(prefix="clip_upright_")
        self.addCleanup(shutil.rmtree, tmp, True)
        photo = os.path.join(tmp, "portrait.jpg")
        img = Image.new("RGB", (40, 20))       # stored landscape...
        exif = img.getexif()
        exif[0x0112] = 6                      # ...shown turned a quarter: portrait
        img.save(photo, exif=exif.tobytes())

        seen = []
        embedder = ClipEmbedder(cache_dir=os.path.join(tmp, "cache"))
        embedder.device = "cpu"

        def init_model():
            embedder.model = FakeModel()
            embedder.preprocess = lambda image: (seen.append(image.size), torch.zeros((3, 2, 2)))[1]

        embedder._init_model = init_model
        embedder.embed_image(photo, force_recompute=True)

        self.assertEqual([(20, 40)], seen, "CLIP was shown the stored, sideways pixels")


if __name__ == "__main__":
    unittest.main()

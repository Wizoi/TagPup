"""Rules that were written more than once, each now said in one place (docs/findings.md,
#74). The copies agreed when they were found; nothing kept them agreeing. Each class
here holds one rule to its owner: a copy that reappears fails, and where a page must
keep a copy of its own, the page is pinned to the owner.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from shipped_sources import python_sources  # noqa: E402


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
        return f.read()


def sources_matching(pattern, owner=None):
    """Shipped Python files other than `owner` whose text matches `pattern`."""
    owner = os.path.normcase(owner) if owner else None
    return [path for path in python_sources()
            if os.path.normcase(path) != owner and re.search(pattern, read(path))]


class TheTinyBackgroundFace(unittest.TestCase):
    """A face far smaller than the largest in its photo, and small outright, is noise in
    the background: clustering gives it no name and the suggester offers none."""

    def test_is_decided_in_clustering(self):
        from tagpup.core import clustering
        big, small, middling = [0, 0, 100, 100], [0, 0, 20, 20], [0, 0, 40, 60]
        self.assertEqual({1}, clustering.background_faces([big, small, middling]))
        # Small beside a larger face, but not small outright: 50 x 50 is 2,500.
        self.assertEqual(set(), clustering.background_faces([[0, 0, 400, 400], [0, 0, 50, 50]]))
        # Alone in its photo, a face is never background.
        self.assertEqual(set(), clustering.background_faces([small]))
        self.assertEqual(set(), clustering.background_faces([]))
        self.assertEqual({1}, clustering.background_faces([big, None]))

    def test_nothing_else_writes_it(self):
        self.assertEqual([], sources_matching(r"0\.10?\s*\*\s*max_area|area\s*<\s*2000",
                                              os.path.join("tagpup", "core", "clustering.py")))


class TheFaceCrop(unittest.TestCase):
    """A face's crop is kept at one size and one JPEG quality, whoever cuts it: face
    detection cut its own, beside the one tagpup.files.images cuts for a face whose row
    has none."""

    def test_is_encoded_by_images(self):
        import io

        from PIL import Image

        from tagpup.files import images
        crop = images.crop_jpeg(Image.new("RGB", (600, 300), (120, 80, 40)))
        with Image.open(io.BytesIO(crop)) as back:
            self.assertEqual("JPEG", back.format)
            self.assertEqual((images.CROP_SIZE, images.CROP_SIZE // 2), back.size)
        small = images.crop_jpeg(Image.new("RGB", (40, 60)))
        with Image.open(io.BytesIO(small)) as back:
            self.assertEqual((40, 60), back.size)

    def test_nothing_else_encodes_a_jpeg(self):
        self.assertEqual([], sources_matching(r"format\s*=\s*['\"]JPEG['\"]",
                                              os.path.join("tagpup", "files", "images.py")))


class OpeningAPhoto(unittest.TestCase):
    """A photo's pixels are read by tagpup.files.images: the embedder opened photos and
    turned them upright itself, and four modules each raised Pillow's size limit."""

    def test_images_opens_it_upright_or_as_stored(self):
        import tempfile

        from PIL import Image

        from tagpup.files import images
        folder = tempfile.mkdtemp(prefix="open_photo_")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        photo = os.path.join(folder, "sideways.png")
        img = Image.new("L", (40, 20))
        exif = img.getexif()
        exif[0x0112] = 6
        img.save(photo, exif=exif.tobytes())
        upright = images.opened(photo, upright=True)
        self.assertEqual(((20, 40), "RGB"), (upright.size, upright.mode))
        self.assertEqual((40, 20), images.opened(photo, upright=False).size)
        self.assertGreaterEqual(Image.MAX_IMAGE_PIXELS, 500_000_000)

    def test_nothing_else_opens_one(self):
        # The tutorial's seeding copies a PNG without its metadata: a file, not a photo.
        found = sources_matching(r"Image\.open\(|exif_transpose\(|MAX_IMAGE_PIXELS")
        self.assertEqual([], [path for path in found
                              if not path.startswith(os.path.join("tagpup", "files"))
                              and path != os.path.join("scripts", "prepare_test_environment.py")])


class WhatClipIsAsked(unittest.TestCase):
    """The words CLIP scores a photo against, and the sentence each is put in, are
    tagpup.core.suggesting's: the server and the CLI each merged the tree's words into
    config.ini's, one sorting them and one not, and the suggester wrote its prompts."""

    def test_the_words(self):
        from tagpup.core import suggesting
        words = suggesting.zero_shot_words(
            ["Beach", "sunset "], ["Trips/Coast", "People/Oda Castellane", "Activity/Beach", "Activity/ Rowing"],
            {"people"})
        self.assertEqual(["Beach", "sunset", "Rowing", "Coast"], words)

    def test_the_prompts(self):
        from tagpup.core import suggesting
        self.assertEqual("a photo of a sunset", suggesting.clip_prompt("Sunset"))
        self.assertEqual("a photo of a sunset in 1998", suggesting.clip_prompt("Sunset", 1998))
        self.assertEqual("a photo of Oda Castellane in 1998",
                         suggesting.clip_prompt("Oda Castellane", 1998, person=True))

    def test_nothing_else_writes_them(self):
        self.assertEqual([], sources_matching(r"[\"']a photo of",
                                              os.path.join("tagpup", "core", "suggesting.py")))
        for path in sources_matching(r"config\.candidate_tags\("):
            for line in read(path).splitlines():
                if re.search(r"config\.candidate_tags\(", line):
                    self.assertIn("zero_shot_words(", line, path)


class TheLengthOfAPhotosVector(unittest.TestCase):
    """How long a model's vectors are is asked of open_clip, in the embedder: the CLI
    guessed it from the model's name three times, 512 for anything not ViT-L or ViT-H,
    and so cleared every vector on each index with ViT-bigG-14, whose are 1280."""

    def test_is_the_models_own(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import embedder
        self.assertEqual(1280, embedder.output_dim("ViT-bigG-14"))
        self.assertEqual(1024, embedder.output_dim("ViT-H-14"))
        self.assertEqual(512, embedder.output_dim("ViT-B-32"))
        self.assertIsNone(embedder.output_dim("hf-hub:somebody/unknown-model"))

    def test_the_library_is_compared_with_it(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import embedder

        class Index:
            def __init__(self, d):
                self.index = type("Vectors", (), {"d": d})() if d else None

        self.assertIsNone(embedder.stored_mismatch(Index(1280), "ViT-bigG-14"))
        self.assertIsNone(embedder.stored_mismatch(Index(None), "ViT-bigG-14"))
        self.assertIsNone(embedder.stored_mismatch(Index(7), "hf-hub:somebody/unknown-model"))
        self.assertEqual((512, 1280), embedder.stored_mismatch(Index(512), "ViT-bigG-14"))

    def test_nobody_guesses_it_from_the_name(self):
        guess = r"[\"']ViT-[LH][\"']\s+in\b"
        found = sources_matching(guess)
        tests = os.path.join(ROOT, "tests")
        found += [name for name in os.listdir(tests) if name.endswith(".py")
                  and name != "test_rules_have_one_owner.py" and re.search(guess, read(os.path.join("tests", name)))]
        self.assertEqual([], found)


if __name__ == "__main__":
    unittest.main()

"""What counts as a photo is said once: tagpup.files.images (docs/findings.md, #72).

It was written eight times. Relink took .heic, which nothing scans, so it could point a
row at a file the index never reads; the image server sent bmp, gif, heic and heif, and
everything but png and webp as image/jpeg.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import images  # noqa: E402

OWNER = os.path.join("tagpup", "files", "images.py")


class OneList(unittest.TestCase):
    def test_no_other_module_lists_the_photo_extensions(self):
        # A literal naming two photo extensions is a list of them -- unless it lists the
        # page's own files (.css, .js), which is another thing.
        literal = re.compile(r"[(\[{][^()\[\]{}]*[)\]}]")
        photo = re.compile(r"[\"']\.(?:jpe?g|png|tiff?|webp|heic)[\"']", re.IGNORECASE)
        found = []
        for relative in python_sources():
            if relative == OWNER:
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
                for listed in literal.findall(f.read()):
                    if len(photo.findall(listed)) >= 2 and not re.search(r"[\"']\.(?:css|js|html)[\"']", listed):
                        found.append(relative)
        self.assertEqual([], found)

    def test_a_photo_goes_out_as_what_it_is(self):
        self.assertEqual("image/tiff", images.content_type("D:/a.TIF"))
        self.assertEqual("image/jpeg", images.content_type("D:/a.jpeg"))
        self.assertEqual("image/webp", images.content_type("D:/a.webp"))

    def test_what_nothing_scans_is_not_a_photo(self):
        for name in ("a.heic", "a.gif", "a.bmp", "a.txt"):
            self.assertFalse(images.is_photo(name), name)
        self.assertTrue(images.is_photo("A.JPG"))


class PhotosUnderAFolder(unittest.TestCase):
    def test_are_walked_from_the_stored_spelling_at_any_depth(self):
        root = tempfile.mkdtemp(prefix="photos_under_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "Heats"))
        for name in ("a.jpg", os.path.join("Heats", "b.PNG"), "notes.txt", "c.heic"):
            with open(os.path.join(root, name), "wb") as f:
                f.write(b"x")
        found = images.photos_under(root.replace(chr(92), "/"))
        self.assertEqual(sorted([paths.stored(os.path.join(root, "a.jpg")),
                                 paths.stored(os.path.join(root, "Heats", "b.PNG"))]), sorted(found))


if __name__ == "__main__":
    unittest.main()

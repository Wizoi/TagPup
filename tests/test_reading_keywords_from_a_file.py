"""tagpup.files.keywords.tags_in_file raises for a file it cannot read as a photo.

A file ExifTool cannot parse read as a photo with no tags. Replacing a tag then took it
for one that did not carry the tag, and rewrote its index row to no tags
(docs/findings.md, #46). These run ExifTool on real files.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exiftool.exceptions import ExifToolExecuteError  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.files import exiftool_session, keywords  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

#: Where the machine has ExifTool; the checkout's settings are not read.
EXIFTOOL = own_home.installed_exiftool()
requires_exiftool = unittest.skipUnless(EXIFTOOL and os.path.exists(EXIFTOOL), "ExifTool not installed")


@requires_exiftool
class ReadingKeywords(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reading_keywords_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def read(self, path):
        with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
            return keywords.tags_in_file(et, path)

    def test_a_photo_with_no_keywords_has_no_tags(self):
        photo = os.path.join(self.dir, "plain.jpg")
        Image.new("RGB", (4, 4)).save(photo)
        self.assertEqual(self.read(photo), [])

    def test_a_file_that_is_not_an_image_raises(self):
        fake = os.path.join(self.dir, "fake.jpg")
        with open(fake, "wb") as f:
            f.write(b"not a picture")
        with self.assertRaises(ValueError):
            self.read(fake)

    def test_a_missing_file_raises(self):
        with self.assertRaises(ExifToolExecuteError):
            self.read(os.path.join(self.dir, "gone.jpg"))


if __name__ == "__main__":
    unittest.main()

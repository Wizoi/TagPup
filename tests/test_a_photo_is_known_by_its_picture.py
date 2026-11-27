"""A photo's fingerprint is its picture's, not its file's (tagpup.files.images.fingerprint; docs/PHASE6_DESIGN.md, section 7).

A bake carries the fingerprint of the picture it describes, and is used only while the picture is still that one. The
owner's library writes keywords, captions and identities into its files and renames them: none of that changes what the
picture is, and a fingerprint of the file's bytes would call each one a different photo. A rotation does change the
picture as it is shown (the Orientation turns what the vectors were made of), and so does an edit of its pixels.

Real ExifTool for the writes a library makes (field_values.write, the one writer), on JPEGs made here in a home of the
test's own.
"""
import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from tagpup.core import fields  # noqa: E402
from tagpup.files import field_values, images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()


def picture(size=(48, 32)):
    """A picture whose every pixel differs from its neighbours, so a turn or an edit shows in its bytes."""
    img = Image.new("RGB", size)
    img.putdata([((x * 7) % 256, (y * 11) % 256, (x * y) % 256) for y in range(size[1]) for x in range(size[0])])
    return img


class APhotoIsKnownByItsPicture(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="fingerprint_")

    def path(self, name):
        return os.path.join(self.home.root, name)

    def of(self, path):
        return images.fingerprint(images.opened(path, upright=True))

    def saved(self, name, img=None, **options):
        path = self.path(name)
        (img or picture()).save(path, **options)
        return path

    @unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
    def test_a_keyword_write_and_an_identity_leave_it(self):
        path = self.saved("Harbour.jpg", quality=90)
        before = self.of(path)
        size = os.path.getsize(path)
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values._write(et, path, {"XMP:Subject": ["Places/Harbour"], "IPTC:Keywords": ["Places/Harbour"],
                                          "XMP:Description": "A quay at dusk"})
            field_values.ensure_document_id(et, path)
            held = field_values._read_one(et, path, ["XMP:Subject"])
        self.assertEqual(["Places/Harbour"], held["XMP:Subject"])
        self.assertNotEqual(size, os.path.getsize(path), "the file's bytes changed")
        self.assertEqual(before, self.of(path))

    def test_a_rename_leaves_it(self):
        path = self.saved("Harbour.jpg", quality=90)
        before = self.of(path)
        renamed = self.path("Quay at dusk.jpg")
        os.rename(path, renamed)
        self.assertEqual(before, self.of(renamed))

    @unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
    def test_a_rotation_changes_it(self):
        path = self.saved("Harbour.jpg", quality=90)
        before = self.of(path)
        with ExifToolSession(executable=EXIFTOOL) as et:
            # What a rotation writes: the Orientation alone (tagpup.services.rotation), never the pixels.
            field_values._write(et, path, {fields.ORIENTATION: "6"})
        after = self.of(path)
        self.assertNotEqual(before, after)
        stored = images.opened(path, upright=False)
        self.assertEqual(images.fingerprint(stored.transpose(Image.Transpose.ROTATE_270)), after,
                         "the fingerprint is the upright picture's")

    def test_a_pixel_edit_changes_it(self):
        path = self.saved("Harbour.png")
        before = self.of(path)
        edited = picture()
        edited.putpixel((5, 5), (255, 0, 0))
        edited.save(path)
        self.assertNotEqual(before, self.of(path))

    def test_a_picture_of_one_colour_turned_a_quarter_is_another_picture(self):
        wide = self.saved("Wall.png", Image.new("RGB", (40, 30), (90, 120, 150)))
        tall = self.saved("Wall turned.png", Image.new("RGB", (30, 40), (90, 120, 150)))
        wide_img, tall_img = images.opened(wide, upright=True), images.opened(tall, upright=True)
        self.assertEqual(wide_img.tobytes(), tall_img.tobytes(), "the same pixel bytes")
        self.assertNotEqual(images.fingerprint(wide_img), images.fingerprint(tall_img))

    def test_an_alpha_channel_and_a_colour_profile_are_not_the_picture(self):
        rgb = picture()
        clear, opaque = rgb.convert("RGBA"), rgb.convert("RGBA")
        clear.putalpha(40)
        self.assertEqual(self.of(self.saved("Opaque.png", opaque)), self.of(self.saved("Clear.png", clear)))
        plain = self.saved("Plain.jpg", rgb, quality=90)
        profiled = self.saved("Profiled.jpg", rgb, quality=90, icc_profile=b"\0" * 128)
        self.assertNotEqual(os.path.getsize(plain), os.path.getsize(profiled))
        self.assertEqual(self.of(plain), self.of(profiled))

    def test_the_decoder_is_the_pinned_one(self):
        """A new Pillow can decode other pixels: it is a new FINGERPRINT_KEY, so the pin and the installed copy agree."""
        import PIL
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "requirements.txt"),
                  encoding="utf-8") as handle:
            pinned = [line.split("==")[1].strip() for line in handle if line.startswith("Pillow==")]
        self.assertEqual([PIL.__version__], pinned)

    def test_its_key_and_shape(self):
        self.assertEqual("upright-sha256-1", images.FINGERPRINT_KEY)
        self.assertEqual(32, len(images.fingerprint(picture())))


if __name__ == "__main__":
    unittest.main()

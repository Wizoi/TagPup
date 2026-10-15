"""The follow-up review's findings #528-#531 (docs/findings.md).

#528  The file-only check is at parity with the held path: it refuses an empty file or a zero-filled tail by
      images.zero_tail_of's own rule, and nothing else -- not what follows the end of the picture.
#529, #530  (goes_to_bin, the long name, the true reason of a permanent delete: tests/test_delete_a_photo.py.)
#531  Any exception after the held part of a Smart Rename committed answers what did change.
"""
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import test_just_look_edits as jl  # noqa: E402

from tagpup.files import images  # noqa: E402
from tagpup.services import file_only  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


def whole_jpeg_bytes(path, seed=3):
    return damaged_photos.whole_jpeg(path, seed=seed)


class TheCheckIsAtParityWithTheHeldPath(jl.Base):
    """Each variant is a photo that decodes in Pillow and that the held path writes without any check."""

    def variants(self):
        """{name: path}, each made in the Lighthouse folder from a whole JPEG or PNG."""
        from PIL import Image
        made = {}

        def jpeg(name, tail=b"", **save):
            path = os.path.join(self.lighthouse, name)
            if save:
                Image.frombytes("RGB", (160, 120), random.Random(5).randbytes(160 * 120 * 3)).save(path, "JPEG", **save)
            else:
                whole_jpeg_bytes(path, seed=len(made) + 40)
            with open(path, "ab") as handle:
                handle.write(tail)
            made[name] = path

        jpeg("samsung trailer.jpg", b"\x00\x00\x00SEFH\x00\x00\x00\x01" + b"SEFT" * 40 + b"\x01\x02\x03")
        jpeg("motion photo.jpg", random.Random(9).randbytes(3 * 1024 * 1024) + b"ftypmp42")
        jpeg("ff padding.jpg", b"\xff" * 700)
        jpeg("zeros 4097.jpg", bytes(4097))
        jpeg("zeros 65535.jpg", bytes(65535))
        jpeg("progressive.jpg", progressive=True, quality=90)
        png = os.path.join(self.lighthouse, "trailing bytes.png")
        Image.new("RGB", (32, 32), "blue").save(png)
        with open(png, "ab") as handle:
            handle.write(b"trailer written by a camera app")
        made["trailing bytes.png"] = png
        return made

    def test_every_one_is_written(self):
        for name, path in self.variants().items():
            reply = self.post("/photo/save-metadata", {"path": path, "title": "x", "tags": ["A/B"]})
            self.assertEqual(200, reply.status_code, (name, reply.data))
            self.assertEqual(["A/B"], jl.tags_in(path), name)
        self.assert_library_unchanged()

    def test_a_jpeg_with_an_exif_thumbnail(self):
        from PIL import Image
        thumb = os.path.join(self.home.root, "thumb.jpg")
        Image.new("RGB", (16, 12), "red").save(thumb, "JPEG")
        photo = self.loose[0]
        with jl.ExifToolSession(executable=jl.EXIFTOOL) as et:
            et.execute("-ThumbnailImage<=" + thumb, "-overwrite_original", photo)
            held = et.get_tags([photo], tags=["ThumbnailImage"])[0]
        self.assertIn("ThumbnailImage", " ".join(held), "the fixture holds no thumbnail")
        self.assertEqual(200, self.post("/photo/save-metadata", {"path": photo, "title": "x", "tags": []}).status_code)

    def test_a_zero_filled_tail_and_an_empty_file_are_refused(self):
        filled = os.path.join(self.lighthouse, "zero filled.jpg")
        whole_jpeg_bytes(filled)
        with open(filled, "ab") as handle:
            handle.write(bytes(images.ZERO_TAIL + 5000))
        empty = os.path.join(self.lighthouse, "empty.jpg")
        open(empty, "wb").close()
        for path in (filled, empty):
            self.assertEqual(409, self.post("/photo/save-metadata", {"path": path, "title": "x",
                                                                    "tags": []}).status_code, path)

    def test_the_two_checks_agree_on_a_table_of_tails(self):
        tails = [b"", b"\xff\xd9", bytes(1), bytes(4096), bytes(4097), bytes(images.ZERO_TAIL - 1),
                 bytes(images.ZERO_TAIL), bytes(images.ZERO_TAIL + 1), bytes(3 * images.ZERO_TAIL),
                 b"\x01" + bytes(images.ZERO_TAIL - 1), bytes(images.ZERO_TAIL - 1) + b"\x01",
                 b"\xff" * (images.ZERO_TAIL + 10), b"x" * 70000]
        for n, tail in enumerate(tails):
            path = os.path.join(self.lighthouse, "tail %d.jpg" % n)
            whole_jpeg_bytes(path, seed=n)
            with open(path, "ab") as handle:
                handle.write(tail)
            refused_by_held_rule = images.zero_tail_of(path) >= images.ZERO_TAIL
            self.assertEqual(refused_by_held_rule, bool(file_only.unwritable([path])), (n, len(tail)))


class AnyFailureAfterTheHeldPartAnswersWhatChanged(jl.Base):
    def rename(self):
        return self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                  "photo_paths": self.held + [self.scan]})

    def test_whatever_the_second_part_raises(self):
        for error in (OSError("the share went away"), KeyError("x")):
            with mock.patch.object(file_only, "rename", side_effect=error):
                reply = self.rename()
            self.assertEqual(500, reply.status_code, (error, reply.data))
            body = reply.get_json()
            self.assertEqual(2, len(body["updated_paths"]), (error, body))
            shown = sorted(os.path.basename(p["path"]) for p in body["updated_photos"] if "Scans" not in p["path"])
            self.assertTrue(all(name.startswith("Regatta - ") for name in shown), shown)
            self.assertEqual(["scan_01.jpg"], os.listdir(self.scans))
            # put the files back for the next round
            for old, new in body["updated_paths"].items():
                os.rename(new, old)
                from tagpup.store import db
                db.write_with_connection(self.library.path, lambda conn, o=old, n=new: conn.execute(
                    "UPDATE photos SET path = ? WHERE path = ?", (o, n)))
            tagpup_routes.folders.forget(self.library)

    def test_a_folder_that_cannot_be_read_again_is_a_plain_500_and_the_cache_is_dropped(self):
        folder = tagpup_routes.folders.of(self.library)
        folder.put(self.regatta, {"stale": {"path": self.held[0]}})
        with mock.patch.object(file_only, "rename", side_effect=OSError("gone")), \
                mock.patch.object(tagpup_routes.photo_actions, "read_folder", side_effect=OSError("share gone")):
            reply = self.rename()
        self.assertEqual(500, reply.status_code, reply.data)
        self.assertNotIn("updated_paths", reply.get_json())
        self.assertIsNone(folder.get(self.regatta), "a cache of the old names was kept")


if __name__ == "__main__":
    unittest.main()

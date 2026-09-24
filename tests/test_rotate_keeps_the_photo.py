"""Rotating a photo turns it, and changes nothing else about it.

Rotate Left and Rotate Right used to decode the photo with Pillow, turn the pixels and
save it again, passing only the EXIF block. Every XMP and IPTC field went with the old
file -- keywords, the people hierarchy, the caption, the DocumentID the index knows the
photo by -- and the JPEG was re-encoded at quality 95 on every click. The face boxes
stayed where they had been, now over a different part of a turned picture.

A rotation is now a change of the EXIF Orientation tag, which browsers and the app's
previews honour. The stored pixels are untouched, so the face boxes, which are in the
coordinates Pillow shows the photo in, stay right -- except for a TIFF, which Pillow
shows already oriented, where they are turned with it.
"""
import json
import os
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

import tagpup_server
from exiftool_session import ExifToolSession
from metadata import ROTATED_ORIENTATION, rotate_image_file
from tagpup.store import db
from tests.face_rows import add_face


def face_in(lib, path, box, **columns):
    """A face in the photo at `path`, which gets a row holding only the path if it has
    none. Returns the face's id."""
    conn = db.connect(lib.db_path)
    try:
        face_id = add_face(conn, path, box, **columns)
        conn.commit()
        return face_id
    finally:
        conn.close()


FIELDS = {
    "XMP:Subject": ["Beach", "People", "Rowan Thackeray"],
    "IPTC:Keywords": ["Beach", "People", "Rowan Thackeray"],
    "XMP:HierarchicalSubject": ["People/Rowan Thackeray"],
    "XMP:Description": "Rowan at the beach",
    "IPTC:Caption-Abstract": "Rowan at the beach",
    "XMP-xmpMM:DocumentID": "xmp.did:3f2a9c1e-0000-4000-8000-00000000abcd",
}
READ_BACK = ["XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject", "XMP:Description",
             "IPTC:Caption-Abstract", "XMP:DocumentID", "EXIF:Orientation"]


def make_picture(path, size=(60, 40), fmt=None):
    """A picture with a marked corner, so a turn is visible in the pixels."""
    from PIL import Image

    img = Image.new("RGB", size, (40, 60, 80))
    for x in range(10):
        for y in range(8):
            img.putpixel((x, y), (250, 20, 20))
    img.save(path, fmt) if fmt else img.save(path)
    return path


def write_fields(path, fields=FIELDS):
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=dict(fields), params=["-overwrite_original"])


def read_fields(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        found = et.get_tags([path], tags=READ_BACK)[0]
    found.pop("SourceFile", None)
    return found


def stored_pixels(path):
    from PIL import Image

    with Image.open(path) as img:
        return img.size, img.tobytes()


def shown(path):
    """What a viewer shows: the stored pixels with the Orientation applied."""
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


class TestTheOrientationTable(unittest.TestCase):
    """The table is what exif_transpose shows, turned, for every starting value."""

    def test_every_orientation_turns_the_way_it_is_shown(self):
        from PIL import Image, ImageOps

        base = Image.new("RGB", (3, 2))
        base.putdata([(i, i, i) for i in range(6)])

        def with_orientation(value):
            img = base.copy()
            exif = img.getexif()
            exif[0x0112] = value
            img.info["exif"] = exif.tobytes()
            return ImageOps.exif_transpose(img)

        for direction, angle in (("left", 90), ("right", 270)):
            for before in range(1, 9):
                after = ROTATED_ORIENTATION[direction][before]
                want = with_orientation(before).rotate(angle, expand=True)
                got = with_orientation(after)
                self.assertEqual((got.size, got.tobytes()), (want.size, want.tobytes()),
                                 "%s from %d" % (direction, before))


@requires_exiftool
class TestRotateImageFile(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.photo = make_picture(os.path.join(self.lib.photos, "beach.jpg"))
        write_fields(self.photo)

    def test_every_keyword_and_caption_field_survives_both_turns(self):
        before = read_fields(self.photo)
        for direction in ("left", "right", "right", "left"):
            rotate_image_file(self.photo, direction, EXIFTOOL)
            after = read_fields(self.photo)
            for field in READ_BACK:
                if field == "EXIF:Orientation":
                    continue
                self.assertEqual(after.get(field), before.get(field),
                                 "%s after rotating %s" % (field, direction))

    def test_the_pixels_are_not_re_encoded(self):
        before = stored_pixels(self.photo)
        rotate_image_file(self.photo, "left", EXIFTOOL)
        self.assertEqual(stored_pixels(self.photo), before)
        rotate_image_file(self.photo, "right", EXIFTOOL)
        self.assertEqual(stored_pixels(self.photo), before)

    def test_left_turns_what_is_shown_a_quarter_counter_clockwise(self):
        before = shown(self.photo)
        self.assertEqual(rotate_image_file(self.photo, "left", EXIFTOOL), 8)
        after = shown(self.photo)
        want = before.rotate(90, expand=True)
        self.assertEqual((after.size, after.tobytes()), (want.size, want.tobytes()))

    def test_right_turns_what_is_shown_a_quarter_clockwise(self):
        before = shown(self.photo)
        self.assertEqual(rotate_image_file(self.photo, "right", EXIFTOOL), 6)
        after = shown(self.photo)
        want = before.rotate(270, expand=True)
        self.assertEqual((after.size, after.tobytes()), (want.size, want.tobytes()))

    def test_left_then_right_is_where_it_started(self):
        rotate_image_file(self.photo, "left", EXIFTOOL)
        self.assertEqual(rotate_image_file(self.photo, "right", EXIFTOOL), 1)

    def test_a_turn_starts_from_the_orientation_already_there(self):
        # A phone photo stored sideways and shown upright: one turn left from there
        # is the stored pixels as they are, not a quarter turn from them.
        write_fields(self.photo, {"EXIF:Orientation": 6})
        self.assertEqual(rotate_image_file(self.photo, "left", EXIFTOOL), 1)

    def test_png_and_webp_keep_their_fields_too(self):
        for name in ("still.png", "still.webp"):
            path = make_picture(os.path.join(self.lib.photos, name))
            write_fields(path, {"XMP:Subject": ["Beach"], "XMP:Description": "A still"})
            pixels = stored_pixels(path)
            self.assertEqual(rotate_image_file(path, "right", EXIFTOOL), 6)
            found = read_fields(path)
            self.assertEqual(found.get("XMP:Subject"), "Beach", name)
            self.assertEqual(found.get("XMP:Description"), "A still", name)
            self.assertEqual(stored_pixels(path), pixels, name)

    def test_refuses_without_exiftool_rather_than_re_encoding(self):
        with self.assertRaises(ValueError):
            rotate_image_file(self.photo, "left", None)

    def test_refuses_an_unknown_direction(self):
        with self.assertRaises(ValueError):
            rotate_image_file(self.photo, "sideways", EXIFTOOL)


@requires_exiftool
class TestTheRotateRoute(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        # In a subfolder of the scanned folder: the scan files it under the folder.
        sub = os.path.join(self.lib.photos, "Day 1")
        os.makedirs(sub)
        self.photo = make_picture(os.path.join(sub, "beach.jpg"))
        write_fields(self.photo)
        stat = os.stat(self.photo)
        self.lib.execute(
            "INSERT INTO photos (path, mtime, size, tags, people, raw_metadata) VALUES (?,?,?,?,?,?)",
            (os.path.abspath(self.photo), stat.st_mtime, stat.st_size,
             json.dumps(["Beach"]), "[]", "{}"))
        self.face = face_in(self.lib, os.path.abspath(self.photo), [0, 0, 10, 8], name="Rowan Thackeray")
        self.lib.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (self.face, b"crop"))
        self.handler = self.lib.handler(EXIFTOOL)
        status, _ = self.handler.call("handle_get_folder_scan", None,
                                      {"path": [self.lib.photos]})
        self.assertEqual(status, 200)

    def rotate(self, direction):
        return self.handler.call("handle_post_photo_rotate",
                                 {"path": self.photo, "direction": direction})

    def test_the_index_row_records_the_new_file(self):
        self.assertEqual(self.rotate("left")[0], 200)
        stat = os.stat(self.photo)
        mtime, size = self.lib.rows("SELECT mtime, size FROM photos")[0]
        self.assertAlmostEqual(mtime, stat.st_mtime, places=3)
        self.assertEqual(size, stat.st_size)

    def test_the_folder_cache_records_the_new_file(self):
        self.assertEqual(self.rotate("right")[0], 200)
        entries = tagpup_server.TagPupHTTPRequestHandler.cached_photo_entries(self.photo)
        self.assertEqual(len(entries), 1)
        stat = os.stat(self.photo)
        self.assertEqual(entries[0][1]["size"], stat.st_size)
        self.assertAlmostEqual(entries[0][1]["mtime"], stat.st_mtime, places=3)

    def test_a_jpeg_face_box_still_frames_the_face(self):
        # The box is in the coordinates face detection and the crops see: Pillow's
        # view of a JPEG, which is its stored pixels, and those did not move.
        from PIL import Image

        def face_pixels():
            box = json.loads(self.lib.rows("SELECT box FROM faces")[0][0])
            with Image.open(self.photo) as img:
                return img.convert("RGB").crop(box).tobytes()

        before = face_pixels()
        self.assertEqual(self.rotate("left")[0], 200)
        self.assertEqual(face_pixels(), before)
        self.assertEqual(self.lib.rows("SELECT (SELECT jpeg FROM face_crops c WHERE c.face_id = faces.id) FROM faces")[0][0], b"crop")

    def test_a_tiff_face_box_turns_with_the_photo(self):
        from PIL import Image

        tiff = make_picture(os.path.join(self.lib.photos, "scan.tif"), fmt="TIFF")
        box = [0, 0, 10, 8]
        face = face_in(self.lib, os.path.abspath(tiff), box)
        self.lib.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face, b"crop"))
        with Image.open(tiff) as img:
            img.load()
            face_before = img.convert("RGB").crop(box)

        for direction, angle in (("left", 90), ("right", 270), ("right", 270)):
            status, reply = self.handler.call("handle_post_photo_rotate",
                                              {"path": tiff, "direction": direction})
            self.assertEqual(status, 200, reply)
            new_box, crop = self.lib.rows("SELECT box, (SELECT jpeg FROM face_crops c WHERE c.face_id = faces.id) FROM faces WHERE id = ?", (face,))[0]
            new_box = json.loads(new_box)
            self.assertIsNone(crop, "a crop cut from the old box must be cut again")
            with Image.open(tiff) as img:
                img.load()
                face_after = img.convert("RGB").crop(new_box)
            face_before = face_before.rotate(angle, expand=True)
            self.assertEqual((face_after.size, face_after.tobytes()),
                             (face_before.size, face_before.tobytes()), direction)

    def test_a_failed_rotation_is_reported(self):
        with mock.patch("tagpup.files.metadata.rotate_image_file",
                               side_effect=RuntimeError("the file now says 1")):
            status, reply = self.rotate("left")
        self.assertEqual(status, 500)
        self.assertIn("the file now says 1", reply["error"])


if __name__ == "__main__":
    unittest.main()

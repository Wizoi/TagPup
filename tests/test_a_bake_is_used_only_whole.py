"""A bake read from a photo file is used only when it is whole and consistent (tagpup.files.bake; docs/PHASE6_DESIGN.md,
sections 3 and 7).

A library given a baked photo takes its vectors and its faces instead of working them out. A bake cut short, made under
another fingerprint, holding a vector of the wrong length or a number that is none, or counting faces it has no region
for would put wrong faces and wrong vectors into the library; each is refused with its reason, and the photo is worked
out as if it held no bake. "Looked, none found" is a bake, not a missing one.

The records are ExifTool's: hand-made ones in the shape a real file gives, and one real file, baked here by ExifTool under
a configuration of the test's own (the writer is a later cut) and read back the way the read side reads it, to prove that
shape is the one ExifTool returns -- a list of one face included.
"""
import base64
import os
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from baked_files import DIGEST, EXIFTOOL, FACE_MODEL, PHOTO_MODEL, area, record, unit  # noqa: E402
import baked_files  # noqa: E402
from tagpup.core import fields, orientation, paths  # noqa: E402
from tagpup.files import bake, field_values, images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402


def half_bytes(values):
    return base64.b64encode(np.asarray(values, dtype="<f2").tobytes()).decode("ascii")


class VectorsKeepTheirDirection(unittest.TestCase):
    def test_float16_in_base64_keeps_a_unit_vector(self):
        for dim, length in ((bake.PHOTO_DIM, 3072), (bake.FACE_DIM, 1368)):
            for seed in range(5):
                vector = unit(dim, seed)
                text = bake.encode_vector(vector)
                self.assertEqual(length, len(text))
                back = bake.decode_vector(text, dim)
                vector, back = vector.astype(np.float64), back.astype(np.float64)
                cosine = float(np.dot(vector, back) / np.linalg.norm(vector) / np.linalg.norm(back))
                self.assertLess(1.0 - cosine, 1e-7)

    def test_a_value_float16_cannot_hold_is_not_encoded(self):
        with self.assertRaises(ValueError):
            bake.encode_vector([1.0, 70000.0])


class ABakeIsUsedOnlyWhole(unittest.TestCase):
    def assertRejected(self, held, *words):
        found = bake.parse(held)
        self.assertIsInstance(found, bake.Rejected)
        for word in words:
            self.assertIn(word, found.reason)
        return found

    def test_a_whole_bake(self):
        found = bake.parse(record(faces=2))
        self.assertIsInstance(found, bake.Bake)
        self.assertEqual((PHOTO_MODEL, FACE_MODEL, images.FINGERPRINT_KEY, DIGEST),
                         (found.photo_model, found.face_model, found.fingerprint_key, found.fingerprint))
        self.assertEqual((4000.0, 3000.0), found.applied)
        self.assertEqual([area(0), area(1)], [face.area for face in found.faces])
        self.assertEqual((bake.PHOTO_DIM,), found.photo_vector.shape)
        self.assertGreater(float(np.dot(found.faces[1].vector, unit(bake.FACE_DIM, 11))), 0.999)

    def test_looked_none_found_is_a_bake(self):
        found = bake.parse(record(faces=0))
        self.assertIsInstance(found, bake.Bake)
        self.assertEqual(((), None), (found.faces, found.applied))

    def test_nothing_baked(self):
        self.assertEqual(bake.NOT_BAKED, bake.parse({"SourceFile": "a.jpg"}).reason)
        # Another program's face regions, no bake of ours.
        regions = record(faces=1)[bake.REGIONS]
        self.assertEqual(bake.NOT_BAKED, bake.parse({"SourceFile": "a.jpg", bake.REGIONS: regions}).reason)

    def test_a_part_missing(self):
        for key in bake.REQUIRED:
            self.assertRejected(record(**{key: None}), "missing", key)

    def test_a_fingerprint_made_another_way(self):
        self.assertRejected(record(**{bake.FINGERPRINT: "bytes-sha256-0:" + DIGEST.hex()}), "bytes-sha256-0")
        self.assertRejected(record(**{bake.FINGERPRINT: images.FINGERPRINT_KEY + ":" + DIGEST.hex()[:-2]}), "sha256")

    def test_a_vector_of_the_wrong_length(self):
        self.assertRejected(record(**{bake.PHOTO_VECTOR: bake.encode_vector(unit(768, 1))}), "1536 bytes", "1152")
        short = record(faces=1)
        short[bake.FACE_VECTORS] = [bake.encode_vector(unit(128, 1))]
        self.assertRejected(short, "256 bytes", "512")
        self.assertRejected(record(**{bake.PHOTO_VECTOR: "not base64!"}), "base64")

    def test_a_number_that_is_none(self):
        for bad in (float("nan"), float("inf")):
            values = list(unit(bake.PHOTO_DIM, 1))
            values[7] = bad
            self.assertRejected(record(**{bake.PHOTO_VECTOR: half_bytes(values)}), "finite")
        self.assertRejected(record(**{bake.REGIONS: dict(record()[bake.REGIONS], AppliedToDimensions={
            "W": float("nan"), "H": 3000, "Unit": "pixel"})}), "finite")

    def test_a_count_that_is_not_the_regions(self):
        self.assertRejected(record(faces=2, **{bake.FACES_FOUND: 3}), "FacesFound is 3, with 2 regions")
        fewer = record(faces=2)
        fewer[bake.FACE_VECTORS] = fewer[bake.FACE_VECTORS][:1]
        self.assertRejected(fewer, "2 regions and 1 face vectors")
        self.assertRejected(record(faces=2, **{bake.REGIONS: None}), "no regions")

    def test_none_found_with_regions(self):
        self.assertRejected(record(faces=0, **{bake.REGIONS: record(faces=1)[bake.REGIONS]}), "FacesFound is 0")

    def test_a_value_too_long(self):
        self.assertRejected(record(**{bake.PHOTO_MODEL: "m" * (bake.MAX_VALUE + 1)}), "longer")

    def test_a_region_outside_the_picture(self):
        for changed in ({"X": 5.0}, {"Y": -0.2}, {"W": 1.5}, {"H": 0}):
            held = record(faces=1)
            held[bake.REGIONS]["RegionList"][0]["Area"].update(changed)
            self.assertRejected(held, "region")

    def test_a_region_that_is_not_a_face(self):
        held = record(faces=1)
        held[bake.REGIONS]["RegionList"][0]["Type"] = "Pet"
        self.assertRejected(held, "not a face")

    def test_a_record_of_any_shape_is_answered_not_raised(self):
        shapes = [{bake.REGIONS: "a face"}, {bake.FACES_FOUND: "2"}, {bake.FACES_FOUND: True},
                  {bake.FACE_VECTORS: "AAAA"}, {bake.FACE_VECTORS: [None, None]}, {bake.PHOTO_MODEL: 12},
                  {bake.REGIONS: {"AppliedToDimensions": "4000x3000", "RegionList": []}},
                  {bake.REGIONS: {"AppliedToDimensions": {"W": 4, "H": 3, "Unit": "pixel"}, "RegionList": ["x", 1]}}]
        for changed in shapes:
            self.assertIsInstance(bake.parse(record(faces=2, **changed)), bake.Rejected, changed)

    def test_a_bake_is_not_an_indexed_field(self):
        self.assertEqual(set(), set(bake.FIELDS) & set(fields.METADATA_FIELDS))
        self.assertEqual(set(), {key.split(":")[-1] for key in bake.FIELDS} & set(fields.METADATA_FIELDS))


@unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
class ABakeAsExifToolReadsItBack(unittest.TestCase):
    """A photo baked by ExifTool, read the way the read side reads it."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="bake_")

    def baked(self, name, boxes):
        path = os.path.join(self.home.root, name)
        Image.new("RGB", (48, 32), (120, 90, 60)).save(path, quality=90)
        faces = [(orientation.area_of(box, 48, 32), unit(bake.FACE_DIM, 10 + n)) for n, box in enumerate(boxes)]
        return baked_files.write(path, baked_files.baked(unit(bake.PHOTO_DIM, 1), faces, (48, 32)))

    def read(self, path):
        records = {}
        with ExifToolSession(executable=EXIFTOOL, common_args=list(bake.READ_ARGS)) as et:
            field_values._read(et, [path], [], also=bake.FIELDS, records=records)
        return bake.parse(records[paths.key(path)])

    def test_faces_one_and_two_and_none(self):
        boxes = [[4, 6, 16, 22], [30, 2, 44, 20]]
        for count in (2, 1, 0):
            found = self.read(self.baked("Quay %d.jpg" % count, boxes[:count]))
            self.assertIsInstance(found, bake.Bake, getattr(found, "reason", ""))
            self.assertEqual(count, len(found.faces))
            self.assertEqual(DIGEST, found.fingerprint)
            for box, face in zip(boxes, found.faces):
                self.assertEqual(box, orientation.to_stored(face.area, *found.applied, 1, 48, 32))
            for n, face in enumerate(found.faces):
                self.assertGreater(float(np.dot(face.vector, unit(bake.FACE_DIM, 10 + n))), 0.999)

    def test_a_photo_with_no_bake(self):
        path = os.path.join(self.home.root, "Plain.jpg")
        Image.new("RGB", (48, 32)).save(path)
        self.assertEqual(bake.NOT_BAKED, self.read(path).reason)


if __name__ == "__main__":
    unittest.main()

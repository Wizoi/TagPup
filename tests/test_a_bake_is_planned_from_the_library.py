"""A bake is planned from what the library knows of a photo, against what its file holds (scenario F3,
tagpup.services.baking; docs/PHASE6_DESIGN.md, section 9).

The owner runs a bake over a folder: each photo plans to be baked, already holds its bake (current), or cannot be baked,
and why. A photo baked and written through the one writer then plans current, and its fingerprint row is put in the
transaction that marks the write done; an undo of the bake leaves the row (the picture is the one it was, #1894). A face
holding only another model's vector refuses the whole photo; a photo whose detector never looked is "never examined",
never "none found"; a turned photo's regions are upright.

Real ExifTool, on JPEGs made here, in a home of the test's own; rows as the indexer stores them (photo_rows.add_read,
embeddings.put with the file's stamp, faces through services.faces.record_batch with the detector that ran).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_file_holds_what_exiftool_kept import BakeCase  # noqa: E402

import baked_files  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402
from tagpup.core import validation  # noqa: E402
from tagpup.files import bake, field_values, images  # noqa: E402
from tagpup.services import baking  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, embeddings, faces_detected, photo_fingerprints  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

FACES = validation.SCRFD_ADAFACE
#: The library's detector, and one that ran before it (MTCNN's settings).
DETECTOR = face_service.detector_of(NewFaces(), None)
OLD_DETECTOR = faces_detected.detector({"min_face_size": 20, "max_side": 1600})


def box(n):
    """Face n's box in a 16 x 12 picture."""
    return [n % 8 * 2, n // 8 * 1.5, n % 8 * 2 + 1.5, n // 8 * 1.5 + 1.25]


class ABakeIsPlannedFromTheLibrary(BakeCase):

    def setUp(self):
        super().setUp()
        library_settings.stamp(self.library, {"faces.model": FACES}, operation=library_settings.FROM_REPLACED)

    def write(self, work):
        conn = db.connect(self.library.path)
        try:
            found = work(conn)
            conn.commit()
            return found
        finally:
            conn.close()

    def read(self, work):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return work(conn)
        finally:
            conn.close()

    def photo(self, name, faces=None, detector=DETECTOR, model=FACES, vector=True, turn=None, tags=("Beach",)):
        """A photo read by the index (holding `tags`, declaring Orientation `turn`): its photo vector stamped as the file
        is now, and `faces` (a count) found by `detector` under `model`; None, no detection at all."""
        path = self.make_read(name, tags=tags)
        if turn:
            self.outside(path, "-EXIF:Orientation#=%d" % turn)
        photo_key = self.read(lambda conn: library_settings.photo_key_on(conn))

        def seed(conn):
            stamp = embeddings.stamp_of(path)
            embeddings.put(conn, path, photo_key, stamp[0], stamp[1], baked_files.unit(bake.PHOTO_DIM, 1).tobytes())
        if vector:
            self.write(seed)
        if faces is not None:
            self.detect(path, faces, detector, model)
        return path

    def detect(self, path, count, detector=DETECTOR, model=FACES, boxes=None):
        """`detector` found `count` faces in the photo (`boxes`, in that order, or box(n)), their vectors under `model`,
        in place of any it had."""
        boxes = boxes or [box(n) for n in range(count)]
        found = [{"box": boxes[n], "embedding": baked_files.unit(bake.FACE_DIM, 10 + n).tolist(), "prob": 0.9}
                 for n in range(count)]
        conn = db.connect(self.library.path)
        try:
            face_service.record_batch(conn, {path: found}, model=model, detector=detector, overwrite=True)
        finally:
            conn.close()

    def plan(self, path, fingerprint=True):
        """The plan of the photo at `path`, as a bake run makes it (fingerprint False: a dry run's photo outside its
        sample)."""
        return baked_files.plan(self.library.path, path, DETECTOR, fingerprint)

    def plan_without_opening(self, path):
        """The plan of the photo at `path` by callables that fail the test when called (the file's fields, its shape, its
        fingerprint's decode): the library refuses it first."""
        def opened(*_args):
            self.fail("the file was opened for a photo the library refuses")
        return self.read(lambda conn: baking.plan_photo(conn, path, baking.keys(conn, DETECTOR), opened, opened, opened))

    def expected(self, path, boxes, applied=(16, 12)):
        photo_key = self.read(lambda conn: library_settings.photo_key_on(conn))
        return bake.values(baked_files.unit(bake.PHOTO_DIM, 1),
                           [(b, baked_files.unit(bake.FACE_DIM, 10 + n)) for n, b in enumerate(boxes)], applied,
                           images.fingerprint(images.opened(path, upright=True)), photo_key, FACES)

    def fingerprint_row(self, path):
        return self.read(lambda conn: photo_fingerprints.get(conn, store_roots.photo_ids_at(conn, [path])[0]))

    def test_a_photo_plans_its_bake_is_current_once_written_and_its_fingerprint_row_outlives_an_undo(self):
        a = self.photo("a.jpg", faces=2)
        sample_less = self.plan(a, fingerprint=False)
        self.assertIsNone(sample_less.values, "a dry run's photo outside its sample plans no values")
        plan = self.plan(a)
        self.assertIsInstance(plan, baking.ToBake)
        self.assertEqual(self.expected(a, [box(0), box(1)]), plan.values, "faces of one size in id order, boxes as stored")
        self.assertIsNone(self.fingerprint_row(a))

        result = self.bake([a], plan.values)
        self.assertEqual((1, []), (result.changed, result.errors))
        digest = images.fingerprint(images.opened(a, upright=True))
        self.assertEqual((images.FINGERPRINT_KEY, digest), tuple(self.fingerprint_row(a)), "put as the write is done")
        self.assertIsInstance(self.plan(a), baking.Current, "written, it plans current (its vector restamped)")
        self.assertIsInstance(self.plan(a, fingerprint=False), baking.Current)

        undone = self.undo(result.details["change"])
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual((images.FINGERPRINT_KEY, digest), tuple(self.fingerprint_row(a)), "an undo leaves the row")
        self.assertIsInstance(self.plan(a), baking.ToBake, "the bake undone, it plans again")

    def test_the_largest_face_is_baked_first_and_the_bake_then_reads_current(self):
        # Owner, 2026-10-25: largest first, ties by id. The small face was found first: it holds the lower id.
        a = self.photo("a.jpg")
        small, large = [1, 1, 2, 2], [4, 3, 12, 11]
        self.detect(a, 2, boxes=[small, large])
        in_id_order = sorted(self.read(lambda conn: store_faces.in_photo_with_names(conn, a, FACES)))
        self.assertEqual([small, large], [images.parse_box(row[1]) for row in in_id_order], "the small face, the lower id")
        plan = self.plan(a)
        photo_key = self.read(lambda conn: library_settings.photo_key_on(conn))
        self.assertEqual(bake.values(baked_files.unit(bake.PHOTO_DIM, 1),
                                     [(large, baked_files.unit(bake.FACE_DIM, 11)), (small, baked_files.unit(bake.FACE_DIM, 10))],
                                     (16, 12), images.fingerprint(images.opened(a, upright=True)), photo_key, FACES),
                         plan.values, "the large face's region and vector first")
        self.assertEqual((1, []), (lambda result: (result.changed, result.errors))(self.bake([a], plan.values)))
        written = self.bake_of(a)
        self.assertEqual([0.5, 0.0625], [face.area[2] for face in written.faces], "the file lists the large face first")
        self.assertEqual(bake.encode_vector(baked_files.unit(bake.FACE_DIM, 11)), bake.encode_vector(written.faces[0].vector),
                         "and its vector")
        self.assertIsInstance(self.plan(a), baking.Current, "written in that order, it plans current")

    def test_faces_of_one_pixel_area_and_two_shapes_keep_id_order(self):
        # Owner, 2026-10-25: ties by id. In a 16 x 12 picture 1 x 5 and 5 x 1 cover the same pixels, but as fractions
        # (1/16)*(5/12) and (5/16)*(1/12) differ in the last bit: ordered by rounding, the later face came first.
        a = self.photo("a.jpg")
        wide, tall = [0, 0, 5, 1], [8, 0, 9, 5]
        self.detect(a, 2, boxes=[wide, tall])
        plan = self.plan(a)
        photo_key = self.read(lambda conn: library_settings.photo_key_on(conn))
        self.assertEqual(bake.values(baked_files.unit(bake.PHOTO_DIM, 1),
                                     [(wide, baked_files.unit(bake.FACE_DIM, 10)), (tall, baked_files.unit(bake.FACE_DIM, 11))],
                                     (16, 12), images.fingerprint(images.opened(a, upright=True)), photo_key, FACES),
                         plan.values, "the lower id first")

    def test_a_stale_bake_plans_again(self):
        a = self.photo("a.jpg", faces=2)
        self.bake([a], self.plan(a).values)
        self.detect(a, 3)
        again = self.plan(a)
        self.assertIsInstance(again, baking.ToBake, "a face more in the library than in the file")
        self.assertEqual(self.expected(a, [box(0), box(1), box(2)]), again.values)

        b = self.photo("b.jpg", faces=1)
        other = dict(self.plan(b).values, **{bake.WRITTEN[bake.FACE_MODEL]: ["facenet-vggface2"]})
        self.bake([b], other)   # journaled, so its photo vector is restamped over the write
        self.assertIsInstance(self.plan(b), baking.ToBake, "a file baked under another face model")

    def test_what_cannot_be_baked_says_why(self):
        a = self.photo("a.jpg", faces=1)
        self.outside(a, "-XMP:Description=Harbour at dusk")
        self.assertEqual(baking.CHANGED, self.plan(a).kind, "the file changed since its vector was made")
        b = self.photo("b.jpg", faces=1, vector=False)
        self.assertEqual(baking.NO_VECTOR, self.plan(b).kind)
        gone = self.make("gone.jpg", row=False)
        self.assertEqual(baking.GONE, self.plan(gone).kind)
        away = self.photo("away.jpg", faces=1)
        os.remove(away)
        plan = self.plan_without_opening(away)
        self.assertEqual(baking.UNREADABLE, plan.kind, "a file that cannot be reached is not one that changed")
        self.assertNotIn("index it again", plan.reason)

        c = self.photo("c.jpg", faces=1)
        keys = self.read(lambda conn: baking.keys(conn, DETECTOR))
        for held in (field_values.Unreadable("locked"), field_values.Unreadable("Its XMP-mwg-rs:RegionInfo could not be read")):
            plan = self.read(lambda conn, held=held: baking.plan_photo(conn, c, keys, lambda: held, lambda: (16, 12, 1)))
            self.assertEqual(baking.UNREADABLE, plan.kind)
            self.assertIn(str(held), plan.reason)

        def torn():
            raise field_values.Unreadable("locked")

        def cannot_open():
            raise OSError("cannot identify image file")
        for held, shape in ((torn, lambda: (16, 12, 1)), (lambda: {}, cannot_open)):
            plan = self.read(lambda conn, held=held, shape=shape: baking.plan_photo(conn, c, keys, held, shape))
            self.assertEqual(baking.UNREADABLE, plan.kind, "a read that raises, or a picture that will not open")

    def test_a_photo_the_library_refuses_is_not_opened(self):
        stale = self.photo("stale.jpg", faces=1)
        self.outside(stale, "-XMP:Description=Harbour at dusk")
        mixed = self.photo("mixed.jpg", faces=1)
        self.write(lambda conn: store_faces.insert(conn, mixed, [1, 1, 3, 3], face_service.vector_bytes([0.5] * 8),
                                                   model="facenet-vggface2"))
        gone = self.make("gone.jpg", row=False)
        for path, kind in ((self.photo("never.jpg"), baking.NEVER_EXAMINED),
                           (self.photo("novector.jpg", faces=1, vector=False), baking.NO_VECTOR),
                           (stale, baking.CHANGED), (mixed, baking.FACES_WITHOUT_VECTORS), (gone, baking.GONE)):
            self.assertEqual(kind, self.plan_without_opening(path).kind)
        away = self.photo("away.jpg", faces=1)
        os.remove(away)
        self.assertEqual(baking.UNREADABLE, self.plan_without_opening(away).kind, "a file that cannot be reached")

    def test_a_face_with_another_models_vector_refuses_the_whole_photo(self):
        a = self.photo("a.jpg", faces=2)
        self.write(lambda conn: store_faces.insert(conn, a, [1, 1, 3, 3], face_service.vector_bytes([0.5] * 8),
                                                   model="facenet-vggface2"))
        plan = self.plan(a)
        self.assertEqual(baking.FACES_WITHOUT_VECTORS, plan.kind, "no part of the photo is baked")
        self.assertIn("1 face(s) hold no vector", plan.reason)

    def test_never_examined_is_not_none_found(self):
        never = self.photo("never.jpg")
        old = self.photo("old.jpg", faces=0, detector=OLD_DETECTOR)
        looked = self.photo("looked.jpg", faces=0)
        self.assertEqual(baking.NEVER_EXAMINED, self.plan(never).kind)
        self.assertEqual(baking.NEVER_EXAMINED, self.plan(old).kind, "another detector's look is not this one's")
        plan = self.plan(looked)
        self.assertEqual(self.expected(looked, []), plan.values, "looked, none found: FacesFound 0, no regions")
        self.assertEqual(["0"], plan.values[bake.WRITTEN[bake.FACES_FOUND]])

    def test_a_turned_photos_regions_are_upright(self):
        a = self.photo("a.jpg", faces=1, turn=6)
        # Stored [0, 0, 1.5, 1.25] of 16 x 12 under Orientation 6 is shown at [12 - 1.25, 0, 12, 1.5] of 12 x 16.
        self.assertEqual(self.expected(a, [[10.75, 0, 12, 1.5]], applied=(12, 16)), self.plan(a).values)



if __name__ == "__main__":
    unittest.main()

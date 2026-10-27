"""A rotation is a journaled change of the file's Orientation (tagpup.services.photos.rotate_photos, tagpup.services.rotation;
docs/INVARIANTS.md). The face boxes it turns, and a crash half way, are test_rotate_crash_settles_with_the_boxes.

Rotate used to write the Orientation, turn the boxes and stamp the row in three steps with no record: a crash after the file
write left a turned file with the boxes of the old picture, and nothing found it. Now the file goes through the file journal
(planned, writing, done), a crash is settled forward at the next start, and History undoes the turn.

Real JPEGs and TIFFs made in a home of the test's own: Pillow writes the Orientation a photo starts with (one ExifTool process
less for every photo), the real ExifTool turns it, and the real ExifTool reads it back -- one session for the whole test -- so
the FILE is what every assertion about an orientation asks (skipped where ExifTool is not installed). Rows are seeded as the
indexer stores them; a crash is a BaseException raised at a step of the write (file_changes.STEPS).

Lifted: test_service_rotate (#224 #249): what the click's Result says (attempted, changed, orientation, mtime and size), a
direction that is not one, a write that fails. Its stale-row arm (#249) is
test_partial_writes_leave_rows_unread's rotate arm (mutation: store/photos.py follow_fields stamps without `_describes_before`
-> that arm fails). Mutations of this file, each in a scratch copy: services/photos.py `rotate`, `result.details.update(mtime=
stat.st_mtime, size=stat.st_size)` -> `mtime=0, size=0` fails test_the_row_follows_the_file_and_the_vectors_go; `rotate_photos`,
`refused = validation.problem("rotate direction", direction)` -> `refused = None` fails test_a_direction_that_is_not_one;
file_changes `result.fail(path, why)` -> `pass` fails test_a_write_that_fails.
"""
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, crash_at, requires_exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import duplicate_faces, face_pass, file_changes, journal as journal_service  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db, embeddings, faces_detected, journal, photo_fingerprints, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

MODEL = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
WIDTH, HEIGHT = 60, 40
BOX = [0, 0, 10, 8]
ORIENTATION = 274   # the EXIF tag Pillow writes


def picture(path, fmt="JPEG", orientation=None):
    """A WIDTH x HEIGHT picture at `path` holding `orientation` when given, written by Pillow."""
    from PIL import Image

    kept = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        kept["exif"] = exif
    Image.new("RGB", (WIDTH, HEIGHT), (90, 110, 130)).save(path, fmt, **kept)


@requires_exiftool
class RotationCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="rotate_journal_")
        self.library = Library(self.home.library("rotate.db"))
        schema.ensure(self.library.path)
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)
        self.et = ExifToolSession(executable=EXIFTOOL)
        self.addCleanup(self.et.terminate)

    def make(self, name, fmt="JPEG", orientation=None, row=True, folder=None):
        """A picture of WIDTH x HEIGHT, with `orientation` when given, and its row as the indexer leaves it."""
        path = os.path.join(folder or self.folder, name)
        picture(path, fmt, orientation)
        if row:
            self.execute_with(lambda conn: photo_rows.add_read(conn, path, {}))
        return path

    def orientations(self, paths):
        """What each file holds as its Orientation (None for none), asked of the file in one ExifTool command."""
        found = self.et.get_tags(list(paths), tags=["EXIF:Orientation"])
        return [int(each["EXIF:Orientation"]) if each.get("EXIF:Orientation") is not None else None for each in found]

    def orientation(self, path):
        return self.orientations([path])[0]

    def execute_with(self, work):
        conn = db.connect(self.library.path)
        try:
            found = work(conn)
            conn.commit()
            return found
        finally:
            conn.close()

    def execute(self, sql, params=()):
        self.execute_with(lambda conn: conn.execute(sql, params))

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def face(self, path, box=BOX, **columns):
        return self.execute_with(lambda conn: add_face(conn, path, list(box), **columns))

    def boxes(self):
        import json
        return [json.loads(box) for (box,) in self.rows("SELECT box FROM faces ORDER BY id")]

    def rotate(self, path, direction="left"):
        return photo_actions.rotate(self.library, path, direction, EXIFTOOL)

    def settle(self):
        return file_changes.settle(self.library, EXIFTOOL)

    def last_change(self):
        return self.rows("SELECT MAX(id) FROM changes")[0][0]

    def states(self, change_id):
        return [state for (state,) in self.rows("SELECT state FROM change_files WHERE change_id = ? ORDER BY id", (change_id,))]

    def status(self, change_id):
        return self.rows("SELECT status FROM changes WHERE id = ?", (change_id,))[0][0]

    def undo(self, change_id):
        return journal_service.undo(self.library, change_id, apply=True, exiftool_path=EXIFTOOL)

    def crash(self, step, path, direction="left"):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
            with self.assertRaises(Crash):
                self.rotate(path, direction)
        return self.last_change()


class TheTurn(RotationCase):
    def test_it_is_one_journaled_change_with_the_orientation_before_and_after(self):
        path = self.make("a.jpg", orientation=6)
        result = self.rotate(path, "left")
        self.assertEqual((result.ok, result.attempted, result.changed, result.errors, self.orientation(path)), (True, 1, 1, [], 1))
        self.assertEqual((result.details["orientation"], result.details["with_rows"]), (1, 1))
        change = result.details["change"]
        self.assertEqual((self.rows("SELECT operation FROM changes WHERE id = ?", (change,)), self.states(change), self.status(change)),
                         ([("rotate",)], ["done"], "applied"))
        self.assertEqual(self.rows("SELECT fields_before, fields_after FROM change_files WHERE change_id = ?", (change,)),
                         [('{"EXIF:Orientation": ["6"]}', '{"EXIF:Orientation": ["1"]}')])

    def test_a_photo_with_no_orientation_is_taken_for_1_and_undone_to_none(self):
        path = self.make("a.jpg")
        self.assertIsNone(self.orientation(path))
        change = self.rotate(path, "left").details["change"]
        self.assertEqual(self.orientation(path), 8)
        self.assertEqual(self.undo(change).changed, 1)
        self.assertIsNone(self.orientation(path))
        self.assertEqual(self.status(change), "undone")

    def test_a_mirrored_photo_stays_mirrored(self):
        path = self.make("a.jpg", orientation=2)
        self.rotate(path, "left")
        self.assertEqual(self.orientation(path), 5)
        self.rotate(path, "right")
        self.assertEqual(self.orientation(path), 2)

    def test_turned_twice_and_undone_once_lands_after_one_turn(self):
        path = self.make("a.jpg", orientation=6)
        self.rotate(path, "right")
        second = self.rotate(path, "right").details["change"]
        self.assertEqual(self.orientation(path), 8)
        self.undo(second)
        self.assertEqual(self.orientation(path), 3)

    def test_the_row_follows_the_file_and_the_vectors_go(self):
        # The vectors and the fingerprint go: each is of the upright decode the turn changes. Another photo's stay.
        path = self.make("a.jpg", orientation=6)
        self.make("b.jpg")
        turned, kept = [photo_id for (photo_id,) in self.rows("SELECT id FROM photos ORDER BY id")]
        stat = os.stat(path)
        self.execute_with(lambda conn: embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size, b"\x00\x00\x80\x3f" * 4, None))
        for photo_id in (turned, kept):
            self.execute_with(lambda conn: photo_fingerprints.put(conn, photo_id, "upright-sha256-1", b"\x5a" * 32))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM embeddings"), [(1,)])
        result = self.rotate(path, "left")
        stat = os.stat(path)
        self.assertEqual(self.rows("SELECT mtime, size FROM photos WHERE id = ?", (turned,)), [(stat.st_mtime, stat.st_size)])
        self.assertEqual((result.details["mtime"], result.details["size"]), (stat.st_mtime, stat.st_size))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM embeddings"), [(0,)])
        self.assertEqual(self.rows("SELECT photo_id FROM photo_fingerprints"), [(kept,)])

class TheFacesOfATurnedPhoto(RotationCase):
    """A turn changes the picture faces are detected on and crops are cut from (both as shown, #1975): the faces' cached
    crops and the photo's record of what looked at it go, forward and in an undo; the faces themselves stay. A crop is a
    cache (owner, 2026-10-27; journal.CACHES): a change that made or deleted a face is undone after a turn took the face's
    crop, as it records it now and as a change made before recorded the crop (a row of its own in change_rows)."""

    DETECTOR = faces_detected.detector({"model": "scrfd-adaface"})
    KEY = "scrfd-adaface"
    VECTOR = b"\x00\x00\x80\x3f" * 4
    CROP = bytes([7]) * 64
    SHAPES = ("as a change records it now", "with the crop recorded, as before 2026-10-27")

    def faces(self):
        return self.rows("SELECT f.id, f.name, f.tag_id, f.excluded, f.box, v.vector FROM faces f"
                         " LEFT JOIN face_embeddings v ON v.face_id = f.id ORDER BY f.id")

    def crop_size(self, face):
        """The size of the face's crop as the page's request gets it: kept, or cut and kept."""
        import io
        from PIL import Image
        with Image.open(io.BytesIO(photo_actions.face_crop(self.library, face))) as img:
            return img.size

    def looked_at(self, *paths):
        self.execute_with(lambda conn: [faces_detected.record(conn, path, self.DETECTOR, None, found)
                                        for path, found in paths])

    def counts(self):
        return self.rows("SELECT (SELECT COUNT(*) FROM face_crops), (SELECT COUNT(*) FROM faces_detected)")[0]

    def test_the_crops_and_the_record_go_and_the_faces_stay(self):
        with_face, faceless = self.make("a.jpg"), self.make("b.jpg")
        face = self.face(with_face, name="Rowan Thackeray", embedding=b"\x00\x00\x80\x3f" * 4)
        self.face(with_face, box=[20, 10, 30, 30], excluded=1)
        self.looked_at((with_face, 2), (faceless, 0))
        self.assertEqual(self.crop_size(face), (10, 8))   # cut and kept, as the photo is shown before the turn
        faces = self.faces()
        self.assertEqual(self.counts(), (1, 2))

        change = photo_actions.rotate_photos(self.library, [with_face, faceless], "left", EXIFTOOL).details["change"]
        self.assertEqual((self.faces(), self.counts()), (faces, (0, 0)))
        self.assertEqual(self.crop_size(face), (8, 10))   # cut again, upright for the new Orientation

        self.looked_at((with_face, 2), (faceless, 0))
        self.assertEqual(self.undo(change).changed, 2)
        self.assertEqual((self.orientations([with_face, faceless]), self.faces(), self.counts()), ([None, None], faces, (0, 0)))
        self.assertEqual(self.crop_size(face), (10, 8))

    def crop_recorded(self, conn, change_id, action, face_id, crop):
        """The crop's row in change `change_id` exactly as journal._record wrote it before a crop was a cache."""
        key = schema.row_key((face_id,))
        conn.executemany("INSERT INTO change_rows (change_id, action, table_name, row_key, column_name, old, new)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(change_id, action, "face_crops", key, column,
                           None if action == "insert" else value, value if action == "insert" else None)
                          for column, value in (("face_id", face_id), ("jpeg", crop))])

    def a_pass(self, path, shape):
        """A face pass that finds one new face, with its crop, in the photo at `path`, through the pass's writer."""
        def work(conn):
            change_id = journal.record(conn, face_pass.OPERATION % self.KEY, [],
                                       {"folder": store_roots.to_row(conn, os.path.dirname(path)), "matched": 0, "new": 0})
            (face_id,) = store_faces.give_vectors(conn, change_id, path, self.KEY, [],
                                                  [(list(BOX), 0.99, self.VECTOR, self.CROP)], {"matched": 0, "new": 1})
            if shape != self.SHAPES[0]:
                self.crop_recorded(conn, change_id, "insert", face_id, self.CROP)
            return change_id
        return self.execute_with(work)

    def assertUndone(self, change_id):
        undone = self.undo(change_id)
        self.assertEqual((undone.refused, self.status(change_id)), (None, "undone"))

    def test_a_pass_is_undone_after_a_turn_took_its_crop(self):
        for n, shape in enumerate(self.SHAPES):
            with self.subTest(shape):
                path = self.make("pass%d.jpg" % n)
                passed = self.a_pass(path, shape)
                self.assertEqual(self.rows("SELECT COUNT(*) FROM face_crops")[0][0], 1)
                photo_actions.rotate_photos(self.library, [path], "left", EXIFTOOL)
                self.assertEqual(self.rows("SELECT COUNT(*) FROM face_crops")[0][0], 0)
                rehearsed = journal_service.undo(self.library, passed, apply=False, exiftool_path=EXIFTOOL)
                self.assertIsNone(rehearsed.refused)
                self.assertUndone(passed)
                self.assertEqual(self.rows("SELECT (SELECT COUNT(*) FROM faces), (SELECT COUNT(*) FROM face_crops),"
                                           " (SELECT COUNT(*) FROM face_embeddings)"), [(0, 0, 0)])

    def test_a_turn_then_a_pass_are_undone_the_turn_first(self):
        for n, shape in enumerate(self.SHAPES):
            with self.subTest(shape):
                path = self.make("turned%d.jpg" % n)
                turned = photo_actions.rotate_photos(self.library, [path], "left", EXIFTOOL).details["change"]
                passed = self.a_pass(path, shape)
                self.assertUndone(turned)   # takes the pass's crop: it was cut from the turned picture
                self.assertEqual((self.orientation(path), self.rows("SELECT COUNT(*) FROM face_crops")[0][0]), (None, 0))
                self.assertUndone(passed)
                self.assertEqual(self.rows("SELECT COUNT(*) FROM faces"), [(0,)])

    def test_a_dedupe_undone_after_a_turn_brings_the_face_back_cut_as_the_photo_is_now(self):
        for n, shape in enumerate(self.SHAPES):
            with self.subTest(shape):
                self.execute("DELETE FROM faces")   # the other shape's
                path = self.make("copies%d.jpg" % n)
                kept, copy = self.face(path), self.face(path)
                self.assertEqual((self.crop_size(kept), self.crop_size(copy)), ((10, 8), (10, 8)))   # cut and kept
                (crop,) = self.rows("SELECT jpeg FROM face_crops WHERE face_id = ?", (copy,))[0]
                self.assertEqual(duplicate_faces.dedupe_faces(self.library, apply=True).changed, 1)
                deduped = self.last_change()
                if shape != self.SHAPES[0]:
                    self.execute_with(lambda conn: self.crop_recorded(conn, deduped, "delete", copy, crop))
                photo_actions.rotate_photos(self.library, [path], "left", EXIFTOOL)
                self.assertUndone(deduped)
                self.assertEqual(self.rows("SELECT id FROM faces WHERE id = ?", (copy,)), [(copy,)])
                self.assertEqual(self.rows("SELECT COUNT(*) FROM face_crops WHERE face_id = ?", (copy,)), [(0,)])
                self.assertEqual(self.crop_size(copy), (8, 10))   # cut again, upright for the turned photo


class APassBesideATurn(RotationCase):
    """The owner turns a photo while the face pass has read its picture and not yet written what it found in it: the
    turn's done transaction stamps the row's (mtime, size) (store.photos.follow_fields), which the pass read before the
    picture, so its write finds the photo not as read and writes nothing -- no face from the old picture, no record of a
    photo looked at that the turn just forgot -- and the photo is failed, looked at again next run, as turned."""

    def a_photo(self, name):
        """A photo big enough for a face over the floor, in a folder of its own, and its row as the indexer leaves it."""
        from PIL import Image
        folder = os.path.join(self.folder, os.path.splitext(name)[0])
        os.makedirs(folder)
        path = os.path.join(folder, name)
        Image.new("RGB", (300, 200), (90, 110, 130)).save(path, "JPEG")
        self.execute_with(lambda conn: photo_rows.add_read(conn, path, {}))
        return folder, path, self.rows("SELECT MAX(id) FROM photos")[0][0]

    def turning(self, found):
        """The model, which the owner's click turns the photo beside: after it has read the picture, before the write."""
        library = self.library

        class Turning(NewFaces):
            def detect_and_embed(model, picture):
                detected = NewFaces.detect_and_embed(model, picture)
                turned = photo_actions.rotate_photos(library, [picture.path], "left", EXIFTOOL)
                assert turned.changed == 1, turned.message()
                return detected

        return Turning(found)

    def test_the_photo_is_failed_and_nothing_of_the_old_picture_is_written(self):
        for name, found in (("faces.jpg", [((100, 50, 200, 170), 0.9, 5)]), ("faceless.jpg", [])):
            with self.subTest(name):
                folder, path, photo_id = self.a_photo(name)
                (passed,) = face_pass.run(self.library.path, self.turning({name: found}), folder, apply=True)
                self.assertEqual((passed.failed, passed.new, passed.none_found), ([(photo_id, face_pass.CHANGED)], 0, 0))
                self.assertEqual(self.orientation(path), 8)
                self.assertEqual(self.rows("SELECT (SELECT COUNT(*) FROM faces), (SELECT COUNT(*) FROM faces_detected)"),
                                 [(0, 0)])
                (again,) = face_pass.run(self.library.path, NewFaces({name: found}), folder, apply=True)
                self.assertEqual((again.failed, again.new, again.none_found), ([], len(found), 1 - len(found)))
                self.execute("DELETE FROM faces")
                self.execute("DELETE FROM faces_detected")


class WhereTheLibraryDoesNotHoldTheFolder(RotationCase):
    def test_a_just_look_photo_is_turned_in_its_file_only(self):
        loose_folder = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(loose_folder)
        loose = self.make("x.jpg", row=False, folder=loose_folder)
        held = self.make("h.jpg")
        result = photo_actions.rotate_photos(self.library, [held, loose], "right", EXIFTOOL)
        self.assertEqual((result.ok, result.changed, result.details["file_only"], result.details["with_rows"]), (True, 2, 1, 1))
        self.assertEqual(self.orientations([loose, held]), [6, 6])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos"), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM change_files"), [(1,)])


class WhatIsRefused(RotationCase):
    def test_a_direction_that_is_not_one_touches_nothing(self):
        # The "rotate direction" kind (#224): the route checked it, the service took anything, and the file layer raised on it.
        path = self.make("a.jpg")
        result = self.rotate(path, "up")
        self.assertEqual("Direction must be 'left' or 'right'", result.refused)
        self.assertEqual((result.changed, result.errors), (0, []))
        self.assertEqual((self.orientation(path), self.rows("SELECT COUNT(*) FROM changes WHERE operation = 'rotate'")), (None, [(0,)]))

    def test_a_write_that_fails_changes_nothing_and_says_why(self):
        path = self.make("a.jpg", orientation=6)
        self.face(path)
        stamp = self.rows("SELECT mtime, size FROM photos")
        with mock.patch.object(field_values, "write", side_effect=OSError("the file is locked by another program")):
            result = self.rotate(path)
        self.assertFalse(result.ok)
        self.assertEqual((result.attempted, result.changed), (1, 0))
        self.assertIn("locked", result.message())
        self.assertEqual((self.orientation(path), self.rows("SELECT mtime, size FROM photos"), self.boxes()), (6, stamp, [BOX]))


if __name__ == "__main__":
    unittest.main()

"""A rotation is a journaled change of the file's Orientation, and the face boxes it turns are part of it
(tagpup.services.photos.rotate_photos, tagpup.services.rotation; docs/INVARIANTS.md).

Rotate used to write the Orientation, turn the boxes and stamp the row in three steps with no record: a crash after the file
write left a turned file with the boxes of the old picture, and nothing found it. Now the file goes through the file journal
(planned, writing, done), the boxes and the vectors are made in the transaction that marks it done, a crash is settled forward
at the next start, and History undoes the turn: file and boxes together.

Real JPEGs and TIFFs made in a home of the test's own, written by the real ExifTool (skipped where it is not installed). Rows are
seeded as the indexer stores them; a crash is a BaseException raised at a step of the write (file_changes.STEPS).
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
from test_file_journal import EXIFTOOL, Crash, crash_at, requires_exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import batches, file_changes, journal as journal_service, photos as photo_actions  # noqa: E402
from tagpup.store import db, embeddings, file_journal, schema  # noqa: E402
from tagpup.store.faces import turned_box  # noqa: E402

MODEL = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
WIDTH, HEIGHT = 60, 40
BOX = [0, 0, 10, 8]


def orientation_of(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        value = et.get_tags([path], tags=["EXIF:Orientation"])[0].get("EXIF:Orientation")
    return int(value) if value is not None else None


def set_orientation(path, value):
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags={"Orientation": value}, params=["-overwrite_original"])


@requires_exiftool
class RotationCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="rotate_journal_")
        self.library = Library(self.home.library("rotate.db"))
        schema.ensure(self.library.path)
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)

    def make(self, name, fmt="JPEG", orientation=None, row=True, folder=None):
        """A picture of WIDTH x HEIGHT, with `orientation` when given, and its row as the indexer leaves it."""
        from PIL import Image

        path = os.path.join(folder or self.folder, name)
        Image.new("RGB", (WIDTH, HEIGHT), (90, 110, 130)).save(path, fmt)
        if orientation is not None:
            set_orientation(path, orientation)
        if row:
            self.execute_with(lambda conn: photo_rows.add_read(conn, path, {}))
        return path

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
        self.assertEqual((result.ok, result.changed, orientation_of(path)), (True, 1, 1))
        change = result.details["change"]
        self.assertEqual((self.rows("SELECT operation FROM changes WHERE id = ?", (change,)), self.states(change), self.status(change)),
                         ([("rotate",)], ["done"], "applied"))
        self.assertEqual(self.rows("SELECT fields_before, fields_after FROM change_files WHERE change_id = ?", (change,)),
                         [('{"EXIF:Orientation": ["6"]}', '{"EXIF:Orientation": ["1"]}')])

    def test_a_photo_with_no_orientation_is_taken_for_1_and_undone_to_none(self):
        path = self.make("a.jpg")
        self.assertIsNone(orientation_of(path))
        change = self.rotate(path, "left").details["change"]
        self.assertEqual(orientation_of(path), 8)
        self.assertEqual(self.undo(change).changed, 1)
        self.assertIsNone(orientation_of(path))
        self.assertEqual(self.status(change), "undone")

    def test_a_mirrored_photo_stays_mirrored(self):
        path = self.make("a.jpg", orientation=2)
        self.rotate(path, "left")
        self.assertEqual(orientation_of(path), 5)
        self.rotate(path, "right")
        self.assertEqual(orientation_of(path), 2)

    def test_turned_twice_and_undone_once_lands_after_one_turn(self):
        path = self.make("a.jpg", orientation=6)
        self.rotate(path, "right")
        second = self.rotate(path, "right").details["change"]
        self.assertEqual(orientation_of(path), 8)
        self.undo(second)
        self.assertEqual(orientation_of(path), 3)

    def test_the_row_follows_the_file_and_the_vectors_go(self):
        path = self.make("a.jpg", orientation=6)
        stat = os.stat(path)
        self.execute_with(lambda conn: embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size, b"\x00\x00\x80\x3f" * 4))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM embeddings"), [(1,)])
        result = self.rotate(path, "left")
        stat = os.stat(path)
        self.assertEqual(self.rows("SELECT mtime, size FROM photos"), [(stat.st_mtime, stat.st_size)])
        self.assertEqual((result.details["mtime"], result.details["size"]), (stat.st_mtime, stat.st_size))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM embeddings"), [(0,)])


class TheFaceBoxesTurnWithTheFile(RotationCase):
    def test_a_tiffs_boxes_turn_in_the_same_change_and_undo_turns_them_back(self):
        path = self.make("scan.tif", fmt="TIFF")
        self.face(path)
        first = self.rotate(path, "left")
        self.assertEqual(first.details["faces_turned"], 1)
        self.assertEqual(self.boxes(), [turned_box(BOX, "left", WIDTH, HEIGHT)])
        second = self.rotate(path, "left").details["change"]
        self.assertEqual(self.boxes(), [turned_box(turned_box(BOX, "left", WIDTH, HEIGHT), "left", HEIGHT, WIDTH)])
        self.undo(second)
        self.assertEqual(self.boxes(), [turned_box(BOX, "left", WIDTH, HEIGHT)])
        self.undo(first.details["change"])
        self.assertEqual((self.boxes(), orientation_of(path)), ([BOX], None))

    def test_a_jpegs_boxes_stay_where_the_stored_pixels_are(self):
        path = self.make("a.jpg")
        self.face(path)
        self.assertEqual(self.rotate(path, "right").details["faces_turned"], 0)
        self.assertEqual(self.boxes(), [BOX])

    def test_a_crash_after_the_file_is_written_is_settled_forward_boxes_and_all(self):
        path = self.make("scan.tif", fmt="TIFF")
        face = self.face(path, name="Rowan Thackeray")
        self.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face, b"crop"))
        stat = os.stat(path)
        self.execute_with(lambda conn: embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size, b"\x00\x00\x80\x3f" * 4))
        change = self.crash("file written", path, "left")
        # The file is turned; its rows are not: the change says so, and is not finished.
        self.assertEqual((orientation_of(path), self.states(change), self.boxes()), (8, ["writing"], [BOX]))
        self.assertEqual(self.status(change), "planned")
        self.assertEqual(len(file_journal.unfinished(self.library.path)), 1)
        # The face is named something else meanwhile; the box is all the settling turns.
        self.execute("UPDATE faces SET name = 'Cora Ingersoll' WHERE id = ?", (face,))
        self.assertEqual(self.settle(), 1)
        self.assertEqual((self.states(change), self.status(change)), (["done"], "applied"))
        self.assertEqual(self.boxes(), [turned_box(BOX, "left", WIDTH, HEIGHT)])
        self.assertEqual(self.rows("SELECT name FROM faces"), [("Cora Ingersoll",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM face_crops"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM embeddings"), [(0,)])
        self.assertEqual(orientation_of(path), 8, "settling must not turn the file twice")
        # Settled once: nothing is left to settle, and the boxes are not turned again.
        self.assertEqual(self.settle(), 0)
        self.assertEqual(self.boxes(), [turned_box(BOX, "left", WIDTH, HEIGHT)])

    def test_a_crash_before_the_file_is_written_is_carried_out_whole(self):
        path = self.make("scan.tif", fmt="TIFF")
        self.face(path)
        change = self.crash("file writing", path, "right")
        self.assertEqual((orientation_of(path), self.boxes()), (None, [BOX]))
        self.assertEqual(self.settle(), 1)
        self.assertEqual((orientation_of(path), self.boxes(), self.status(change)),
                         (6, [turned_box(BOX, "right", WIDTH, HEIGHT)], "applied"))

    def test_a_crash_in_an_undo_is_settled_back_with_the_boxes(self):
        path = self.make("scan.tif", fmt="TIFF")
        self.face(path)
        change = self.rotate(path, "left").details["change"]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 1)):
            with self.assertRaises(Crash):
                self.undo(change)
        self.assertEqual((orientation_of(path), self.boxes()), (None, [turned_box(BOX, "left", WIDTH, HEIGHT)]))
        self.assertEqual(self.settle(), 1)
        self.assertEqual((orientation_of(path), self.boxes(), self.status(change)), (None, [BOX], "undone"))


class ManyPhotos(RotationCase):
    def test_a_rotation_of_many_is_one_batch_with_one_change(self):
        photos = [self.make("p%02d.jpg" % n, orientation=6) for n in range(batches.CHUNK + 2)]
        claimed = []
        real = batches.claim

        def claim(*args, **kwargs):
            claimed.append(args[1:3])
            return real(*args, **kwargs)
        with mock.patch.object(batches, "claim", side_effect=claim):
            result = photo_actions.rotate_photos(self.library, photos, "right", EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, len(photos)))
        self.assertEqual(claimed, [("rotate", len(photos))])
        self.assertEqual({orientation_of(path) for path in photos}, {3})
        change = result.details["change"]
        self.assertEqual((self.states(change), self.status(change)), (["done"] * len(photos), "applied"))
        self.assertEqual(self.undo(change).changed, len(photos))
        self.assertEqual({orientation_of(path) for path in photos}, {6})

    def test_a_batch_asked_to_stop_keeps_what_it_turned_and_can_undo_it(self):
        photos = [self.make("p%02d.jpg" % n) for n in range(batches.CHUNK + 5)]
        # Cancel is asked for at the first chunk's end.
        with mock.patch.object(file_changes, "between_chunks", return_value=False):
            result = photo_actions.rotate_photos(self.library, photos, "left", EXIFTOOL)
        self.assertIn("Stopped", result.refused)
        self.assertEqual(result.changed, batches.CHUNK)
        self.assertEqual([orientation_of(path) for path in photos].count(8), batches.CHUNK)
        change = result.details["change"]
        self.assertEqual(self.undo(change).changed, batches.CHUNK)
        self.assertEqual({orientation_of(path) for path in photos}, {None})


class WhereTheLibraryDoesNotHoldTheFolder(RotationCase):
    def test_a_just_look_photo_is_turned_in_its_file_only(self):
        loose_folder = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(loose_folder)
        loose = self.make("x.jpg", row=False, folder=loose_folder)
        held = self.make("h.jpg")
        result = photo_actions.rotate_photos(self.library, [held, loose], "right", EXIFTOOL)
        self.assertEqual((result.ok, result.changed, result.details["file_only"], result.details["with_rows"]), (True, 2, 1, 1))
        self.assertEqual((orientation_of(loose), orientation_of(held)), (6, 6))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos"), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM change_files"), [(1,)])


class WhatIsRefused(RotationCase):
    def test_a_direction_that_is_not_one_touches_nothing(self):
        path = self.make("a.jpg")
        self.assertIn("Direction", self.rotate(path, "up").refused)
        self.assertEqual((orientation_of(path), self.rows("SELECT COUNT(*) FROM changes WHERE operation = 'rotate'")), (None, [(0,)]))


if __name__ == "__main__":
    unittest.main()

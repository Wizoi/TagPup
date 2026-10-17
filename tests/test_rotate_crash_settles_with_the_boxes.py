"""A rotation's face boxes turn with the file, a crash half way is settled forward or back with them, and a rotation of many is
one batch with one change (tagpup.services.photos.rotate_photos, tagpup.services.rotation). The turn itself, its refusals and
the just-look photo are test_rotate_is_journaled; this file shares its fixtures.

The boxes and the vectors are made in the transaction that marks the file done, so a crash between the file and its rows leaves
a turned file with the boxes of the old picture, and the next start settles that by what the file holds. Real TIFFs and JPEGs
(Pillow writes the starting Orientation, the real ExifTool turns it and reads it back), rows as the indexer stores them.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, crash_at  # noqa: E402
from test_rotate_is_journaled import BOX, HEIGHT, MODEL, WIDTH, RotationCase, picture  # noqa: E402

from tagpup.services import batches, file_changes, photos as photo_actions  # noqa: E402
from tagpup.store import embeddings, file_journal  # noqa: E402
from tagpup.store.faces import turned_box  # noqa: E402


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
        self.assertEqual((self.boxes(), self.orientation(path)), ([BOX], None))

    def test_a_file_that_holds_its_old_orientation_after_the_write_has_its_boxes_turned_back(self):
        path = self.make("scan.tif", fmt="TIFF", orientation=1)
        self.face(path)

        def the_write_did_not_take(step):
            if step == "file written":
                picture(path, "TIFF", orientation=1)

        with mock.patch.object(file_changes, "_reached", side_effect=the_write_did_not_take):
            self.rotate(path, "left")
        # Taken out of its change (read back holding what it held): the boxes the done turned go back in the same transaction.
        self.assertEqual((self.orientation(path), self.boxes()), (1, [BOX]))

    def test_a_jpegs_boxes_stay_where_the_stored_pixels_are(self):
        path = self.make("a.jpg")
        face = self.face(path)
        self.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face, b"crop"))
        self.assertEqual(self.rotate(path, "right").details["faces_turned"], 0)
        self.assertEqual((self.boxes(), self.rows("SELECT jpeg FROM face_crops")), ([BOX], [(b"crop",)]))

    def test_a_crash_after_the_file_is_written_is_settled_forward_boxes_and_all(self):
        path = self.make("scan.tif", fmt="TIFF")
        face = self.face(path, name="Rowan Thackeray")
        self.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face, b"crop"))
        stat = os.stat(path)
        self.execute_with(lambda conn: embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size, b"\x00\x00\x80\x3f" * 4))
        change = self.crash("file written", path, "left")
        # The file is turned; its rows are not: the change says so, and is not finished.
        self.assertEqual((self.orientation(path), self.states(change), self.boxes()), (8, ["writing"], [BOX]))
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
        self.assertEqual(self.orientation(path), 8, "settling must not turn the file twice")
        # Settled once: nothing is left to settle, and the boxes are not turned again.
        self.assertEqual(self.settle(), 0)
        self.assertEqual(self.boxes(), [turned_box(BOX, "left", WIDTH, HEIGHT)])

    def test_a_crash_before_the_file_is_written_is_carried_out_whole(self):
        path = self.make("scan.tif", fmt="TIFF")
        self.face(path)
        change = self.crash("file writing", path, "right")
        self.assertEqual((self.orientation(path), self.boxes()), (None, [BOX]))
        self.assertEqual(self.settle(), 1)
        self.assertEqual((self.orientation(path), self.boxes(), self.status(change)),
                         (6, [turned_box(BOX, "right", WIDTH, HEIGHT)], "applied"))

    def test_a_crash_in_an_undo_is_settled_back_with_the_boxes(self):
        path = self.make("scan.tif", fmt="TIFF")
        self.face(path)
        change = self.rotate(path, "left").details["change"]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", 1)):
            with self.assertRaises(Crash):
                self.undo(change)
        self.assertEqual((self.orientation(path), self.boxes()), (None, [turned_box(BOX, "left", WIDTH, HEIGHT)]))
        self.assertEqual(self.settle(), 1)
        self.assertEqual((self.orientation(path), self.boxes(), self.status(change)), (None, [BOX], "undone"))


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
        self.assertEqual(set(self.orientations(photos)), {3})
        change = result.details["change"]
        self.assertEqual((self.states(change), self.status(change)), (["done"] * len(photos), "applied"))
        self.assertEqual(self.undo(change).changed, len(photos))
        self.assertEqual(set(self.orientations(photos)), {6})

    def test_a_batch_asked_to_stop_keeps_what_it_turned_and_can_undo_it(self):
        photos = [self.make("p%02d.jpg" % n) for n in range(batches.CHUNK + 5)]
        # Cancel is asked for at the first chunk's end.
        with mock.patch.object(file_changes, "between_chunks", return_value=False):
            result = photo_actions.rotate_photos(self.library, photos, "left", EXIFTOOL)
        self.assertIn("Stopped", result.refused)
        self.assertEqual(result.changed, batches.CHUNK)
        self.assertEqual(self.orientations(photos).count(8), batches.CHUNK)
        change = result.details["change"]
        self.assertEqual(self.undo(change).changed, batches.CHUNK)
        self.assertEqual(set(self.orientations(photos)), {None})


if __name__ == "__main__":
    unittest.main()

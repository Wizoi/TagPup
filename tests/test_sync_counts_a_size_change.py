"""A file whose size changed is counted by sync and its picture is left alone (docs/findings.md, #336, #903, #904).

A changed row is re-read as refresh_rows does -- tags, captions, raw metadata, stamp -- and keeps its CLIP vector and face
boxes. A keyword write changes a file's size too (on photo_index every size change was one), so sync only COUNTS the
photos whose size changed (`size_changed`) and the CLI says so; the owner fixes a real picture edit by hand (the setting
that took the vector and faces away, `library.reread_resized_pictures`, is gone). A library that still holds that key in
its settings table is read all the same (tests/test_settings.py).

A rehearsal changes nothing. Rows are made as the indexer makes them (tests/photo_rows); the stand-in ExifTool is test_sync's.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from face_rows import add_face, add_vector  # noqa: E402
from test_sync import THEN, SyncTestCase  # noqa: E402

from tagpup.core.result import Result  # noqa: E402
from tagpup.store import db, faces_detected  # noqa: E402

DETECTOR = faces_detected.detector({"min_face_size": 20})
VECTOR = np.array([1.0, 0.0], dtype=np.float32).tobytes()


class ASizeChange(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.photo(self.meet, "IMG_0001.jpg", body=b"the picture as it was", **{"XMP:Subject": ["Events/Invitational"]})
        self.other = self.photo(self.trip, "IMG_0002.jpg", body=b"another picture", **{"XMP:Subject": ["Weather/Rain"]})
        self.indexed(self.path, self.other)
        conn = db.connect(self.db_path)
        try:
            for path in (self.path, self.other):
                add_vector(conn, path, VECTOR)
                add_face(conn, path, [0, 0, 10, 10])
                faces_detected.record(conn, path, DETECTOR, None, 1)
            conn.commit()
        finally:
            conn.close()
        self.queued = []

    def queue(self, folders):
        self.queued.append(sorted(folders))
        return Result(attempted=len(folders), changed=len(folders))

    def edit(self, path, body):
        """The file written elsewhere: more bytes, a later time."""
        with open(path, "wb") as handle:
            handle.write(body)
        os.utime(path, (THEN + 100, THEN + 100))

    def held(self, path):
        """(faces, vectors, detection records) the library holds of the photo."""
        return self.query(
            "SELECT (SELECT COUNT(*) FROM faces WHERE photo_id = p.id),"
            " (SELECT COUNT(*) FROM embeddings WHERE photo_id = p.id),"
            " (SELECT COUNT(*) FROM faces_detected WHERE photo_id = p.id) FROM photos p WHERE p.path = ?", (path,))[0]

    def test_a_size_change_is_counted_and_the_picture_is_left_alone(self):
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, result.details["counts"]["size_changed"])
        self.assertEqual((1, 1, 1), self.held(self.path), "its faces, vector and the record of them stay")
        self.assertEqual((1, 1, 1), self.held(self.other), "a photo that did not change was touched")
        self.assertEqual([], self.queued)
        self.assertEqual([], self.query("SELECT photo_id FROM faces_pending"))

    def test_the_row_is_read_again_like_any_changed_row(self):
        self.truth["IMG_0001.jpg"] = {"XMP:Subject": ["Events/Classic"]}
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual(1, result.details["changed"]["from_files"])
        tags = self.query("SELECT tags FROM photos WHERE path = ?", (self.path,))[0][0]
        self.assertIn("Events/Classic", tags)
        self.assertNotIn("Events/Invitational", tags)

    def test_a_decided_face_is_left_as_it_was(self):
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE faces SET name = 'Rowan Thackeray', name_source = 'manual' WHERE photo_id ="
                         " (SELECT id FROM photos WHERE path = ?)", (self.path,))
            conn.commit()
        finally:
            conn.close()
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual([("Rowan Thackeray",)], self.query(
            "SELECT f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE p.path = ?", (self.path,)))

    def test_a_write_that_changed_the_time_and_not_the_size_is_not_a_size_change(self):
        self.edit(self.path, b"the picture as it was")      # the same length
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 0), (result.details["counts"]["changed"], result.details["counts"]["size_changed"]))
        self.assertEqual((1, 1, 1), self.held(self.path))

    def test_a_file_that_cannot_be_read_now_is_not_counted_and_is_left_for_the_next_sync(self):
        self.truth["IMG_0001.jpg"] = {}      # ExifTool answers nothing for it
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual((1, 0), (result.details["counts"]["unreadable"], result.details["counts"]["size_changed"]))

    def test_a_rehearsal_changes_nothing_and_counts_the_size_change(self):
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync()
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual(1, result.details["counts"]["size_changed"])


if __name__ == "__main__":
    unittest.main()

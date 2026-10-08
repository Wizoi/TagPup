"""A file whose size changed has its picture read again by sync -- only with library.reread_resized_pictures on (docs/findings.md,
#336, #903, #904).

A changed row was re-read as refresh_rows does -- tags, captions, raw metadata, stamp -- and kept its CLIP vector and face
boxes. A keyword write changes a file's size too (on photo_index every size change was one), so the setting is OFF, and sync
only counts the photos whose size changed. ON, applied:

- a photo with no face a person decided: its vector and faces are taken away, it is marked to have its faces detected
  (store.faces_pending: seen, and counted, until an index has run), and its folder is queued;
- a photo with a decided face (named by hand, or excluded): its faces keep their names and decisions; its vector is taken
  away and its folder queued, so the index makes the vector again.

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

    def run_sync(self, **more):
        more.setdefault("reread_resized", True)
        return super().run_sync(**more)

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

    def test_a_photo_with_no_decided_face_is_embedded_and_detected_again(self):
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertTrue(result.ok, result.message())
        self.assertEqual((0, 0, 0), self.held(self.path))
        self.assertEqual((1, 1, 1), self.held(self.other), "a photo that did not change was touched")
        self.assertEqual([[self.meet]], self.queued)
        self.assertEqual({"redetect": 1, "decided_kept": 0, "faces_removed": 1, "vectors_removed": 1, "pending": 1},
                         result.details["pictures"])
        self.assertEqual([(self.path,)], self.query(
            "SELECT p.path FROM faces_pending f JOIN photos p ON p.id = f.photo_id"), "an index that never runs is not seen")

    def test_it_is_off_unless_the_library_says_so(self):
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue, reread_resized=False)
        self.assertEqual((1, 1, 1), self.held(self.path), "the picture was read again with the setting off")
        self.assertEqual([], self.queued)
        self.assertEqual(1, result.details["counts"]["size_changed"], "the size changes are still counted")
        self.assertFalse(result.details["pictures_reread"])

    def test_the_setting_is_a_library_setting_off_by_default(self):
        from tagpup.services import settings
        found = settings.read(self.library)
        self.assertFalse(found.reread_resized_pictures)

    def test_a_decided_face_keeps_its_name_and_its_vector_is_made_again(self):
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE faces SET name = 'Rowan Thackeray', name_source = 'manual' WHERE photo_id ="
                         " (SELECT id FROM photos WHERE path = ?)", (self.path,))
            conn.commit()
        finally:
            conn.close()
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 0, 1), self.held(self.path), "its faces and the record of them stay, its vector goes")
        self.assertEqual([("Rowan Thackeray",)], self.query(
            "SELECT f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE p.path = ?", (self.path,)))
        self.assertEqual([[self.meet]], self.queued, "its folder is queued so the vector is made again")
        self.assertEqual({"redetect": 0, "decided_kept": 1, "faces_removed": 0, "vectors_removed": 1, "pending": 0},
                         result.details["pictures"])

    def test_an_excluded_face_is_a_decision_too(self):
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE faces SET excluded = 1")
            conn.commit()
        finally:
            conn.close()
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 0, 1), self.held(self.path))

    def test_a_write_that_changed_the_time_and_not_the_size_leaves_the_picture_alone(self):
        self.edit(self.path, b"the picture as it was")      # the same length
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual(1, result.details["counts"]["changed"])
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual({"redetect": 0, "decided_kept": 0, "faces_removed": 0, "vectors_removed": 0, "pending": 0},
                         result.details["pictures"])

    def test_a_file_that_cannot_be_read_now_keeps_its_faces_for_the_next_sync(self):
        self.truth["IMG_0001.jpg"] = {}      # ExifTool answers nothing for it
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual(0, result.details["pictures"]["redetect"])

    def test_a_rehearsal_changes_nothing_and_says_what_it_would(self):
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        result = self.run_sync()
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual((1, 0), (result.details["counts"]["size_changed"], result.details["counts"]["size_changed_decided"]))

    def test_a_picture_the_index_has_already_read_again_is_left_as_it_is(self):
        # An index run that finished between the sync's plan and its write has made a vector from the new file: its
        # faces are the new picture's.
        self.edit(self.path, b"the picture, edited elsewhere and longer")
        stat = os.stat(self.path)
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE embeddings SET mtime = ?, size = ? WHERE photo_id = (SELECT id FROM photos WHERE path = ?)",
                         (stat.st_mtime, stat.st_size, self.path))
            conn.commit()
        finally:
            conn.close()
        result = self.run_sync(apply=True, queue=self.queue)
        self.assertEqual((1, 1, 1), self.held(self.path))
        self.assertEqual(0, result.details["pictures"]["redetect"])


if __name__ == "__main__":
    unittest.main()

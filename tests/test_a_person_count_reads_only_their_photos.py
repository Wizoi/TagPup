"""docs/findings.md, #844: counting the photos of a person that wait for a face's name reads that person's photos.

`faces-from-tags` asks, for each person who has no decided face, whether more than one photo of theirs has a
face to be named (face_tags._photos_to_be_named, #839). It joined the person's keyword rows to the faces, and
SQLite drove the join from the faces: every unnamed face of the library, 190,000 on photo_index, each probing
photo_people. 113 people took 15.1 of the plan's 27.5 seconds there. The join now starts from the person's rows
and goes to each photo's faces by idx_faces_photo_id, as the folder reads of tagpup.store.faces do (#644).

A fixture cannot time it, so the work is counted: the SQLite virtual machine's instructions for the count with
many faces in the library that have nothing to do with the person, which grew with them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_from_tags import WREN, Case  # noqa: E402

from tagpup.store import db, face_tags  # noqa: E402

#: The instructions between two calls of the progress handler.
EVERY = 50


def instructions(path, work):
    """About how many virtual machine instructions `work(conn)` took, and what it answered."""
    conn = db.connect(db.readonly_uri(path), uri=True)
    calls = []
    try:
        conn.set_progress_handler(lambda: calls.append(1) and 0, EVERY)
        answer = work(conn)
        conn.set_progress_handler(None, 0)
    finally:
        conn.close()
    return len(calls) * EVERY, answer


class CountingAPersonsPhotos(Case):
    def seed(self, others):
        """Wren on two photos with a face each, and `others` photos with a face each and no keyword."""
        for number in range(2):
            self.face(self.photo("regatta_%03d.jpg" % number, ["People/" + WREN]))
        for number in range(others):
            self.face(self.photo("quay_%04d.jpg" % number))

    def test_the_count_does_not_grow_with_the_faces_of_everyone_else(self):
        self.seed(30)
        few, answer = instructions(self.path, lambda conn: face_tags._photos_to_be_named(conn, WREN))
        self.assertEqual(2, answer)
        for number in range(30, 400):
            self.face(self.photo("quay_%04d.jpg" % number))
        many, answer = instructions(self.path, lambda conn: face_tags._photos_to_be_named(conn, WREN))
        self.assertEqual(2, answer)
        self.assertLess(many, few * 2, "%d instructions with 400 other faces, %d with 30" % (many, few))


if __name__ == "__main__":
    unittest.main()

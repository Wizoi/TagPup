"""docs/findings.md, #844: counting the photos of a person that wait for a face's name reads that person's photos.

`faces-from-tags` asks, for each person who has no decided face, whether more than one photo of theirs has a
face to be named (face_tags._photos_to_be_named, #839). It joined the person's keyword rows to the faces, and
SQLite drove the join from the faces: every unnamed face of the library, 190,000 on photo_index, each probing
photo_people. 113 people took 15.1 of the plan's 27.5 seconds there. The join now starts from the person's rows
and goes to each photo's faces by idx_faces_photo_id, as the folder reads of tagpup.store.faces do (#644).

A fixture cannot time it, and counting the work of 400 extra photos took 47 seconds to build: the query that runs is
captured and its plan read. Mutation: the join left to SQLite (no CROSS JOIN, no INDEXED BY) plans a scan of faces.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_from_tags import WREN, Case  # noqa: E402

from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.store import db, face_tags  # noqa: E402


class CountingAPersonsPhotos(Case):
    def test_the_count_starts_from_the_persons_photos_and_never_scans_the_faces(self):
        for number in range(2):
            self.face(self.photo("regatta_%03d.jpg" % number, ["People/" + WREN]))
        for number in range(30):
            self.face(self.photo("quay_%04d.jpg" % number))
        wren = Ref(self.node("People/" + WREN), WREN)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        ran = []
        conn.set_trace_callback(ran.append)
        self.assertEqual(2, face_tags._photos_to_be_named(conn, wren))
        conn.set_trace_callback(None)
        query, = [sql for sql in ran if sql.lstrip().startswith("SELECT pp.photo_id")]
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
        self.assertFalse([step for step in plan if step.startswith("SCAN") and "photo_people" not in step],
                         "a table other than the person's rows is scanned: %s" % plan)
        self.assertTrue([step for step in plan if "idx_faces_photo_id" in step], "faces not found by their photo: %s" % plan)
        self.assertTrue([step for step in plan if "idx_photo_people_tag" in step], "the person's rows not found by node: %s" % plan)


if __name__ == "__main__":
    unittest.main()

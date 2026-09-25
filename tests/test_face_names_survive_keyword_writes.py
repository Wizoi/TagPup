"""Writing a photo's keywords keeps everyone its faces were named as.

A photo's people have two sources: naming a face in TagTuner adds the person, without
necessarily writing a keyword, and every keyword write rebuilt the column from the
keywords alone -- so tagging a photo, or re-indexing it, silently took off everyone
identified only by their face. Found by a dry run of the row refresh on a real
library, where 33 rows would have lost people this way.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.services.search import PhotoIndex  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face, configured_model, people_of  # noqa: E402

from tagpup.store import db, people  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.store.photos import record_tags  # noqa: E402


class FaceNamesSurviveKeywordWrites(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_names_")
        self.db = os.path.join(self.dir, "lib.db")
        self.photo = os.path.join(self.dir, "Meet - 01.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"jpeg")
        index = PhotoIndex(self.db, configured_model())
        index.load()
        index.conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
            " VALUES (?, 0, 0, ?, '[]', ?)",
            (self.photo, json.dumps(["Activity/Running"]),
             json.dumps({"XMP:Subject": ["Activity/Running"]})))
        emb = np.zeros(4, dtype=np.float32).tobytes()
        # Named in Identify Faces only; and excluded, so not in the photo's people.
        add_face(index.conn, self.photo, box="[0,0,1,1]", embedding=emb, name="Rowan Thackeray", excluded=0)
        add_face(index.conn, self.photo, box="[0,0,1,1]", embedding=emb, name="Imogen Vale", excluded=1)
        people.rebuild(index.conn)
        index.conn.commit()
        index.close()
        store_taxonomy.forget_people_paths()

    def tearDown(self):
        store_taxonomy.forget_people_paths()
        shutil.rmtree(self.dir, ignore_errors=True)

    def listed(self):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return people_of(conn, self.photo)
        finally:
            conn.close()

    def test_a_bulk_keyword_write_keeps_a_face_named_person(self):
        self.assertTrue(record_tags(self.db, self.photo, ["Activity/Running", "Event/Classic"]))
        self.assertEqual(self.listed(), ["Rowan Thackeray"])

    def test_re_indexing_the_photo_keeps_a_face_named_person(self):
        index = PhotoIndex(self.db, configured_model())
        index.load()
        try:
            index.build_or_update([[0.0, 0.0, 0.0, 1.0]], [{
                "path": self.photo, "mtime": 1.0, "size": 4, "tags": ["Activity/Running"],
                "people": [], "captions": [], "raw_metadata": {}}], reload=False)
        finally:
            index.close()
        self.assertEqual(self.listed(), ["Rowan Thackeray"])


if __name__ == "__main__":
    unittest.main()

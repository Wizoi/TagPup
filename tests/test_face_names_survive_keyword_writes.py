"""Writing a photo's keywords keeps everyone its faces were named as.

photos.people has two sources: naming a face in TagTuner adds the person, without
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import tagpup_server  # noqa: E402
from index import PhotoIndex  # noqa: E402


class FaceNamesSurviveKeywordWrites(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="face_names_")
        self.db = os.path.join(self.dir, "lib.db")
        self.photo = os.path.join(self.dir, "Meet - 01.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"jpeg")
        index = PhotoIndex(self.db)
        index.load()
        index.conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, 0, 0, ?, ?, '[]', ?)",
            (self.photo, json.dumps(["Activity/Running"]), json.dumps(["Rowan Thackeray"]),
             json.dumps({"XMP:Subject": ["Activity/Running"]})))
        emb = np.zeros(4, dtype=np.float32).tobytes()
        index.conn.executemany(
            "INSERT INTO faces (photo_path, box, embedding, name, excluded) VALUES (?, '[0,0,1,1]', ?, ?, ?)",
            [(self.photo, emb, "Rowan Thackeray", 0),   # named in Identify Faces only
             (self.photo, emb, "Imogen Vale", 1)])      # excluded: not in the photo's people
        index.conn.commit()
        index.close()
        tagpup_server.invalidate_people_cache()

    def tearDown(self):
        tagpup_server.invalidate_people_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def people(self):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return json.loads(conn.execute("SELECT people FROM photos").fetchone()[0])
        finally:
            conn.close()

    def test_a_bulk_keyword_write_keeps_a_face_named_person(self):
        self.assertTrue(tagpup_server.record_tags_in_index(
            self.db, self.photo, ["Activity/Running", "Event/Classic"]))
        self.assertEqual(self.people(), ["Rowan Thackeray"])

    def test_re_indexing_the_photo_keeps_a_face_named_person(self):
        index = PhotoIndex(self.db)
        index.load()
        try:
            index.build_or_update([[0.0, 0.0, 0.0, 1.0]], [{
                "path": self.photo, "mtime": 1.0, "size": 4, "tags": ["Activity/Running"],
                "people": [], "captions": [], "raw_metadata": {}}], reload=False)
        finally:
            index.close()
        self.assertEqual(self.people(), ["Rowan Thackeray"])


if __name__ == "__main__":
    unittest.main()

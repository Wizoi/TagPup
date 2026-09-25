"""Opening New Person reads the nameless faces once per state of the faces table
(docs/findings.md, #5).

Every open read all of them -- 190,000 embeddings, 390 MB, on the real library -- and
dotted each with the chosen face: two to three seconds a click. They are kept now, per
library, against the faces fingerprint; naming or excluding faces takes those out of
what is kept, and any other change reads them again. The faces found are still scored
from their rows, so the answer is the one the full read gave.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.store import db  # noqa: E402

ROUTE = "/library/api/face-matches-unmatched"


def like(likeness):
    """A unit vector `likeness`-like the seed's, [1, 0, ...]."""
    vector = np.zeros(512, dtype=np.float32)
    vector[0], vector[1] = likeness, np.sqrt(1 - likeness * likeness)
    return vector


def pool_reads(statements):
    """The statements that read every nameless face's embedding."""
    return [s for s in statements if "embedding" in s and "name IS NULL" in s and " IN (" not in s]


class NewPerson(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()
        self.db_path = self.home.library("library.db")
        conn = db.connect(self.db_path)
        try:
            self.ids = {}
            for n, (label, likeness, columns) in enumerate([
                    ("seed", 1.0, {}),
                    ("close", 0.9, {}),
                    ("just over", 0.8003, {}),
                    ("just under", 0.7997, {}),
                    ("far", 0.5, {}),
                    ("named", 0.95, {"name": "Imogen Vale", "name_source": "manual"}),
                    ("excluded", 0.95, {"excluded": 1, "excluded_reason": "stranger"})]):
                photo = os.path.join(self.home.root, "Photos", "IMG_%04d.jpg" % n)
                photo_rows.add_read(conn, photo, {"XMP:Subject": []})
                self.ids[label] = face_rows.add_face(conn, photo, embedding=like(likeness).tobytes(), **columns)
            conn.commit()
        finally:
            conn.close()
        self.statements = []
        connect = db.connect

        def traced(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(self.statements.append)
            return conn

        patcher = mock.patch.object(db, "connect", side_effect=traced)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_new_person(self, seed="seed"):
        reply = self.client.get(ROUTE, query_string={"id": self.ids[seed]})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["matches"]

    def labels(self, matches):
        by_id = {face_id: label for label, face_id in self.ids.items()}
        return [by_id[m["id"]] for m in matches]

    def test_offers_the_faces_alike_enough_best_first_scored_from_their_rows(self):
        matches = self.open_new_person()
        self.assertEqual(["close", "just over"], self.labels(matches))
        self.assertAlmostEqual(0.9, matches[0]["similarity"], places=5)
        self.assertEqual("IMG_0001.jpg", matches[0]["filename"])
        self.assertEqual([0, 0, 10, 10], matches[0]["box"])

    def test_a_second_open_does_not_read_every_nameless_face_again(self):
        self.open_new_person()
        self.open_new_person("close")
        self.assertEqual(1, len(pool_reads(self.statements)))

    def test_a_face_named_since_is_left_out_without_reading_them_all_again(self):
        self.open_new_person()
        reply = self.client.post("/library/api/face/match",
                                 json={"face_id": self.ids["close"], "person_name": "Rowan Thackeray"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["just over"], self.labels(self.open_new_person()))
        self.assertEqual(1, len(pool_reads(self.statements)))

    def test_a_face_added_since_is_found(self):
        self.open_new_person()
        conn = db.connect(self.db_path)
        try:
            photo = os.path.join(self.home.root, "Photos", "IMG_0100.jpg")
            photo_rows.add_read(conn, photo, {"XMP:Subject": []})
            self.ids["new"] = face_rows.add_face(conn, photo, embedding=like(0.85).tobytes())
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(["close", "new", "just over"], self.labels(self.open_new_person()))


if __name__ == "__main__":
    unittest.main()

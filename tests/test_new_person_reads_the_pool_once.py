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

from tagpup.jobs import identify as identify_jobs  # noqa: E402
from tagpup.services import identify  # noqa: E402
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


class BuildingThePool(unittest.TestCase):
    """The pool is filled a slice at a time from the rows as they are read, not stacked
    from a list of them: it holds what stacking held."""

    def rows(self):
        rng = np.random.default_rng(7)
        found = [(n * 3 + 1, rng.standard_normal(8).astype(np.float32).tobytes()) for n in range(11)]
        found.insert(4, (100, None))   # a face without an embedding
        found.insert(7, (101, b""))
        return found

    def stacked(self, rows):
        kept = [(face_id, np.frombuffer(blob, dtype=np.float32)) for face_id, blob in rows if blob]
        matrix = np.vstack([v for _, v in kept])
        return [f for f, _ in kept], matrix.astype(np.float16), float(np.max(np.linalg.norm(matrix, axis=1)))

    def check(self, pool, rows):
        ids, matrix, largest = self.stacked(rows)
        self.assertEqual(ids, pool.ids.tolist())
        self.assertEqual(np.float16, pool.matrix.dtype)
        np.testing.assert_array_equal(matrix, pool.matrix)
        self.assertAlmostEqual(largest, pool.largest_norm, places=5)

    def test_slice_by_slice_is_what_stacking_gave(self):
        rows = self.rows()
        with mock.patch.object(identify.UnnamedFaces, "SLICE", 3):
            for count in (len(rows), len(rows) - 2, 5, None):   # as counted, fewer, more
                with self.subTest(count=count):
                    self.check(identify.UnnamedFaces.of(iter(rows) if count else rows, count), rows)

    def test_no_embeddings_is_an_empty_pool(self):
        pool = identify.UnnamedFaces.of(iter([(1, None)]), 1)
        self.assertEqual(0, len(pool.ids))
        self.assertEqual([], pool.near(np.ones(8, dtype=np.float32), lambda likeness, slack: likeness > -9))

    def test_a_stale_pool_is_let_go_before_the_next_is_read(self):
        cache = identify_jobs.GridCache()
        cache.put("unnamed_faces", "an older state", "the stale pool")
        held = []

        def read(library):
            held.append(cache.entry("unnamed_faces"))
            return "now", "the new pool"

        with mock.patch.object(identify, "fingerprint", return_value="now"), \
                mock.patch.object(identify, "unnamed_faces", side_effect=read):
            self.assertEqual("the new pool", identify_jobs.unnamed_faces(object(), cache))
        self.assertEqual([None], held)


if __name__ == "__main__":
    unittest.main()

"""Clicking face cards reads the named faces once, not once per click.

Each click on a face card in Identify Faces asks /api/photo-details for the photo, and
that read every named face's embedding from SQLite -- 35,826 rows, half a second on the
real library -- to show one similarity per face. /api/face-matches already used a
matrix built once per state of the faces table; photo-details and both automatch
handlers now use the same one.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
import tuner_server  # noqa: E402
from index import PhotoIndex  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"


class Handler(TunerHTTPRequestHandler):
    def __init__(self, db_path):  # noqa: D107 -- no socket, on purpose
        self.db_path = db_path
        self.reply = None
        self.wfile = io.BytesIO()

    def send_json(self, data):
        self.reply = data

    def send_error(self, code, message=None):
        self.reply = {"status": code, "error": message}


class FaceClickReadsNamedFacesOnce(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="named_once_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        emb = lambda v: np.asarray(v, dtype=np.float32).tobytes()  # noqa: E731
        conn = tagpup_db.connect(self.db)
        conn.execute("INSERT INTO photos (path, tags, people, captions) VALUES (?, '[]', '[]', '[]')", (PHOTO,))
        add_face(conn, PHOTO, box="[1,2,3,4]", embedding=emb([1, 0, 0, 0]), name="Rowan Thackeray")
        add_face(conn, PHOTO, box="[5,6,7,8]", embedding=emb([0.8, 0.6, 0, 0]))
        conn.commit()
        conn.close()
        tuner_server.set_active_db_path(self.db)
        self.addCleanup(tuner_server.set_active_db_path, None)
        TunerHTTPRequestHandler.identify_cache.clear()

    def test_two_clicks_read_the_named_faces_once(self):
        statements = []
        real_connect = tagpup_db.connect

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with mock.patch.object(tuner_server.tagpup_db, "connect", side_effect=connect):
            for _ in range(2):
                handler = Handler(self.db)
                handler.handle_get_photo_details({"path": [PHOTO]})

        named_reads = [s for s in statements if "embedding" in s and "name IS NOT NULL" in s]
        self.assertEqual(1, len(named_reads), "every click re-read the named faces:\n" + "\n".join(named_reads))
        similarities = sorted(f["max_similarity"] for f in handler.reply["faces"])
        self.assertAlmostEqual(0.8, similarities[0], places=4)
        self.assertAlmostEqual(1.0, similarities[1], places=4)


if __name__ == "__main__":
    unittest.main()

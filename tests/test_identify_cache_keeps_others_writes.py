"""An exclude does not stamp somebody else's write as accounted for in the cached grid.

After naming or excluding faces, the cached Identify Faces grids have those cards taken
out and are re-stamped with the table's new fingerprint, rather than rebuilt. The
fingerprint was read before the write and again after the commit, with nothing held in
between: faces the indexer (or TagPup) committed in that window were counted into the
new stamp, so the grid never showed them. Now both are read inside one write
transaction, and a write from elsewhere waits until it is over -- and then moves the
fingerprint, as it should.
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
import tuner_server  # noqa: E402
from index import PhotoIndex  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"
KEY = "matches:Unknown Faces"


class Handler(TunerHTTPRequestHandler):
    def __init__(self, db_path, body):  # noqa: D107 -- no socket, on purpose
        self.db_path = db_path
        self.body = body
        self.reply = None
        self.wfile = io.BytesIO()

    def read_json_body(self):
        return self.body

    def send_json(self, data):
        self.reply = data

    def send_error(self, code, message=None):
        self.reply = {"status": code, "error": message}

    def send_json_error(self, code, message):
        self.reply = {"status": code, "error": message}


class IdentifyCacheKeepsOthersWrites(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cache_others_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = tagpup_db.connect(self.db)
        conn.execute("INSERT INTO photos (path, people) VALUES (?, '[]')", (PHOTO,))
        self.faces = [conn.execute("INSERT INTO faces (photo_path, box) VALUES (?, '[1,2,3,4]')", (PHOTO,)).lastrowid
                      for _ in range(3)]
        conn.commit()
        conn.close()
        tuner_server.set_active_db_path(self.db)
        self.addCleanup(tuner_server.set_active_db_path, None)
        TunerHTTPRequestHandler.identify_cache.clear()
        self.addCleanup(TunerHTTPRequestHandler.identify_cache.clear)

    def fingerprint(self):
        conn = tagpup_db.connect(self.db)
        try:
            return TunerHTTPRequestHandler.faces_fingerprint(None, conn)
        finally:
            conn.close()

    def test_a_face_indexed_during_an_exclude_is_not_counted_into_the_grid(self):
        TunerHTTPRequestHandler.identify_cache[KEY] = {
            "fingerprint": self.fingerprint(),
            "value": {"faces": [{"id": f} for f in self.faces], "total_count": 3},
        }
        handler = Handler(self.db, {"face_ids": [self.faces[0]], "reason": "stranger"})
        pending = []
        real = store_faces.fingerprint

        # The write reads the fingerprint in the store (tagpup.store.faces.accounted_write).
        def fingerprint(conn):
            value = real(conn)
            if not pending:
                # The indexer, writing while the exclude is under way.
                other = sqlite3.connect(self.db, timeout=0.2)
                try:
                    other.execute("INSERT INTO faces (photo_path, box) VALUES (?, '[5,6,7,8]')", (PHOTO,))
                    other.commit()
                    pending.append("written")
                except sqlite3.OperationalError:
                    pending.append("waiting")
                finally:
                    other.close()
            return value

        with mock.patch.object(store_faces, "fingerprint", side_effect=fingerprint):
            handler.handle_post_faces_exclude()
        self.assertEqual(["waiting"], pending, "the other write was not held off")
        self.assertEqual(200, getattr(handler, "status", 200))
        if pending == ["waiting"]:
            # Held off until the exclude committed; it lands now.
            conn = tagpup_db.connect(self.db)
            conn.execute("INSERT INTO faces (photo_path, box) VALUES (?, '[5,6,7,8]')", (PHOTO,))
            conn.commit()
            conn.close()

        entry = TunerHTTPRequestHandler.identify_cache.get(KEY)
        self.assertTrue(entry is None or entry["fingerprint"] != self.fingerprint(),
                        "the grid was stamped as including a face it has never seen")


if __name__ == "__main__":
    unittest.main()

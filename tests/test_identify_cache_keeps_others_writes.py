"""An exclude does not stamp somebody else's write as accounted for in the cached grid.

After naming or excluding faces, the cached Identify Faces grids have those cards taken
out and are re-stamped with the table's new fingerprint, rather than rebuilt. The
fingerprint was read before the write and again after the commit, with nothing held in
between: faces the indexer (or TagPup) committed in that window were counted into the
new stamp, so the grid never showed them. Now both are read inside one write
transaction, and a write from elsewhere waits until it is over -- and then moves the
fingerprint, as it should.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.services.search import PhotoIndex  # noqa: E402
from face_rows import add_face  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"
KEY = "matches:Unknown Faces"


class IdentifyCacheKeepsOthersWrites(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cache_others_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = db.connect(self.db)
        conn.execute("INSERT INTO photos (path) VALUES (?)", (PHOTO,))
        self.faces = [add_face(conn, PHOTO, box="[1,2,3,4]") for _ in range(3)]
        conn.commit()
        conn.close()
        tuner_client.forget(self.db)
        self.addCleanup(tuner_client.forget, self.db)
        self.cache = tuner_client.grid_cache(self.db)
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db))

    def fingerprint(self):
        conn = db.connect(self.db)
        try:
            return store_faces.fingerprint(conn)
        finally:
            conn.close()

    def test_a_face_indexed_during_an_exclude_is_not_counted_into_the_grid(self):
        self.cache.put(KEY, self.fingerprint(),
                       {"faces": [{"id": f} for f in self.faces], "total_count": 3})
        pending = []
        real = store_faces.fingerprint

        # The write reads the fingerprint in the store (tagpup.store.faces.accounted_write).
        def fingerprint(conn):
            value = real(conn)
            if not pending:
                # The indexer, writing while the exclude is under way.
                other = sqlite3.connect(self.db, timeout=0.2)
                try:
                    add_face(other, PHOTO, box="[5,6,7,8]")
                    other.commit()
                    pending.append("written")
                except sqlite3.OperationalError:
                    pending.append("waiting")
                finally:
                    other.close()
            return value

        with mock.patch.object(store_faces, "fingerprint", side_effect=fingerprint):
            status, body = self.requests.post("/api/faces/exclude",
                                              {"face_ids": [self.faces[0]], "reason": "stranger"})
        self.assertEqual(["waiting"], pending, "the other write was not held off")
        self.assertEqual(200, status, body)
        if pending == ["waiting"]:
            # Held off until the exclude committed; it lands now.
            conn = db.connect(self.db)
            add_face(conn, PHOTO, box="[5,6,7,8]")
            conn.commit()
            conn.close()

        entry = self.cache.entry(KEY)
        self.assertTrue(entry is None or entry["fingerprint"] != self.fingerprint(),
                        "the grid was stamped as including a face it has never seen")


if __name__ == "__main__":
    unittest.main()

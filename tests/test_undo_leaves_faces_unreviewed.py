"""Undoing an assignment puts faces back unreviewed, not marked as nobody.

unmatch-bulk records a person's decision that a face is nobody (name_source 'manual'),
and the page's Undo used it to take back a one-click assign. So an undo left every face
marked as deliberately nobody -- a decision nobody made, which clustering then honours.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from index import PhotoIndex  # noqa: E402
from face_rows import add_people  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"


class UndoLeavesFacesUnreviewed(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="undo_unmatch_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = db.connect(self.db)
        conn.execute("INSERT INTO photos (path) VALUES (?)", (PHOTO,))
        self.face = conn.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES"
                                 " ((SELECT id FROM photos WHERE path = ?), '[1,2,3,4]', 'Rowan Thackeray', 'manual')",
                                 (PHOTO,)).lastrowid
        add_people(conn, PHOTO, ["Rowan Thackeray"], source="face")
        conn.commit()
        conn.close()
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db))

    def source_after(self, body):
        status, reply = self.requests.post("/api/faces/unmatch-bulk", body)
        self.assertEqual(200, status, reply)
        conn = db.connect(self.db)
        try:
            return conn.execute("SELECT name, name_source FROM faces WHERE id = ?", (self.face,)).fetchone()
        finally:
            conn.close()

    def test_an_undo_leaves_the_face_unreviewed(self):
        self.assertEqual((None, None), self.source_after({"face_ids": [self.face], "undo": True}))

    def test_an_unmatch_is_still_a_decision(self):
        self.assertEqual((None, "manual"), self.source_after({"face_ids": [self.face]}))


if __name__ == "__main__":
    unittest.main()

"""Undoing an assignment puts faces back unreviewed, not marked as nobody.

unmatch-bulk records a person's decision that a face is nobody (name_source 'manual'),
and the page's Undo used it to take back a one-click assign. So an undo left every face
marked as deliberately nobody -- a decision nobody made, which clustering then honours.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
from index import PhotoIndex  # noqa: E402
from tuner_server import TunerHTTPRequestHandler  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_people  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"


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


class UndoLeavesFacesUnreviewed(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="undo_unmatch_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = tagpup_db.connect(self.db)
        conn.execute("INSERT INTO photos (path) VALUES (?)", (PHOTO,))
        self.face = conn.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES"
                                 " ((SELECT id FROM photos WHERE path = ?), '[1,2,3,4]', 'Rowan Thackeray', 'manual')",
                                 (PHOTO,)).lastrowid
        add_people(conn, PHOTO, ["Rowan Thackeray"], source="face")
        conn.commit()
        conn.close()

    def source_after(self, body):
        Handler(self.db, body).handle_post_unmatch_bulk()
        conn = tagpup_db.connect(self.db)
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

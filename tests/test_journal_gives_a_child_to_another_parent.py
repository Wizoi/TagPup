"""A child given to another parent in a change is not deleted with the parent it leaves (tagpup.store.journal).

The cascade of a delete was read from the rows as they stood before the change, so a face re-pointed at another photo by one
edit was taken as a child of the photo another edit deletes, and deleted with it. A row an update of the change writes the
column of that names its parent is left out of the cascade. Needed by tagpup.services.duplicate_rows: the faces of a row
are moved onto the row kept, and the row is deleted, in one change that one undo takes back.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, journal  # noqa: E402


class AFaceMovedBeforeItsPhotoIsDeleted(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="journal_child_")
        self.db_path = home.library("library.db")
        library_actions.create(self.db_path)
        self.leaving = os.path.join(home.root, "Pictures", "a.jpg")
        self.staying = os.path.join(home.root, "Pictures", "b.jpg")
        conn = db.connect(self.db_path)
        try:
            self.leaving_id = photo_rows.add_read(conn, self.leaving, {})
            self.staying_id = photo_rows.add_read(conn, self.staying, {})
            self.moved = add_face(conn, self.leaving, [0, 0, 10, 10], embedding=b"a")
            self.lost = add_face(conn, self.leaving, [20, 20, 30, 30], embedding=b"b")
            conn.commit()
        finally:
            conn.close()

    def faces(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT id, photo_id FROM faces ORDER BY id").fetchall()
        finally:
            conn.close()

    def edits(self):
        return [journal.update("faces", (self.moved,), {"photo_id": self.leaving_id}, {"photo_id": self.staying_id}),
                journal.delete("photos", (self.leaving_id,), {})]

    def test_the_moved_face_survives_and_the_other_goes_with_the_photo(self):
        applied = journal.apply(self.db_path, "move and delete", self.edits())
        self.assertEqual([(self.moved, self.staying_id)], self.faces())
        self.assertIsNotNone(applied.change_id)

    def test_an_undo_puts_both_back(self):
        applied = journal.apply(self.db_path, "move and delete", self.edits())
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual([(self.moved, self.leaving_id), (self.lost, self.leaving_id)], self.faces())

    def test_the_rehearsal_says_the_undo_is_exact(self):
        rehearsal = journal.rehearse(self.db_path, "move and delete", self.edits())
        self.assertTrue(rehearsal.exact, rehearsal.differences)
        self.assertEqual([(self.moved, self.leaving_id), (self.lost, self.leaving_id)], self.faces())


if __name__ == "__main__":
    unittest.main()

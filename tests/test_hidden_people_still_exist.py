"""A person hidden from autocomplete is still somebody the library knows.

TagTuner's /api/people leaves out people hidden from autocomplete, which is right for
the list offered while typing. The page also used that list to ask "does this person
exist?", so assigning a face to a hidden person offered to create them as someone new.
include_hidden=1 answers the second question.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.services.search import PhotoIndex  # noqa: E402
from face_rows import add_face  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db  # noqa: E402


class HiddenPeopleStillExist(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hidden_people_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        conn = db.connect(self.db)
        conn.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face, hidden_from_autocomplete)"
                     " VALUES (1, 'People', 'People', NULL, 1, 0),"
                     " (2, 'People/Rowan Thackeray', 'Rowan Thackeray', 1, 1, 1)")
        conn.execute("INSERT INTO photos (path) VALUES ('D:\\a.jpg')")
        add_face(conn, "D:\\a.jpg", box="[]", name="Rowan Thackeray")
        conn.commit()
        conn.close()
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db))

    def people(self, query=""):
        return self.requests.get("/api/people" + query)

    def test_the_list_offered_while_typing_leaves_them_out(self):
        self.assertEqual([], self.people())

    def test_asking_who_exists_includes_them(self):
        self.assertEqual(["Rowan Thackeray"], self.people("?include_hidden=1"))


if __name__ == "__main__":
    unittest.main()

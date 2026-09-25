"""What TagTuner's read routes answer, called in-process against a scratch library.

The reads moved into tagpup.store in phase 3, and into tagpup.services.identify and
people in phase 5. These pin what they answer, and the two things wrong with them found
on the way (docs/findings.md, #49 and #50).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from index import PhotoIndex  # noqa: E402
from face_rows import add_people  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db  # noqa: E402


def vector(*values):
    v = np.zeros(8, dtype=np.float32)
    v[:len(values)] = values
    return v.tobytes()


class TunerReads(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tuner_reads_")
        self.db_path = os.path.join(self.dir, "library.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        tuner_client.forget(self.db_path)
        self.addCleanup(tuner_client.forget, self.db_path)
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db_path))
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def photo(self, name, people=(), tags=(), mtime=1.0):
        path = os.path.join(self.dir, name)
        self.conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                          " VALUES (?, ?, 1, ?, '[]', '{}')",
                          (path, mtime, json.dumps(list(tags))))
        add_people(self.conn, path, list(people))
        self.conn.commit()
        return path

    def face(self, photo_path, name=None, embedding=None, excluded=0, source=None):
        face_id = self.conn.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, name_source, excluded)"
            " VALUES ((SELECT id FROM photos WHERE path = ?), '[0, 0, 10, 10]', ?, ?, ?, ?)",
            (photo_path, embedding if embedding is not None else vector(1), name, source, excluded)).lastrowid
        self.conn.commit()
        return face_id

    def node(self, tag, has_face=1, hidden=0):
        self.conn.execute("INSERT INTO tag_taxonomy (tag, name, has_face, hidden_from_autocomplete)"
                          " VALUES (?, ?, ?, ?)", (tag, tag.split("/")[-1], has_face, hidden))
        self.conn.commit()


class PersonFaces(TunerReads):
    def person_faces(self, name):
        return self.requests.get("/api/person-faces?name=" + urllib.parse.quote(name))

    def test_unmatched_is_not_a_pseudo_person_that_counts_what_it_cannot_show(self):
        # The list stopped offering an "Unmatched" pseudo-person. The branch that served
        # it counted excluded faces and paged without them (docs/findings.md, #49).
        self.face(self.photo("a.jpg"), excluded=1)
        answer = self.person_faces("Unmatched")
        self.assertEqual((0, [], False), (answer["total_count"], answer["faces"], answer["has_more"]))

    def test_a_person_s_faces_come_with_their_count(self):
        path = self.photo("a.jpg")
        self.face(path, name="Wren Halloway", embedding=vector(1, 0))
        self.face(self.photo("b.jpg"), name="Wren Halloway", embedding=vector(1, 0.1))
        answer = self.person_faces("Wren Halloway")
        self.assertEqual(2, answer["total_count"])
        self.assertEqual(2, len(answer["faces"]))
        self.assertFalse(answer["has_more"])


class TheIdentifyQueue(TunerReads):
    def groups(self):
        return {group["name"] for group in self.requests.get("/api/unmatched-faces/people")}

    def test_follows_the_people_saved_on_its_photos(self):
        # The queue groups nameless faces by the people their photo's row lists. A tag
        # saved in TagPup changes that and no face, and the queue kept its old groups
        # until some face changed (docs/findings.md, #58).
        first = self.photo("a.jpg", people=["Wren Halloway"])
        second = self.photo("b.jpg", people=["Wren Halloway"])
        self.face(first)
        self.face(second)
        self.assertIn("Wren Halloway", self.groups())
        self.conn.execute("DELETE FROM photo_people")
        for path in (first, second):
            add_people(self.conn, path, ["Ansel Ditmore"])
        self.conn.commit()
        groups = self.groups()
        self.assertIn("Ansel Ditmore", groups)
        self.assertNotIn("Wren Halloway", groups)


class PeopleWithCounts(TunerReads):
    def test_reads_the_tree_once_whoever_is_named(self):
        # Each person's nodes were looked up with a query of their own (#50).
        for n, name in enumerate(("Wren Halloway", "Ansel Ditmore", "Cora Ingersoll", "Pell Varga")):
            self.node("People/" + name)
            self.face(self.photo("%d.jpg" % n), name=name)
        statements = []
        connect = db.connect

        def counting(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        db.connect = counting
        try:
            answer = self.requests.get("/api/people-with-counts")
        finally:
            db.connect = connect
        self.assertEqual(4, len(answer))
        tree_reads = [s for s in statements if "FROM tag_taxonomy" in s]
        self.assertLessEqual(len(tree_reads), 2, tree_reads)

    def test_leaves_out_people_whose_every_node_is_hidden(self):
        self.node("People/Wren Halloway", hidden=1)
        self.node("People/Ansel Ditmore")
        self.face(self.photo("a.jpg"), name="Wren Halloway")
        self.face(self.photo("b.jpg"), name="Ansel Ditmore")
        self.face(self.photo("c.jpg"), name="Ansel Ditmore")
        answer = self.requests.get("/api/people-with-counts")
        self.assertEqual([{"name": "Ansel Ditmore", "count": 2}], answer)


if __name__ == "__main__":
    unittest.main()

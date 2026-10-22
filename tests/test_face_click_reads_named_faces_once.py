"""Clicking face cards reads the named faces once, not once per click; and groups them by person once, not once per
click or per face of the strip (tagpup.core.clustering.FaceRows: about 0.34 s against 0.22 s for 40 faces).

Each click on a face card in Identify Faces asks /api/photo-details for the photo, and
that read every named face's embedding from SQLite -- 35,826 rows, half a second on the
real library -- to show one similarity per face. /api/face-matches already used a
matrix built once per state of the faces table; photo-details and both automatch
handlers now use the same one (tagpup.jobs.identify.named_faces).
"""
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.services.search import PhotoIndex  # noqa: E402
from face_rows import add_face  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"
#: The named face's photo: a face is never compared with a face of its own photo (two faces in a photo are two
#: people; clustering.FaceRows.likeness).
FINISH = r"D:\Pictures\Regatta\finish.jpg"


class FaceClickReadsNamedFacesOnce(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="named_once_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        emb = lambda v: np.asarray(v, dtype=np.float32).tobytes()  # noqa: E731
        conn = db.connect(self.db)
        for path in (PHOTO, FINISH):
            conn.execute("INSERT INTO photos (path, tags, captions) VALUES (?, '[]', '[]')", (path,))
        add_face(conn, FINISH, box="[1,2,3,4]", embedding=emb([1, 0, 0, 0]), name="Rowan Thackeray")
        self.unnamed = add_face(conn, PHOTO, box="[5,6,7,8]", embedding=emb([0.8, 0.6, 0, 0]))
        conn.commit()
        conn.close()
        tuner_client.forget(self.db)
        self.addCleanup(tuner_client.forget, self.db)
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db))

    def test_two_clicks_read_the_named_faces_once(self):
        statements = []
        real_connect = db.connect

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with mock.patch.object(db, "connect", side_effect=connect):
            for _ in range(2):
                reply = self.requests.get("/api/photo-details?path=" + urllib.parse.quote(PHOTO))

        named_reads = [s for s in statements if "embedding" in s and "tag_id IS NOT NULL" in s]
        self.assertEqual(1, len(named_reads), "every click re-read the named faces:\n" + "\n".join(named_reads))
        self.assertEqual([0.8], [round(f["max_similarity"], 4) for f in reply["faces"]])

    def test_a_face_card_shows_how_like_someone_named_it_is_never_by_itself_or_a_face_of_its_photo(self):
        """The owner clicks a photo holding a named face beside the unnamed one. The number beside each face was a raw
        maximum over every named face: the named face showed 1.0, its own, and the unnamed one its likeness to the face
        beside it, someone else (two faces in a photo are two people). It is asked of the one gallery now
        (clustering.FaceRows.likeness), as the boxes and the strip are."""
        conn = db.connect(self.db)
        named = add_face(conn, PHOTO, box="[9,9,12,12]", embedding=np.asarray([0.6, 0.8, 0, 0], dtype=np.float32).tobytes(),
                         name="Wren Halloway")
        conn.commit()
        conn.close()
        reply = self.requests.get("/api/photo-details?path=" + urllib.parse.quote(PHOTO))
        shown = {face["id"]: round(face["max_similarity"], 4) for face in reply["faces"]}
        # Each is compared with Rowan alone, the one named face of another photo: 0.8 and 0.6, not 0.96 and 1.0.
        self.assertEqual({self.unnamed: 0.8, named: 0.6}, shown)

    def test_the_named_faces_are_grouped_by_person_once_for_every_click(self):
        from tagpup.core import clustering
        made = []
        real = clustering.FacesByPerson.__init__

        def grouping(self, people, years=None):
            made.append(len(people))
            real(self, people, years)
        with mock.patch.object(clustering.FacesByPerson, "__init__", grouping):
            for _ in range(3):
                found = self.requests.get("/api/face-matches?id=%d" % self.unnamed)
        self.assertEqual([1], made, "the named faces were grouped by person again for a click")
        self.assertEqual(["Rowan Thackeray"], [each["name"] for each in found])

    def test_the_named_faces_are_kept_once_in_their_peoples_order(self):
        """Two people's faces read in the order of the rows, not of the people: the matrix kept is put in the people's order,
        and the grouping reads that matrix itself, not a second copy (about 110 MB on photo_index)."""
        from tagpup.core import clustering
        from tagpup.core import validation
        from tagpup.core.vocabulary import Ref
        matrix = np.eye(4, dtype=np.float32)
        rows = clustering.FaceRows([11, 12, 13, 14], [Ref(2, "Wren Halloway"), Ref(1, "Rowan Thackeray"),
                                                      Ref(2, "Wren Halloway"), Ref(1, "Rowan Thackeray")], matrix,
                                       validation.MTCNN_FACENET)
        ids, people, kept = rows
        self.assertEqual([12, 14, 11, 13], ids)
        self.assertEqual([1, 1, 2, 2], [person.id for person in people])
        self.assertEqual([1.0, 1.0], [kept[0, 1], kept[1, 3]])   # each row is still its face's vector
        self.assertIs(kept, rows.by_person[1], "the grouping keeps a second copy of the matrix")


if __name__ == "__main__":
    unittest.main()

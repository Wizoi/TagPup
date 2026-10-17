"""The queries behind Identify Faces, and what they are allowed to touch (the grid cache is scenario P7:
tests/test_identify_ignore_assign_restore_undo.py).

This screen is the one that meets a real library head on: 225,000 face rows, 189,000 of
them still nameless, and every button on it ran work proportional to all of them. The
tests here pin the three things that made it slow, each of which is also wrong for a
reason that has nothing to do with speed:

* the faces table had no index the identify queries could use, so counting the
  excluded bucket read every row;
* the queue read all 189,000 embeddings -- 380 MB of BLOB -- to produce a list of
  counts that never looks at a vector;
* matching fell back to `photo_path LIKE ?`, which is not a full-table scan by
  accident but by definition, and which silently treats `_` in a filename as a
  wildcard. Every camera on earth writes `IMG_1234.jpg`;
* clicking a card re-read all 35,758 named embeddings and rebuilt a 73 MB matrix to
  ask who the face resembles, on every click.

The third is a correctness test wearing a performance test's clothes: a LIKE lookup on
a path does not mean "this photo". The fourth brought a shared matrix with it, and the
tests below pin what sharing it must not change: a face is not a suggestion for
itself, and naming somebody has to be visible in the next answer.
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.services.search import PhotoIndex
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_exiftool  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import tuner_client  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.store import people as store_people  # noqa: E402
from tagpup.store import db  # noqa: E402


class TestFacesTableIsIndexedForIdentifying(unittest.TestCase):
    """The identify screen filters on `excluded` and `name`. An index has to cover it.

    Without one, `SELECT COUNT(*) FROM faces WHERE excluded = 1` -- which the sidebar
    asks for on every load -- scans 225,000 rows carrying a 2 KB embedding and a 6 KB
    JPEG crop apiece. Measured on the real library: 0.37s for a number that is almost
    always zero, and the queue asks four such questions before it draws anything.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_idx_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db = os.path.join(self.tmpdir, "photo_index.db")

    def indexes_on_faces(self, db_path):
        conn = db.connect(db_path)
        try:
            names = [r[1] for r in conn.execute("PRAGMA index_list(faces)")]
            return {
                n: [c[2] for c in conn.execute("PRAGMA index_info(%s)" % n)]
                for n in names
            }
        finally:
            conn.close()

    def test_a_new_database_is_created_with_the_identify_index(self):
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        indexes = self.indexes_on_faces(self.db)
        self.assertIn(
            "idx_faces_identify", indexes,
            "faces has no index covering the identify filter; every count scans the table",
        )
        self.assertEqual(
            ["excluded", "name"], indexes["idx_faces_identify"],
            "the index has to lead with `excluded` so a covering count is possible",
        )

    def test_the_excluded_count_is_answered_from_the_index_alone(self):
        """A covering index means the count never reaches the row, crop and all."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = db.connect(self.db)
        try:
            plan = " ".join(
                r[3] for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM faces WHERE excluded = 1")
            )
        finally:
            conn.close()
        self.assertIn("idx_faces_identify", plan, "plan was: %s" % plan)
        self.assertIn(
            "COVERING", plan.upper(),
            "the count still reaches the table rows; plan was: %s" % plan,
        )


def unit_vector(seed):
    """A normalised 512-dim embedding, the shape the face model produces."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


class MatchingTestBase(unittest.TestCase):
    """The TagTuner app over a throwaway library, through Flask's test client
    (tests/tuner_client.py)."""

    DB_NAME = "test_identify_hot_paths.db"   # in a home of the class's own

    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls)
        cls.TEST_DB = cls.home.library(cls.DB_NAME)
        pi = PhotoIndex(db_path=cls.TEST_DB)
        pi.load()
        pi.close()
        cls.app = tuner_client.app_on(cls.TEST_DB)

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_hot_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        # Naming a face puts the person on its photo (#861): ExifTool is a table of files, the photos here rows.
        self.files = fake_exiftool.standing_in(self)
        conn = db.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()
        # The identify cache lives as long as the process and outlives the rows it
        # describes, so a payload left by the last test can be served to this one.
        tuner_client.forget(self.TEST_DB)
        self.requests = tuner_client.Requests(self.app)

    def post(self, path, body):
        return self.requests.post(path, body)

    def add_photo(self, name, people=()):
        """A photo whose keywords name `people`, its people rebuilt from them."""
        # As the indexer writes it: absolute, native separators.
        path = os.path.abspath(os.path.join(self.tmpdir, name))
        conn = db.connect(self.TEST_DB)
        photo_rows.add_read(conn, path, {"XMP:Subject": ["People/" + person for person in people]})
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo, seed, name=None):
        conn = db.connect(self.TEST_DB)
        fid = add_face(conn, photo, box="[0,0,10,10]", embedding=unit_vector(seed).tobytes(),
                       name=name, prob=0.99)
        store_people.rebuild_photos(conn, [photo])   # as the store does, inserting a named face
        conn.commit()
        conn.close()
        return fid

    def name_of(self, face_id):
        conn = db.connect(self.TEST_DB)
        try:
            row = conn.execute(
                "SELECT name FROM faces WHERE id = ?", (face_id,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None


class TestTheQueueDoesNotReadEmbeddings(MatchingTestBase):
    """Counting who is waiting does not require anybody's face vector.

    The queue endpoint selected `f.embedding` for all 189,000 nameless faces and
    normalised every one of them -- 380 MB of BLOB read and 189,000 numpy allocations
    -- then used the result for nothing but a non-empty test. It groups faces by the
    tags their photo carries and counts them; no vector is ever compared to anything.

    Tested by giving a face an embedding that is real bytes but not a vector. Code
    that only asks whether an embedding is there is unbothered; code that parses every
    one of them cannot get past it. That is the distinction worth pinning, and it is
    also a robustness property in its own right -- one damaged row should not take the
    whole queue down.
    """

    def unreadable_embedding_face(self, photo):
        conn = db.connect(self.TEST_DB)
        # Not a whole number of float32s.
        fid = add_face(conn, photo, box="[0,0,10,10]", embedding=b"\x01\x02\x03", prob=0.99)
        conn.commit()
        conn.close()
        return fid

    def queue(self):
        return self.requests.get("/api/unmatched-faces/people")

    def test_the_queue_opens_when_a_face_has_an_unreadable_embedding(self):
        photo = self.add_photo("IMG_4242.jpg")
        self.add_face(photo, seed=11)
        self.unreadable_embedding_face(photo)

        buckets = self.queue()
        self.assertTrue(
            any(b["name"] == "Unknown Faces" for b in buckets),
            "the queue did not come back with its Unknown Faces bucket: %s" % (buckets,),
        )

    def test_a_face_with_no_embedding_is_not_counted_as_waiting(self):
        """Not a performance point: a face with no vector cannot be identified."""
        photo = self.add_photo("IMG_4343.jpg")
        self.add_face(photo, seed=12)

        conn = db.connect(self.TEST_DB)
        add_face(conn, photo, box="[0,0,10,10]", embedding=None, prob=0.99)
        conn.commit()
        conn.close()

        unknown = [b for b in self.queue() if b["name"] == "Unknown Faces"]
        self.assertEqual(1, len(unknown), "no Unknown Faces bucket came back")
        self.assertEqual(
            1, unknown[0]["count"],
            "a face with no embedding was counted as waiting to be identified",
        )


class TestAnUnderscoreInAFilenameIsNotAWildcard(MatchingTestBase):
    """`photo_path LIKE ?` does not mean "this photo", and the difference bites.

    Matching looked a photo up with `=` and then, whenever that found nothing -- which
    is the ordinary case, because finding nothing is what "no conflict here" looks like
    -- asked the same question again with LIKE. Two consequences, and the second is the
    one that matters:

    1. LIKE cannot use the index on photo_path, so every assignment scanned all 225,000
       face rows. Measured at 0.38s per face: assigning a selection of fifty spent
       nineteen seconds deciding there was no conflict.
    2. In LIKE, `_` matches any single character. `IMG_1234.jpg` matches `IMG-1234.jpg`
       and `IMGX1234.jpg`. So assigning a name in one photo could be refused because a
       *different* photo, whose name differs only where the underscore sits, already had
       that person on it.

    Every camera names files with an underscore, so this is not a corner case.
    """

    def test_assigning_a_name_is_not_refused_because_of_a_lookalike_filename(self):
        mine = self.add_photo("IMG_1234.jpg")
        other = self.add_photo("IMG-1234.jpg")

        # Somebody is already named in the *other* photo, the one whose filename
        # differs from ours only where the underscore is.
        self.add_face(other, seed=1, name="Rowan Thackeray")
        candidate = self.add_face(mine, seed=2)

        status, body = self.post(
            "/api/face/match",
            {"face_id": candidate, "person_name": "Rowan Thackeray"},
        )
        self.assertEqual(
            200, status,
            "assignment was refused over a different photo's filename: %s" % (body,),
        )
        self.assertEqual("Rowan Thackeray", self.name_of(candidate))

    def test_bulk_assigning_is_not_refused_because_of_a_lookalike_filename(self):
        mine = self.add_photo("DSC_0007.jpg")
        other = self.add_photo("DSCX0007.jpg")

        self.add_face(other, seed=3, name="Marisol Vane")
        candidate = self.add_face(mine, seed=4)

        status, body = self.post(
            "/api/faces/match-bulk",
            {"face_ids": [candidate], "person_name": "Marisol Vane"},
        )
        self.assertEqual(
            200, status,
            "bulk assignment was refused over a different photo's filename: %s" % (body,),
        )
        self.assertEqual("Marisol Vane", self.name_of(candidate))

    def test_a_real_conflict_in_the_same_photo_is_still_refused(self):
        """Removing the wildcard must not remove the check it was doing badly."""
        photo = self.add_photo("IMG_9000.jpg")
        self.add_face(photo, seed=5, name="Teodor Blaskovic")
        candidate = self.add_face(photo, seed=6)

        status, _body = self.post(
            "/api/face/match",
            {"face_id": candidate, "person_name": "Teodor Blaskovic"},
        )
        self.assertEqual(
            400, status,
            "two faces in one photo were both assigned to one person",
        )
        self.assertIsNone(self.name_of(candidate))


class TestTheSharedNamedFaceMatrix(MatchingTestBase):
    """Every named face, built once per state of the faces table instead of per click.

    Selecting a card asks `/api/face-matches` who it resembles. That read all 35,758
    named embeddings back out of SQLite and built a 73 MB matrix to answer it -- half
    a second, every click, for a matrix identical to the one the last click built.

    Sharing it costs two properties that the per-request version got for free, because
    it excluded the face being asked about from its own query. Both are pinned here.
    """

    def matches_for(self, face_id):
        return self.requests.get("/api/face-matches?id=%d" % face_id)

    def test_a_face_is_not_a_suggestion_for_itself(self):
        """Otherwise every named face resembles itself at 100%, which says nothing."""
        photo = self.add_photo("IMG_5150.jpg")
        named = self.add_face(photo, seed=21, name="Ines Okonkwo")

        self.assertEqual(
            [], self.matches_for(named),
            "the only named face in the library was offered as its own match",
        )

    def test_another_face_of_the_same_person_is_still_offered(self):
        """Skipping the face itself must not skip the person."""
        first = self.add_photo("IMG_5151.jpg")
        second = self.add_photo("IMG_5152.jpg")
        vector = unit_vector(22)

        conn = db.connect(self.TEST_DB)
        for photo in (first, second):
            add_face(conn, photo, box="[0,0,10,10]", embedding=vector.tobytes(), name="Ines Okonkwo",
                     prob=0.99)
        conn.commit()
        face_id = conn.execute(
            "SELECT id FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (first,)).fetchone()[0]
        conn.close()

        names = [m["name"] for m in self.matches_for(face_id)]
        self.assertEqual(
            ["Ines Okonkwo"], names,
            "the person's other face was lost along with the face itself",
        )

    def test_naming_somebody_shows_up_in_the_next_answer(self):
        """A shared matrix is only safe if naming a face rebuilds it."""
        photo = self.add_photo("IMG_5153.jpg")
        asking = self.add_face(photo, seed=23)

        self.assertEqual(
            [], self.matches_for(asking), "nobody is named yet, so nothing can match")

        elsewhere = self.add_photo("IMG_5154.jpg")
        newly_named = self.add_face(elsewhere, seed=24)
        status, _body = self.post(
            "/api/face/match",
            {"face_id": newly_named, "person_name": "Halvard Nilsen"},
        )
        self.assertEqual(200, status)

        names = [m["name"] for m in self.matches_for(asking)]
        self.assertIn(
            "Halvard Nilsen", names,
            "the shared matrix was served stale after a face was named",
        )


if __name__ == "__main__":
    unittest.main()

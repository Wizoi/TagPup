"""The data paths behind Identify Faces, and what they are allowed to touch.

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
  wildcard. Every camera on earth writes `IMG_1234.jpg`.

The last is a correctness test wearing a performance test's clothes: a LIKE lookup on
a path does not mean "this photo".
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex
from tuner_server import start_server as start_tuner_server, set_active_db_path


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
        conn = sqlite3.connect(db_path)
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

    def test_an_existing_database_gains_the_index_on_open(self):
        """The libraries that need this most are the ones that already exist."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
        conn.execute("DROP INDEX IF EXISTS idx_faces_identify")
        conn.commit()
        conn.close()
        self.assertNotIn("idx_faces_identify", self.indexes_on_faces(self.db))

        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()
        self.assertIn(
            "idx_faces_identify", self.indexes_on_faces(self.db),
            "opening an older database did not add the index it is missing",
        )

    def test_the_excluded_count_is_answered_from_the_index_alone(self):
        """A covering index means the count never reaches the row, crop and all."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
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
    """A running tuner server over a throwaway database."""

    TEST_PORT = 9094
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_identify_hot_paths.db")

    @classmethod
    def setUpClass(cls):
        pi = PhotoIndex(db_path=cls.TEST_DB)
        pi.load()
        pi.close()
        cls.server_thread = threading.Thread(
            target=start_tuner_server,
            kwargs={
                "port": cls.TEST_PORT,
                "db_path": cls.TEST_DB,
                "gui_dir": os.path.join(WORKSPACE_DIR, "gui"),
            },
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        set_active_db_path(None)
        for p in (cls.TEST_DB, cls.TEST_DB.replace(".db", "_taxonomy.json")):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_hot_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()

    def post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.TEST_PORT, path),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def add_photo(self, name, people=()):
        path = os.path.join(self.tmpdir, name).replace("\\", "/")
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions,"
            " raw_metadata) VALUES (?, 1.0, 1, '[]', ?, '[]', '{}')",
            (path, json.dumps(list(people))),
        )
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo, seed, name=None):
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', ?, ?, 0.99)",
            (photo, unit_vector(seed).tobytes(), name),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def name_of(self, face_id):
        conn = sqlite3.connect(self.TEST_DB)
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
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', ?, NULL, 0.99)",
            (photo, b"\x01\x02\x03"),   # not a whole number of float32s
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def queue(self):
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/unmatched-faces/people" % self.TEST_PORT,
            timeout=30,
        ) as r:
            return json.loads(r.read().decode("utf-8"))

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

        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', NULL, NULL, 0.99)", (photo,))
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


if __name__ == "__main__":
    unittest.main()

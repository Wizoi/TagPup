"""Face exclusion: keeping non-people out of identity work.

Crowd photographs collect passers-by, and MTCNN occasionally returns something that is
not a face at all. Left in the database they cluster, vote in identity resolution, and
drag person centroids around. An earlier attempt at this stored the magic name
'Non Person', which index.py still migrates away on every open, because a name cannot
survive re-clustering. This is the column-based replacement.
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex
from taxonomy import TagTaxonomy
from faces import FaceProcessor
from tuner_server import start_server as start_tuner_server, set_active_db_path
from tests.test_face_clustering_rules import identity_vector, near
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from free_port import free_port  # noqa: E402


class ExclusionTestBase(unittest.TestCase):
    TEST_PORT = free_port()
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_face_exclusion.db")

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
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_excl_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()

    # ---------- helpers ----------

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=30
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def add_photo(self, name, people=()):
        from PIL import Image

        # As the indexer writes it: absolute, native separators.
        path = os.path.abspath(os.path.join(self.tmpdir, name))
        Image.new("RGB", (64, 64), (80, 90, 100)).save(path, "JPEG")
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, 1.0, 1, '[]', ?, '[]', '{}')",
            (path, json.dumps(list(people))),
        )
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo, embedding, name=None, box=(0, 0, 100, 100)):
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, 0.99)",
            (photo, json.dumps(list(box)), embedding.tobytes(), name),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def row(self, face_id):
        conn = sqlite3.connect(self.TEST_DB)
        r = conn.execute(
            "SELECT name, excluded, excluded_reason, name_source FROM faces WHERE id = ?",
            (face_id,),
        ).fetchone()
        conn.close()
        return {"name": r[0], "excluded": r[1], "reason": r[2], "source": r[3]}

    def photo_people(self, photo):
        conn = sqlite3.connect(self.TEST_DB)
        r = conn.execute("SELECT people FROM photos WHERE path = ?", (photo,)).fetchone()
        conn.close()
        return json.loads(r[0]) if r and r[0] else []


class TestExcludeEndpoint(ExclusionTestBase):
    def test_excludes_a_face(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))

        status, body = self.post("/api/faces/exclude", {"face_ids": [face]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.row(face)["excluded"], 1)

    def test_records_a_reason(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face], "reason": "bad crop"})
        self.assertEqual(self.row(face)["reason"], "bad crop")

    def test_excluding_clears_any_name_it_carried(self):
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        face = self.add_face(photo, identity_vector(1), name="Jane Doe")

        self.post("/api/faces/exclude", {"face_ids": [face]})
        self.assertIsNone(self.row(face)["name"])

    def test_the_person_leaves_the_photo_when_no_face_of_theirs_remains(self):
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        face = self.add_face(photo, identity_vector(1), name="Jane Doe")

        self.post("/api/faces/exclude", {"face_ids": [face]})
        self.assertNotIn("Jane Doe", self.photo_people(photo))

    def test_the_person_stays_when_another_face_of_theirs_remains(self):
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        first = self.add_face(photo, identity_vector(1), name="Jane Doe")
        self.add_face(photo, identity_vector(2), name="Jane Doe", box=(200, 0, 300, 100))

        self.post("/api/faces/exclude", {"face_ids": [first]})
        self.assertIn("Jane Doe", self.photo_people(photo))

    def test_excluding_many_at_once(self):
        photo = self.add_photo("crowd.jpg")
        ids = [self.add_face(photo, identity_vector(i), box=(i * 50, 0, i * 50 + 40, 40))
               for i in range(5)]
        status, body = self.post("/api/faces/exclude", {"face_ids": ids})
        self.assertEqual(status, 200, body)
        self.assertTrue(all(self.row(i)["excluded"] == 1 for i in ids))

    def test_accepts_a_single_face_id(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        status, _ = self.post("/api/faces/exclude", {"face_id": face})
        self.assertEqual(status, 200)
        self.assertEqual(self.row(face)["excluded"], 1)

    def test_rejects_a_missing_list(self):
        status, _ = self.post("/api/faces/exclude", {})
        self.assertEqual(status, 400)


class TestRestoreEndpoint(ExclusionTestBase):
    def test_restores_an_excluded_face(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face]})

        status, body = self.post("/api/faces/restore", {"face_ids": [face]})
        self.assertEqual(status, 200, body)
        r = self.row(face)
        self.assertEqual(r["excluded"], 0)
        self.assertIsNone(r["reason"])

    def test_a_restored_face_is_unclaimed_so_it_can_be_identified_again(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face]})
        self.post("/api/faces/restore", {"face_ids": [face]})
        self.assertIsNone(self.row(face)["source"], "restored face stayed pinned as manual")


class TestExcludedListing(ExclusionTestBase):
    def test_lists_excluded_faces_with_their_reason(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face], "reason": "stranger"})

        body = self.get("/api/faces/excluded")
        self.assertEqual(body["total_count"], 1)
        self.assertEqual(body["faces"][0]["id"], face)
        self.assertEqual(body["faces"][0]["reason"], "stranger")

    def test_is_empty_when_nothing_is_excluded(self):
        self.add_face(self.add_photo("a.jpg"), identity_vector(1))
        self.assertEqual(self.get("/api/faces/excluded")["total_count"], 0)


class TestExcludedFacesAreInvisibleToIdentityWork(ExclusionTestBase):
    def test_an_excluded_face_is_not_offered_as_a_match(self):
        base = identity_vector(10)
        p1 = self.add_photo("named.jpg", people=["Jane Doe"])
        self.add_face(p1, base, name="Jane Doe")
        p2 = self.add_photo("query.jpg")
        query = self.add_face(p2, near(base, 11))

        before = self.get(f"/api/face-matches?id={query}")
        self.assertTrue(before, "no baseline match")

        conn = sqlite3.connect(self.TEST_DB)
        named_id = conn.execute(
            "SELECT id FROM faces WHERE name = 'Jane Doe'"
        ).fetchone()[0]
        conn.close()
        self.post("/api/faces/exclude", {"face_ids": [named_id]})

        after = self.get(f"/api/face-matches?id={query}")
        self.assertEqual(after, [], "an excluded face was still offered as a match")

    def test_an_excluded_face_does_not_appear_in_the_identify_queue(self):
        base = identity_vector(12)
        ids = []
        for i in range(2):
            p = self.add_photo(f"p{i}.jpg", people=["Jane Doe"])
            ids.append(self.add_face(p, near(base, 20 + i)))

        before = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertIn("Jane Doe", before)

        self.post("/api/faces/exclude", {"face_ids": ids})
        after = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertNotIn("Jane Doe", after, "excluded faces still queued for identifying")

    def test_an_excluded_face_is_not_a_bulk_profile_candidate(self):
        base = identity_vector(13)
        seed = self.add_face(self.add_photo("seed.jpg"), base)
        other = self.add_face(self.add_photo("other.jpg"), near(base, 14))

        offered = self.get(f"/api/face-matches-unmatched?id={seed}")["matches"]
        self.assertIn(other, {m["id"] for m in offered})

        self.post("/api/faces/exclude", {"face_ids": [other]})
        offered = self.get(f"/api/face-matches-unmatched?id={seed}")["matches"]
        self.assertNotIn(other, {m["id"] for m in offered}, "excluded face still offered")


class TestExcludedFacesSurviveClustering(ExclusionTestBase):
    def cluster(self):
        index = PhotoIndex(db_path=self.TEST_DB)
        index.load()
        taxonomy = TagTaxonomy(file_path=os.path.join(self.tmpdir, "tax.json"))
        taxonomy.paths = set()
        try:
            return FaceProcessor.__new__(FaceProcessor).cluster_and_resolve_identities(
                index, taxonomy, max_iterations=3
            )
        finally:
            index.close()

    def test_clustering_never_names_an_excluded_face(self):
        jane = identity_vector(1)
        self.add_face(self.add_photo("anchor.jpg", people=["Jane Doe"]), jane)

        photo = self.add_photo("crowd.jpg", people=["Jane Doe"])
        stranger = self.add_face(photo, near(jane, 40))
        self.post("/api/faces/exclude", {"face_ids": [stranger]})

        self.cluster()
        r = self.row(stranger)
        self.assertIsNone(r["name"], "clustering named an excluded face")
        self.assertEqual(r["excluded"], 1, "clustering cleared the exclusion")

    def test_excluded_faces_do_not_pollute_a_person_centroid(self):
        """The whole point: a stranger must not drag an identity around."""
        jane, stranger_vec = identity_vector(1), identity_vector(50)
        self.add_face(self.add_photo("j1.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("j2.jpg", people=["Jane Doe"]), near(jane, 41))

        bad = self.add_face(self.add_photo("j3.jpg", people=["Jane Doe"]), stranger_vec)
        self.post("/api/faces/exclude", {"face_ids": [bad], "reason": "stranger"})

        stats = self.cluster()
        self.assertGreaterEqual(stats.get("Jane Doe", 0), 1)
        self.assertIsNone(self.row(bad)["name"])

    def test_clustering_still_works_when_everything_is_excluded(self):
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face]})
        self.assertEqual(self.cluster(), {})


if __name__ == "__main__":
    unittest.main()


class TestDetectedFacesArePersisted(ExclusionTestBase):
    """The suggester detects faces as a side effect; that work must be kept."""

    def index(self):
        pi = PhotoIndex(db_path=self.TEST_DB)
        pi.load()
        return pi

    def test_detected_faces_are_recorded_for_an_unknown_photo(self):
        photo = self.add_photo("new.jpg")
        detected = [
            {"box": [0, 0, 50, 50], "embedding": identity_vector(60).tolist(), "prob": 0.99},
            {"box": [60, 0, 110, 50], "embedding": identity_vector(61).tolist(), "prob": 0.98},
        ]
        pi = self.index()
        try:
            self.assertEqual(pi.save_faces_if_absent(photo, detected), 2)
        finally:
            pi.close()

        conn = sqlite3.connect(self.TEST_DB)
        rows = conn.execute(
            "SELECT name, name_source, excluded FROM faces WHERE photo_path = ?", (photo,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r[0] is None for r in rows), "recorded faces arrived pre-named")
        self.assertTrue(all(r[2] == 0 for r in rows))

    def test_a_photo_that_already_has_faces_is_left_alone(self):
        """Never overwrite: existing rows carry manual names and exclusions."""
        photo = self.add_photo("existing.jpg", people=["Jane Doe"])
        kept = self.add_face(photo, identity_vector(62), name="Jane Doe")

        pi = self.index()
        try:
            inserted = pi.save_faces_if_absent(
                photo, [{"box": [0, 0, 9, 9], "embedding": identity_vector(63).tolist(), "prob": 0.9}]
            )
        finally:
            pi.close()

        self.assertEqual(inserted, 0, "existing face rows were added to")
        self.assertEqual(self.row(kept)["name"], "Jane Doe")

        conn = sqlite3.connect(self.TEST_DB)
        count = conn.execute(
            "SELECT COUNT(*) FROM faces WHERE photo_path = ?", (photo,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_an_empty_detection_records_nothing(self):
        pi = self.index()
        try:
            self.assertEqual(pi.save_faces_if_absent(self.add_photo("a.jpg"), []), 0)
        finally:
            pi.close()

    def test_recorded_faces_enter_the_identify_queue(self):
        """The point of persisting: ordinary tagging feeds TagTuner."""
        base = identity_vector(64)
        pi = self.index()
        try:
            for i in range(2):
                photo = self.add_photo(f"q{i}.jpg", people=["Jane Doe"])
                pi.save_faces_if_absent(photo, [{
                    "box": [0, 0, 50, 50],
                    "embedding": near(base, 70 + i).tolist(),
                    "prob": 0.99,
                }])
        finally:
            pi.close()

        names = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertIn("Jane Doe", names, "recorded faces never reached the queue")


class TestExcludedBucketIsReachable(ExclusionTestBase):
    """An exclusion must be reviewable, or it is a one-way door."""

    def test_the_queue_offers_an_excluded_bucket_once_something_is_excluded(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        before = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertNotIn("Excluded", before, "empty bucket was advertised")

        self.post("/api/faces/exclude", {"face_ids": [face]})
        after = {e["name"]: e["count"] for e in self.get("/api/unmatched-faces/people")}
        self.assertIn("Excluded", after, "no way to reach the excluded faces")
        self.assertEqual(after["Excluded"], 1)

    def test_the_bucket_count_tracks_exclusions(self):
        photo = self.add_photo("crowd.jpg")
        ids = [self.add_face(photo, identity_vector(i), box=(i * 60, 0, i * 60 + 50, 50))
               for i in range(3)]
        self.post("/api/faces/exclude", {"face_ids": ids})
        entries = {e["name"]: e["count"] for e in self.get("/api/unmatched-faces/people")}
        self.assertEqual(entries["Excluded"], 3)

        self.post("/api/faces/restore", {"face_ids": ids[:1]})
        entries = {e["name"]: e["count"] for e in self.get("/api/unmatched-faces/people")}
        self.assertEqual(entries["Excluded"], 2)

    def test_the_bucket_disappears_when_everything_is_restored(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face]})
        self.post("/api/faces/restore", {"face_ids": [face]})
        names = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertNotIn("Excluded", names)

    def test_a_restored_face_returns_to_the_identify_queue(self):
        """The round trip has to actually work, not just clear the flag."""
        base = identity_vector(80)
        ids = []
        for i in range(2):
            p = self.add_photo(f"p{i}.jpg", people=["Jane Doe"])
            ids.append(self.add_face(p, near(base, 90 + i)))

        self.post("/api/faces/exclude", {"face_ids": ids})
        self.assertNotIn(
            "Jane Doe", {e["name"] for e in self.get("/api/unmatched-faces/people")}
        )

        self.post("/api/faces/restore", {"face_ids": ids})
        self.assertIn(
            "Jane Doe", {e["name"] for e in self.get("/api/unmatched-faces/people")},
            "restoring did not put the faces back in the queue",
        )


class TestReindexingPreservesFaceCuration(ExclusionTestBase):
    """Re-indexing a photo must not discard the work done on its faces.

    save_faces_batch replaces a photo's face rows wholesale. Those rows carry assigned
    names, manual overrides, exclusions and cached crops; re-detection reproduces none
    of that. This mattered little when only tagged photos were indexed and matters a
    great deal now that every photo is, because far more photos get re-indexed.
    """

    def index(self):
        pi = PhotoIndex(db_path=self.TEST_DB)
        pi.load()
        return pi

    def detection(self, seed):
        return {"box": [0, 0, 50, 50], "embedding": identity_vector(seed).tolist(), "prob": 0.99}

    def test_a_manual_name_survives_reindexing(self):
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        face = self.add_face(photo, identity_vector(1), name="Jane Doe")
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("UPDATE faces SET name_source='manual' WHERE id=?", (face,))
        conn.commit()
        conn.close()

        pi = self.index()
        try:
            pi.save_faces_batch({photo: [self.detection(2)]})
        finally:
            pi.close()

        r = self.row(face)
        self.assertEqual(r["name"], "Jane Doe", "re-indexing discarded a manual name")
        self.assertEqual(r["source"], "manual")

    def test_an_exclusion_survives_reindexing(self):
        photo = self.add_photo("a.jpg")
        face = self.add_face(photo, identity_vector(1))
        self.post("/api/faces/exclude", {"face_ids": [face], "reason": "stranger"})

        pi = self.index()
        try:
            pi.save_faces_batch({photo: [self.detection(2)]})
        finally:
            pi.close()

        r = self.row(face)
        self.assertEqual(r["excluded"], 1, "re-indexing cleared an exclusion")
        self.assertEqual(r["reason"], "stranger")

    def test_a_photo_with_no_faces_yet_is_still_populated(self):
        photo = self.add_photo("new.jpg")
        pi = self.index()
        try:
            pi.save_faces_batch({photo: [self.detection(3), self.detection(4)]})
        finally:
            pi.close()

        conn = sqlite3.connect(self.TEST_DB)
        count = conn.execute(
            "SELECT COUNT(*) FROM faces WHERE photo_path = ?", (photo,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2, "a photo with no faces was skipped")

    def test_overwrite_still_replaces_when_explicitly_asked(self):
        """--force-reembed means redo the work, accepting the loss."""
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        self.add_face(photo, identity_vector(1), name="Jane Doe")

        pi = self.index()
        try:
            pi.save_faces_batch({photo: [self.detection(5)]}, overwrite=True)
        finally:
            pi.close()

        conn = sqlite3.connect(self.TEST_DB)
        rows = conn.execute(
            "SELECT name FROM faces WHERE photo_path = ?", (photo,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][0], "forced re-detection kept a stale name")

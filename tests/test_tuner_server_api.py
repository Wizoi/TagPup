"""Coverage for the TagTuner face-tuning endpoints that `test_stability.py` leaves out.

`test_stability.py` concentrates on matching and clustering rules. The read paths the
UI is actually built on -- photo details, face crops, similarity ranking, the unmatched
queue -- plus the unmatch and person-rename writes had no coverage at all.

Face embeddings here are synthetic unit vectors, chosen so similarity ordering is exact
and assertions can be about *which* identity ranks first rather than about a threshold.
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
import urllib.parse
import urllib.request

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tuner_server import (
    start_server as start_tuner_server,
    set_active_db_path,
    TunerHTTPRequestHandler,
)

FACE_DIM = 512


def unit_vector(seed, dim=FACE_DIM):
    """Deterministic L2-normalised embedding."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).astype(np.float32)


def blend(a, b, weight):
    """A vector that sits `weight` of the way from a to b, renormalised."""
    vec = (1.0 - weight) * a + weight * b
    return (vec / np.linalg.norm(vec)).astype(np.float32)


class TunerAPITestBase(unittest.TestCase):
    TEST_PORT = 9988
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_tuner_api.db")

    @classmethod
    def setUpClass(cls):
        from index import PhotoIndex

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
        for path in (cls.TEST_DB, cls.TEST_DB.replace(".db", "_taxonomy.json")):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagtuner_api_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        # Writes are rejected with 409 while clustering runs, so make sure no earlier
        # test has left a background clustering pass holding the lock.
        self.wait_for_clustering_to_finish()
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()

    def wait_for_clustering_to_finish(self, timeout=30.0):
        set_active_db_path(self.TEST_DB)
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not TunerHTTPRequestHandler.clustering_in_progress:
                    return True
                time.sleep(0.05)
            TunerHTTPRequestHandler.clustering_in_progress = False
            return False
        finally:
            set_active_db_path(None)

    # ---------- helpers ----------

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=60
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def get_raw(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=60
        ) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def make_photo_file(self, filename, size=(120, 90)):
        from PIL import Image

        path = os.path.join(self.tmpdir, filename)
        Image.new("RGB", size, (70, 90, 110)).save(path, "JPEG")
        return path

    def add_photo(self, path, people=(), tags=(), caption=None):
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                os.path.getmtime(path) if os.path.exists(path) else 1.0,
                os.path.getsize(path) if os.path.exists(path) else 1,
                json.dumps(list(tags)),
                json.dumps(list(people)),
                json.dumps([caption] if caption else []),
                json.dumps({}),
            ),
        )
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo_path, embedding, name=None, box=(10, 10, 50, 50), prob=0.99):
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
            (photo_path, json.dumps(list(box)), embedding.tobytes(), name, prob),
        )
        face_id = cur.lastrowid
        conn.commit()
        conn.close()
        return face_id

    def face_name(self, face_id):
        conn = sqlite3.connect(self.TEST_DB)
        row = conn.execute("SELECT name FROM faces WHERE id = ?", (face_id,)).fetchone()
        conn.close()
        return row[0] if row else None

    def photo_people(self, photo_path):
        conn = sqlite3.connect(self.TEST_DB)
        row = conn.execute(
            "SELECT people FROM photos WHERE path = ?", (photo_path,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row and row[0] else []


class TestPhotoDetails(TunerAPITestBase):
    def test_returns_metadata_and_faces(self):
        photo = self.make_photo_file("a.jpg")
        self.add_photo(photo, people=["Jane Doe"], tags=["Trips/Texas"], caption="A trip")
        face_id = self.add_face(photo, unit_vector(1), name="Jane Doe")

        data = self.get(f"/api/photo-details?path={urllib.parse.quote(photo)}")
        self.assertIn("Jane Doe", data.get("people", []))
        self.assertIn("Trips/Texas", data.get("tags", []))
        self.assertEqual(len(data.get("faces", [])), 1)
        self.assertEqual(data["faces"][0]["id"], face_id)

    def test_reports_unmatched_faces_with_null_name(self):
        photo = self.make_photo_file("a.jpg")
        self.add_photo(photo)
        self.add_face(photo, unit_vector(2), name=None)

        data = self.get(f"/api/photo-details?path={urllib.parse.quote(photo)}")
        self.assertEqual(len(data["faces"]), 1)
        self.assertIsNone(data["faces"][0].get("name"))

    def test_rejects_missing_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/photo-details")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_photo_returns_empty_face_list(self):
        ghost = os.path.join(self.tmpdir, "ghost.jpg")
        data = self.get(f"/api/photo-details?path={urllib.parse.quote(ghost)}")
        self.assertEqual(data.get("faces", []), [])


class TestPhotoFile(TunerAPITestBase):
    def test_serves_the_image_bytes(self):
        photo = self.make_photo_file("a.jpg")
        self.add_photo(photo)
        status, content_type, body = self.get_raw(
            f"/api/photo-file?path={urllib.parse.quote(photo)}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("image/"), content_type)
        self.assertGreater(len(body), 0)

    def test_resizes_when_size_is_given(self):
        from PIL import Image
        import io

        photo = self.make_photo_file("a.jpg", size=(400, 300))
        self.add_photo(photo)
        _, _, body = self.get_raw(
            f"/api/photo-file?path={urllib.parse.quote(photo)}&size=100"
        )
        with Image.open(io.BytesIO(body)) as img:
            self.assertLessEqual(max(img.size), 100, "resize parameter was ignored")

    def test_rejects_non_image_extension(self):
        """Path traversal / arbitrary file reads must not be servable as images."""
        txt = os.path.join(self.tmpdir, "secret.txt")
        with open(txt, "w") as f:
            f.write("sensitive")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_raw(f"/api/photo-file?path={urllib.parse.quote(txt)}")
        self.assertIn(ctx.exception.code, (400, 403, 404))

    def test_rejects_missing_file(self):
        ghost = os.path.join(self.tmpdir, "ghost.jpg")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_raw(f"/api/photo-file?path={urllib.parse.quote(ghost)}")
        self.assertEqual(ctx.exception.code, 404)


class TestFaceCrop(TunerAPITestBase):
    def test_crops_the_face_region_from_the_photo(self):
        photo = self.make_photo_file("a.jpg", size=(200, 200))
        self.add_photo(photo)
        face_id = self.add_face(photo, unit_vector(3), box=(10, 20, 60, 90))

        status, content_type, body = self.get_raw(f"/api/face-crop?id={face_id}")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("image/"), content_type)
        self.assertGreater(len(body), 0)

    def test_crop_is_cached_in_the_database(self):
        photo = self.make_photo_file("a.jpg", size=(200, 200))
        self.add_photo(photo)
        face_id = self.add_face(photo, unit_vector(4), box=(10, 20, 60, 90))

        self.get_raw(f"/api/face-crop?id={face_id}")

        conn = sqlite3.connect(self.TEST_DB)
        row = conn.execute(
            "SELECT crop_image FROM faces WHERE id = ?", (face_id,)
        ).fetchone()
        conn.close()
        self.assertTrue(row[0], "crop was not cached back into the faces table")

    def test_rejects_unknown_face(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_raw("/api/face-crop?id=999999")
        self.assertEqual(ctx.exception.code, 404)

    def test_rejects_non_numeric_id(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_raw("/api/face-crop?id=abc")
        self.assertEqual(ctx.exception.code, 400)


class TestFaceMatches(TunerAPITestBase):
    def test_ranks_the_most_similar_identity_first(self):
        jane, bob = unit_vector(10), unit_vector(20)
        p1 = self.add_photo(self.make_photo_file("jane.jpg"), people=["Jane Doe"])
        p2 = self.add_photo(self.make_photo_file("bob.jpg"), people=["Bob Roe"])
        p3 = self.add_photo(self.make_photo_file("query.jpg"))

        self.add_face(p1, jane, name="Jane Doe")
        self.add_face(p2, bob, name="Bob Roe")
        # A query face that is mostly Jane.
        query_id = self.add_face(p3, blend(jane, bob, 0.05), name=None)

        matches = self.get(f"/api/face-matches?id={query_id}")
        self.assertTrue(matches, "no matches returned")
        self.assertEqual(matches[0]["name"], "Jane Doe")
        self.assertGreater(matches[0]["similarity"], matches[-1]["similarity"])

    def test_returns_at_most_five_unique_names(self):
        target = unit_vector(30)
        photo = self.add_photo(self.make_photo_file("q.jpg"))
        for i in range(8):
            p = self.add_photo(self.make_photo_file(f"p{i}.jpg"), people=[f"Person {i}"])
            self.add_face(p, blend(target, unit_vector(100 + i), 0.2), name=f"Person {i}")
        query_id = self.add_face(photo, target, name=None)

        matches = self.get(f"/api/face-matches?id={query_id}")
        self.assertLessEqual(len(matches), 5)
        self.assertEqual(len({m["name"] for m in matches}), len(matches), "duplicate names")

    def test_returns_empty_when_no_resolved_faces_exist(self):
        photo = self.add_photo(self.make_photo_file("q.jpg"))
        query_id = self.add_face(photo, unit_vector(40), name=None)
        self.assertEqual(self.get(f"/api/face-matches?id={query_id}"), [])

    def test_rejects_unknown_face(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/face-matches?id=999999")
        self.assertEqual(ctx.exception.code, 404)


class TestFaceMatchesUnmatched(TunerAPITestBase):
    def test_returns_similar_unmatched_faces_only(self):
        base = unit_vector(50)
        seed_photo = self.add_photo(self.make_photo_file("seed.jpg"))
        near_photo = self.add_photo(self.make_photo_file("near.jpg"))
        far_photo = self.add_photo(self.make_photo_file("far.jpg"))
        named_photo = self.add_photo(self.make_photo_file("named.jpg"), people=["Jane"])

        seed_id = self.add_face(seed_photo, base, name=None)
        near_id = self.add_face(near_photo, blend(base, unit_vector(51), 0.05), name=None)
        self.add_face(far_photo, unit_vector(52), name=None)
        self.add_face(named_photo, blend(base, unit_vector(53), 0.02), name="Jane")

        payload = self.get(f"/api/face-matches-unmatched?id={seed_id}")
        results = payload["matches"]
        ids = {r["id"] for r in results}

        self.assertIn(near_id, ids, "a highly similar unmatched face was not offered")
        self.assertNotIn(seed_id, ids, "the seed face offered itself")

        # Only unmatched faces may be offered for bulk profile creation.
        conn = sqlite3.connect(self.TEST_DB)
        named = {
            r[0]
            for r in conn.execute("SELECT id FROM faces WHERE name IS NOT NULL").fetchall()
        }
        conn.close()
        self.assertFalse(ids & named, "an already-matched face was offered")

        # Everything returned must clear the documented 0.8 similarity floor.
        for r in results:
            self.assertGreaterEqual(r["similarity"], 0.8)

    def test_rejects_unknown_face(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/face-matches-unmatched?id=999999")
        self.assertEqual(ctx.exception.code, 404)


class TestUnmatch(TunerAPITestBase):
    def test_clears_the_face_name(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(60), name="Jane Doe")

        status, body = self.post("/api/face/unmatch", {"face_id": face_id})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.face_name(face_id))

    def test_removes_person_from_photo_when_no_other_face_matches(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(61), name="Jane Doe")

        self.post("/api/face/unmatch", {"face_id": face_id})
        self.assertNotIn("Jane Doe", self.photo_people(photo))

    def test_keeps_person_when_another_face_still_matches(self):
        """Two faces of the same person in one photo: unmatching one must not drop them."""
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        first = self.add_face(photo, unit_vector(62), name="Jane Doe")
        self.add_face(photo, unit_vector(63), name="Jane Doe")

        self.post("/api/face/unmatch", {"face_id": first})
        self.assertIn(
            "Jane Doe",
            self.photo_people(photo),
            "person dropped while another matched face remains",
        )

    def test_unmatching_an_unmatched_face_is_a_noop(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"))
        face_id = self.add_face(photo, unit_vector(64), name=None)
        status, body = self.post("/api/face/unmatch", {"face_id": face_id})
        self.assertEqual(status, 200, body)

    def test_rejects_missing_face_id(self):
        status, _ = self.post("/api/face/unmatch", {})
        self.assertEqual(status, 400)

    def test_rejects_unknown_face_id(self):
        status, _ = self.post("/api/face/unmatch", {"face_id": 999999})
        self.assertEqual(status, 404)


class TestUnmatchBulk(TunerAPITestBase):
    def test_clears_every_listed_face(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane", "Bob"])
        a = self.add_face(photo, unit_vector(70), name="Jane")
        b = self.add_face(photo, unit_vector(71), name="Bob")

        status, body = self.post("/api/faces/unmatch-bulk", {"face_ids": [a, b]})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.face_name(a))
        self.assertIsNone(self.face_name(b))

    def test_leaves_faces_outside_the_list_alone(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane", "Bob"])
        a = self.add_face(photo, unit_vector(72), name="Jane")
        b = self.add_face(photo, unit_vector(73), name="Bob")

        self.post("/api/faces/unmatch-bulk", {"face_ids": [a]})
        self.assertIsNone(self.face_name(a))
        self.assertEqual(self.face_name(b), "Bob")

    def test_rejects_missing_face_ids(self):
        status, _ = self.post("/api/faces/unmatch-bulk", {})
        self.assertEqual(status, 400)


class TestUnmatchAll(TunerAPITestBase):
    def test_clears_every_face_in_the_photo(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane", "Bob"])
        a = self.add_face(photo, unit_vector(80), name="Jane")
        b = self.add_face(photo, unit_vector(81), name="Bob")

        status, body = self.post("/api/photo/unmatch-all", {"photo_path": photo})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.face_name(a))
        self.assertIsNone(self.face_name(b))

    def test_leaves_other_photos_alone(self):
        target = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane"])
        other = self.add_photo(self.make_photo_file("b.jpg"), people=["Bob"])
        a = self.add_face(target, unit_vector(82), name="Jane")
        b = self.add_face(other, unit_vector(83), name="Bob")

        self.post("/api/photo/unmatch-all", {"photo_path": target})
        self.assertIsNone(self.face_name(a))
        self.assertEqual(self.face_name(b), "Bob", "a different photo was modified")

    def test_rejects_missing_photo_path(self):
        status, _ = self.post("/api/photo/unmatch-all", {})
        self.assertEqual(status, 400)


class TestPersonRename(TunerAPITestBase):
    def test_renames_across_faces_and_photo_people(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(90), name="Jane Doe")

        status, body = self.post(
            "/api/person/rename", {"old_name": "Jane Doe", "new_name": "Jane Smith"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.face_name(face_id), "Jane Smith")
        self.assertIn("Jane Smith", self.photo_people(photo))
        self.assertNotIn("Jane Doe", self.photo_people(photo))

    def test_leaves_other_people_untouched(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe", "Bob Roe"])
        self.add_face(photo, unit_vector(91), name="Jane Doe")
        bob = self.add_face(photo, unit_vector(92), name="Bob Roe")

        self.post("/api/person/rename", {"old_name": "Jane Doe", "new_name": "Jane Smith"})
        self.assertEqual(self.face_name(bob), "Bob Roe")
        self.assertIn("Bob Roe", self.photo_people(photo))

    def test_renaming_to_the_same_name_is_a_noop(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(93), name="Jane Doe")
        status, body = self.post(
            "/api/person/rename", {"old_name": "Jane Doe", "new_name": "Jane Doe"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.face_name(face_id), "Jane Doe")

    def test_rejects_the_reserved_unmatched_label(self):
        status, _ = self.post(
            "/api/person/rename", {"old_name": "Unmatched", "new_name": "Somebody"}
        )
        self.assertEqual(status, 400)

    def test_rejects_missing_names(self):
        status, _ = self.post("/api/person/rename", {"old_name": "Jane Doe"})
        self.assertEqual(status, 400)

    def test_the_taxonomy_node_is_renamed_too(self):
        """A rename that leaves the tag tree behind puts the two out of step."""
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, NULL, ?, 1)",
            ("People", "People"),
        )
        people_id = conn.execute("SELECT id FROM tag_taxonomy WHERE tag='People'").fetchone()[0]
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, 1)",
            ("People/Jane Doe", people_id, "Jane Doe"),
        )
        conn.commit()
        conn.close()

        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(200), name="Jane Doe")

        status, body = self.post(
            "/api/person/rename", {"old_name": "Jane Doe", "new_name": "Jane Smith"}
        )
        self.assertEqual(status, 200, body)

        conn = sqlite3.connect(self.TEST_DB)
        tags = {r[0] for r in conn.execute("SELECT tag FROM tag_taxonomy").fetchall()}
        names = {r[0] for r in conn.execute("SELECT name FROM tag_taxonomy").fetchall()}
        conn.close()
        self.assertIn("People/Jane Smith", tags, "the taxonomy kept the old path")
        self.assertNotIn("People/Jane Doe", tags)
        self.assertIn("Jane Smith", names)

    def test_a_non_person_taxonomy_node_is_left_alone(self):
        """Only face categories are people; a keyword that happens to match is not."""
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, NULL, ?, 0)",
            ("Jane Doe", "Jane Doe"),
        )
        conn.commit()
        conn.close()

        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(201), name="Jane Doe")
        self.post("/api/person/rename", {"old_name": "Jane Doe", "new_name": "Jane Smith"})

        conn = sqlite3.connect(self.TEST_DB)
        tags = {r[0] for r in conn.execute("SELECT tag FROM tag_taxonomy").fetchall()}
        conn.close()
        self.assertIn("Jane Doe", tags, "a non-person keyword tag was renamed")


class TestUnmatchedFacesQueue(TunerAPITestBase):
    def test_people_queue_lists_names_with_a_cluster_of_unmatched_faces(self):
        """The queue needs a DBSCAN cluster, so a name needs two similar faces."""
        base = unit_vector(95)
        for i in range(2):
            photo = self.add_photo(
                self.make_photo_file(f"jane{i}.jpg"), people=["Jane Doe"]
            )
            self.add_face(photo, blend(base, unit_vector(200 + i), 0.02), name=None)

        entries = self.get("/api/unmatched-faces/people")
        names = {e["name"] for e in entries}
        self.assertIn("Jane Doe", names)

    def test_people_queue_ignores_a_lone_unmatched_face(self):
        """One face cannot form a cluster, so it must not create a queue entry."""
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(96), name=None)
        entries = self.get("/api/unmatched-faces/people")
        self.assertNotIn("Jane Doe", {e["name"] for e in entries})

    def test_people_queue_is_empty_when_everything_is_matched(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(96), name="Jane Doe")
        self.assertEqual(self.get("/api/unmatched-faces/people"), [])

    def test_person_matches_requires_a_name(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/unmatched-faces/person-matches")
        self.assertEqual(ctx.exception.code, 400)

    def test_person_matches_returns_unmatched_candidates_for_a_name(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(97), name=None)

        result = self.get(
            f"/api/unmatched-faces/person-matches?name={urllib.parse.quote('Jane Doe')}"
        )
        self.assertIn("faces", result)
        self.assertIsInstance(result["faces"], list)


class TestPhotosListing(TunerAPITestBase):
    def test_lists_photos_that_still_have_unmatched_faces(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(110), name=None)

        rows = self.get("/api/photos?mode=folder-match")
        paths = {r["path"] for r in rows}
        self.assertIn(photo, paths)
        row = next(r for r in rows if r["path"] == photo)
        self.assertEqual(row["unmatched_count"], 1)

    def test_fully_matched_photos_are_excluded(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(111), name="Jane Doe")

        rows = self.get("/api/photos?mode=folder-match")
        self.assertNotIn(photo, {r["path"] for r in rows})

    def test_show_matched_parameter_is_currently_ignored(self):
        """Pins a known limitation: the UI's toggle has no effect on the response.

        The endpoint's query excludes fully-matched photos unconditionally, so passing
        show_matched=true returns the same rows as show_matched=false. If this endpoint
        ever learns to honour the parameter, this test should fail and be replaced by one
        asserting that matched photos ARE returned -- and the Matched Photos Toggle note
        in SPEC_TAGTUNER.md should be removed at the same time.
        """
        matched = self.add_photo(self.make_photo_file("matched.jpg"), people=["Jane Doe"])
        self.add_face(matched, unit_vector(112), name="Jane Doe")
        unmatched = self.add_photo(self.make_photo_file("unmatched.jpg"))
        self.add_face(unmatched, unit_vector(113), name=None)

        with_flag = self.get("/api/photos?mode=folder-match&show_matched=true")
        without_flag = self.get("/api/photos?mode=folder-match&show_matched=false")

        self.assertEqual(
            {r["path"] for r in with_flag},
            {r["path"] for r in without_flag},
            "show_matched now changes the response -- update this test and the spec",
        )
        self.assertNotIn(matched, {r["path"] for r in with_flag})


class TestIdentifyFacesQueue(TunerAPITestBase):
    """The queue that groups nameless faces by the name their photo's tags suggest."""

    def test_review_people_no_longer_lists_an_unmatched_pseudo_person(self):
        """Nameless faces belong to the Identify queue, not the people audit list."""
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(300), name="Jane Doe")
        self.add_face(photo, unit_vector(301), name=None)

        names = {e["name"] for e in self.get("/api/people-with-counts")}
        self.assertIn("Jane Doe", names)
        self.assertNotIn("Unmatched", names, "the flat dump is back in Review People")

    def test_a_lone_candidate_surfaces_under_ungrouped(self):
        """A name with a single unmatched candidate cannot form a group of its own."""
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Solo Person"])
        self.add_face(photo, unit_vector(302), name=None)

        entries = {e["name"]: e["count"] for e in self.get("/api/unmatched-faces/people")}
        self.assertIn("Ungrouped", entries, "a lone candidate went missing entirely")
        self.assertNotIn("Solo Person", entries, "a single candidate formed a group")

    def test_the_ungrouped_count_matches_what_the_view_opens(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Solo Person"])
        self.add_face(photo, unit_vector(303), name=None)

        entries = {e["name"]: e["count"] for e in self.get("/api/unmatched-faces/people")}
        opened = self.get("/api/unmatched-faces/person-matches?name=Ungrouped")
        self.assertEqual(
            entries["Ungrouped"], opened["total_count"],
            "the queue promises a different number of faces than it opens",
        )

    def test_a_face_reachable_under_a_real_group_is_not_also_ungrouped(self):
        """Two tags on one photo, one of which groups: the face is not a stray."""
        base = unit_vector(304)
        for i in range(2):
            p = self.add_photo(self.make_photo_file(f"g{i}.jpg"), people=["Grouped Person"])
            self.add_face(p, blend(base, unit_vector(400 + i), 0.02), name=None)

        photo = self.add_photo(
            self.make_photo_file("mixed.jpg"), people=["Grouped Person", "Solo Person"]
        )
        self.add_face(photo, blend(base, unit_vector(402), 0.02), name=None)

        opened = self.get("/api/unmatched-faces/person-matches?name=Ungrouped")
        paths = {f["photo_path"] for f in opened["faces"]}
        self.assertNotIn(photo, paths, "a face already reachable under a group was duplicated")

    def test_unclustered_faces_are_offered_rather_than_discarded(self):
        """Faces DBSCAN calls noise still need a name, so they must stay reachable."""
        p1 = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        p2 = self.add_photo(self.make_photo_file("b.jpg"), people=["Jane Doe"])
        # Two mutually dissimilar faces: neither forms a cluster with the other.
        self.add_face(p1, unit_vector(310), name=None)
        self.add_face(p2, unit_vector(311), name=None)

        opened = self.get(
            "/api/unmatched-faces/person-matches?name=" + urllib.parse.quote("Jane Doe")
        )
        self.assertEqual(opened["total_count"], 2, "unclustered candidates were dropped")
        self.assertTrue(
            all(f["cluster_id"] == -1 for f in opened["faces"]),
            "expected both faces to be reported as unclustered",
        )

    def test_the_queue_is_served_from_cache_on_repeat_requests(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(320), name=None)

        first = self.get("/api/unmatched-faces/people")
        second = self.get("/api/unmatched-faces/people")
        self.assertEqual(first, second)

    def test_naming_a_face_invalidates_the_cache(self):
        """The cache is keyed on a fingerprint of the faces table, not time."""
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(321), name=None)
        before = {e["name"] for e in self.get("/api/unmatched-faces/people")}

        self.post("/api/face/match", {"face_id": face_id, "person_name": "Jane Doe"})
        after = {e["name"] for e in self.get("/api/unmatched-faces/people")}
        self.assertNotEqual(before, after, "the queue served a stale answer after a match")


class TestRecluster(TunerAPITestBase):
    def test_recluster_starts_in_the_background(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        self.add_face(photo, unit_vector(98), name=None)

        status, body = self.post("/api/faces/recluster", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("success"), body)
        # Do not leave the clustering lock held for whatever test runs next.
        self.assertTrue(
            self.wait_for_clustering_to_finish(),
            "background clustering never released the lock",
        )

    def test_writes_are_rejected_while_clustering_runs(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Jane Doe"])
        face_id = self.add_face(photo, unit_vector(99), name="Jane Doe")

        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.clustering_in_progress = True
        set_active_db_path(None)
        try:
            status, _ = self.post("/api/face/unmatch", {"face_id": face_id})
            self.assertEqual(status, 409)
        finally:
            set_active_db_path(self.TEST_DB)
            TunerHTTPRequestHandler.clustering_in_progress = False
            set_active_db_path(None)


if __name__ == "__main__":
    unittest.main()

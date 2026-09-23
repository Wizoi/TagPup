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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from free_port import free_port  # noqa: E402

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
    TEST_PORT = free_port()
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_tuner_api.db")

    @classmethod
    def setUpClass(cls):
        # Its own port: subclasses inherit the attribute, and a port
        # already held by the last class's server is refused.
        cls.TEST_PORT = free_port()
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
        self.clear_index_status()
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()

    def clear_index_status(self):
        """Forget any indexing job or queue an earlier test left behind.

        index_status and index_queue live on the handler class, so a test that plants
        a "running" job or queues folders leaves them there for every test after it.
        The next test then sees a busy server it never asked for.
        """
        set_active_db_path(self.TEST_DB)
        try:
            TunerHTTPRequestHandler.index_status.clear()
            TunerHTTPRequestHandler.index_queue["pending"] = []
            TunerHTTPRequestHandler.index_queue["runner"] = None
        finally:
            set_active_db_path(None)

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

    def get_with_status(self, path):
        """Like get(), but keeps the status so a refusal can be asserted on."""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=60
            ) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

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


class TestFolderQueue(TunerAPITestBase):
    """Several folders can be asked for at once; they are worked through in turn.

    Indexing is GPU-bound, so folders still run one at a time -- but choosing a
    season of shoots should be one action, not one native dialog per folder with a
    wait beside the machine in between.
    """

    def tearDown(self):
        self.clear_index_status()

    def make_folders(self, n):
        import tempfile
        folders = [tempfile.mkdtemp(prefix="tuner_q%d_" % i) for i in range(n)]
        for folder in folders:
            self.addCleanup(shutil.rmtree, folder, True)
        return folders

    def queue_state(self):
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        set_active_db_path(self.TEST_DB)
        try:
            return list(TunerHTTPRequestHandler.index_queue.get("pending", []))
        finally:
            set_active_db_path(None)

    def post_start(self, folders, block_runner=True):
        """Queue folders, with the worker stubbed out so the queue stays observable."""
        from unittest.mock import patch
        if block_runner:
            with patch("tuner_server.TunerHTTPRequestHandler._ensure_queue_runner"):
                return self.post("/api/folder/index-start", {"folder_paths": folders})
        return self.post("/api/folder/index-start", {"folder_paths": folders})

    def test_several_folders_are_all_queued(self):
        folders = self.make_folders(3)
        status, body = self.post_start(folders)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["queued"]), 3)
        self.assertEqual(len(self.queue_state()), 3)

    def test_the_queue_keeps_the_order_they_were_given_in(self):
        folders = self.make_folders(3)
        self.post_start(folders)
        self.assertEqual([j["folder"] for j in self.queue_state()], folders)

    def test_a_single_folder_path_is_still_accepted(self):
        """The old one-folder shape must keep working."""
        from unittest.mock import patch
        import tempfile
        folder = tempfile.mkdtemp(prefix="tuner_single_")
        with patch("tuner_server.TunerHTTPRequestHandler._ensure_queue_runner"):
            status, body = self.post("/api/folder/index-start", {"folder_path": folder})
        self.assertEqual(status, 200, body)
        self.assertEqual([j["folder"] for j in self.queue_state()], [folder])

    def test_a_folder_asked_for_twice_is_queued_once(self):
        folders = self.make_folders(1) * 2
        self.post_start(folders)
        self.assertEqual(len(self.queue_state()), 1)

    def test_a_folder_already_queued_is_not_added_again(self):
        folders = self.make_folders(1)
        self.post_start(folders)
        status, body = self.post_start(folders)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["queued"], [])
        self.assertEqual(len(body["already_queued"]), 1)
        self.assertEqual(len(self.queue_state()), 1)

    def test_a_folder_being_indexed_now_is_not_queued_behind_itself(self):
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        folders = self.make_folders(1)
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(folders[0])] = {
            "status": "running", "percent": 10, "message": "working",
        }
        set_active_db_path(None)

        status, body = self.post_start(folders)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["queued"], [])
        self.assertEqual(self.queue_state(), [])

    def test_a_folder_that_does_not_exist_is_reported_not_queued(self):
        folders = self.make_folders(1)
        status, body = self.post_start(folders + ["D:/definitely/not/here"])
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["queued"]), 1)
        self.assertEqual(len(body["invalid"]), 1)

    def test_a_request_with_no_valid_folder_at_all_is_refused(self):
        status, _ = self.post_start(["D:/nope", "D:/also/nope"])
        self.assertEqual(status, 400)

    def test_folder_paths_must_be_a_list(self):
        status, _ = self.post("/api/folder/index-start", {"folder_paths": "not a list"})
        self.assertEqual(status, 400)


class TestQueueVisibility(TestFolderQueue):
    """A queue of ten must not look like one folder taking a long time."""

    def test_index_active_lists_what_is_waiting(self):
        folders = self.make_folders(2)
        self.post_start(folders)
        body = self.get("/api/folder/index-active")
        self.assertEqual(len(body["queued"]), 2)
        self.assertTrue(body["busy"])
        self.assertEqual(body["remaining"], 2)

    def test_queued_entries_carry_a_readable_name(self):
        folders = self.make_folders(1)
        self.post_start(folders)
        body = self.get("/api/folder/index-active")
        self.assertEqual(body["queued"][0]["name"], os.path.basename(folders[0]))

    def test_nothing_queued_reads_as_idle(self):
        body = self.get("/api/folder/index-active")
        self.assertFalse(body["busy"])
        self.assertEqual(body["queued"], [])
        self.assertEqual(body["remaining"], 0)

    def test_a_queued_folder_reports_its_own_status_as_queued(self):
        import urllib.parse
        folders = self.make_folders(1)
        self.post_start(folders)
        body = self.get(
            "/api/folder/index-status?path=%s" % urllib.parse.quote(folders[0])
        )
        self.assertEqual(body["status"], "queued")


class TestQueueCancel(TestFolderQueue):
    def test_cancel_all_empties_the_queue(self):
        folders = self.make_folders(3)
        self.post_start(folders)
        status, body = self.post("/api/folder/index-cancel", {"all": True})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["cancelled"]), 3)
        self.assertEqual(self.queue_state(), [])

    def test_cancelling_one_folder_leaves_the_others(self):
        folders = self.make_folders(3)
        self.post_start(folders)
        status, body = self.post(
            "/api/folder/index-cancel", {"folder_paths": [folders[1]]}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual([j["folder"] for j in self.queue_state()],
                         [folders[0], folders[2]])

    def test_cancelling_does_not_touch_the_folder_being_indexed(self):
        """That one owns a subprocess partway through writing rows."""
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        running = self.make_folders(1)[0]
        queued = self.make_folders(1)
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(running)] = {
            "status": "running", "percent": 30, "message": "working",
        }
        set_active_db_path(None)
        self.post_start(queued)

        self.post("/api/folder/index-cancel", {"all": True})
        body = self.get("/api/folder/index-active")
        self.assertEqual(len(body["active"]), 1)
        self.assertEqual(body["queued"], [])

    def test_cancelling_nothing_in_particular_is_refused(self):
        status, _ = self.post("/api/folder/index-cancel", {})
        self.assertEqual(status, 400)


class TestQueueRunner(TestFolderQueue):
    """The worker drains the queue, and one bad folder does not sink the rest."""

    def test_every_queued_folder_is_indexed_in_order(self):
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler
        folders = self.make_folders(3)
        self.post_start(folders)

        seen = []
        with patch.object(TunerHTTPRequestHandler, "run_folder_index_thread",
                          side_effect=lambda f, db, c=False: seen.append(f)):
            TunerHTTPRequestHandler.run_index_queue(self.TEST_DB)
        self.assertEqual(seen, folders)
        self.assertEqual(self.queue_state(), [])

    def test_a_folder_that_throws_does_not_stop_the_queue(self):
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler
        folders = self.make_folders(3)
        self.post_start(folders)

        seen = []

        def flaky(folder, db, cluster=False):
            seen.append(folder)
            if folder == folders[1]:
                raise RuntimeError("that folder is unreadable")

        with patch.object(TunerHTTPRequestHandler, "run_folder_index_thread",
                          side_effect=flaky):
            TunerHTTPRequestHandler.run_index_queue(self.TEST_DB)

        self.assertEqual(seen, folders, "the queue stopped at the failing folder")

    def test_the_failure_is_recorded_against_the_folder_that_failed(self):
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        folders = self.make_folders(2)
        self.post_start(folders)

        def flaky(folder, db, cluster=False):
            if folder == folders[0]:
                raise RuntimeError("unreadable")

        with patch.object(TunerHTTPRequestHandler, "run_folder_index_thread",
                          side_effect=flaky):
            TunerHTTPRequestHandler.run_index_queue(self.TEST_DB)

        set_active_db_path(self.TEST_DB)
        try:
            failed = TunerHTTPRequestHandler.index_status.get(paths.key(folders[0]))
        finally:
            set_active_db_path(None)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("unreadable", failed["message"])

    def test_the_runner_clears_itself_when_the_queue_empties(self):
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        self.post_start(self.make_folders(1))
        with patch.object(TunerHTTPRequestHandler, "run_folder_index_thread"):
            TunerHTTPRequestHandler.run_index_queue(self.TEST_DB)
        set_active_db_path(self.TEST_DB)
        try:
            self.assertIsNone(TunerHTTPRequestHandler.index_queue.get("runner"))
        finally:
            set_active_db_path(None)


class TestSubfolderListing(TunerAPITestBase):
    """Expanding a parent into its children is what lets each be queued on its own."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.parent = tempfile.mkdtemp(prefix="tuner_parent_")
        self.addCleanup(shutil.rmtree, self.parent, True)

    def make_child(self, name, images=0, ext=".jpg"):
        child = os.path.join(self.parent, name)
        os.makedirs(child, exist_ok=True)
        for i in range(images):
            with open(os.path.join(child, "img%d%s" % (i, ext)), "wb") as f:
                f.write(b"x")
        return child

    def list_subfolders(self):
        import urllib.parse
        return self.get(
            "/api/folder/subfolders?path=%s" % urllib.parse.quote(self.parent)
        )

    def test_immediate_subfolders_are_listed(self):
        self.make_child("b_shoot", images=2)
        self.make_child("a_shoot", images=1)
        body = self.list_subfolders()
        self.assertEqual([f["name"] for f in body["folders"]], ["a_shoot", "b_shoot"])

    def test_each_subfolder_reports_how_many_images_it_holds(self):
        self.make_child("shoot", images=3)
        body = self.list_subfolders()
        self.assertEqual(body["folders"][0]["images"], 3)

    def test_images_are_counted_recursively(self):
        child = self.make_child("shoot", images=1)
        deeper = os.path.join(child, "raw")
        os.makedirs(deeper)
        with open(os.path.join(deeper, "extra.jpg"), "wb") as f:
            f.write(b"x")
        body = self.list_subfolders()
        self.assertEqual(body["folders"][0]["images"], 2)
        self.assertTrue(body["folders"][0]["has_subfolders"])

    def test_files_that_are_not_images_are_not_counted(self):
        self.make_child("shoot", images=2, ext=".txt")
        body = self.list_subfolders()
        self.assertEqual(body["folders"][0]["images"], 0)

    def test_images_sitting_in_the_parent_itself_are_reported(self):
        with open(os.path.join(self.parent, "loose.jpg"), "wb") as f:
            f.write(b"x")
        self.make_child("shoot", images=1)
        body = self.list_subfolders()
        self.assertEqual(body["own_images"], 1)

    def test_a_parent_of_only_folders_reports_no_images_of_its_own(self):
        self.make_child("shoot", images=2)
        body = self.list_subfolders()
        self.assertEqual(body["own_images"], 0)
        self.assertTrue(body["has_subfolders"])

    def test_already_indexed_photos_are_counted_per_folder(self):
        """So the picker can show what is already in rather than offering it as new."""
        child = self.make_child("shoot", images=2)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO photos (path, tags, people, captions) VALUES (?, '[]', '[]', '[]')",
            (os.path.join(child, "img0.jpg"),),
        )
        conn.commit()
        conn.close()
        body = self.list_subfolders()
        self.assertEqual(body["folders"][0]["indexed"], 1)

    def test_a_missing_path_is_refused(self):
        status, _ = self.get_with_status("/api/folder/subfolders")
        self.assertEqual(status, 400)

    def test_a_path_that_is_not_a_folder_is_refused(self):
        import urllib.parse
        status, _ = self.get_with_status(
            "/api/folder/subfolders?path=%s" % urllib.parse.quote("D:/definitely/not/here")
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()


class TestTunerFolderIndexing(TunerAPITestBase):
    """TagTuner owns identity work, so it can bring folders in and take them out."""

    def test_status_for_an_unindexed_folder_reads_ready(self):
        import tempfile
        folder = tempfile.mkdtemp(prefix="tuner_idx_")
        body = self.get(
            f"/api/folder/index-status?path={urllib.parse.quote(folder)}"
        )
        self.assertEqual(body["status"], "completed")

    def test_index_start_rejects_a_folder_that_does_not_exist(self):
        status, _ = self.post(
            "/api/folder/index-start", {"folder_path": "D:/definitely/not/here"}
        )
        self.assertEqual(status, 400)

    def test_index_start_launches_a_worker_and_reports_completion(self):
        from unittest.mock import patch, MagicMock
        import tempfile

        folder = tempfile.mkdtemp(prefix="tuner_idx_")
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout.readline.side_effect = ["Indexing photos: 100%\n", ""]

        with patch("subprocess.Popen", return_value=proc) as popen:
            status, body = self.post(
                "/api/folder/index-start", {"folder_path": folder}
            )
            self.assertEqual(status, 200, body)

            q = urllib.parse.quote(folder)
            deadline = time.time() + 20
            final = {}
            while time.time() < deadline:
                final = self.get(f"/api/folder/index-status?path={q}")
                if final.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.1)

        self.assertEqual(final.get("status"), "completed", final)
        self.assertTrue(popen.called, "the indexer subprocess never started")

    def test_indexing_does_not_cluster_unless_asked(self):
        """Clustering rewrites every name in the database, so it is never implicit."""
        from unittest.mock import patch, MagicMock
        import tempfile

        folder = tempfile.mkdtemp(prefix="tuner_idx_")
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout.readline.side_effect = ["done\n", ""]

        with patch("subprocess.Popen", return_value=proc) as popen:
            self.post("/api/folder/index-start", {"folder_path": folder})
            q = urllib.parse.quote(folder)
            deadline = time.time() + 20
            while time.time() < deadline:
                if self.get(f"/api/folder/index-status?path={q}").get("status") != "running":
                    break
                time.sleep(0.1)

        commands = [call.args[0] for call in popen.call_args_list]
        self.assertFalse(
            any("cluster-faces" in c for c in commands),
            f"indexing clustered without being asked: {commands}",
        )


class TestTunerFolderRemoval(TunerAPITestBase):
    def seed_folder(self, name="removeme"):
        import tempfile
        folder = tempfile.mkdtemp(prefix=f"tuner_{name}_")
        photo = os.path.abspath(os.path.join(folder, "a.jpg"))
        from PIL import Image
        Image.new("RGB", (16, 16)).save(photo, "JPEG")

        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, 1.0, 1, '[]', '[]', '[]', '{}')",
            (photo,),
        )
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, name_source) VALUES (?, '[]', ?, ?, 'manual')",
            (photo, unit_vector(500).tobytes(), "Jane Doe"),
        )
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, excluded) VALUES (?, '[]', ?, NULL, 1)",
            (photo, unit_vector(501).tobytes()),
        )
        conn.commit()
        conn.close()
        return folder, photo

    def counts(self):
        conn = sqlite3.connect(self.TEST_DB)
        photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        faces = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        conn.close()
        return photos, faces

    def test_removes_photos_and_their_faces(self):
        folder, _ = self.seed_folder()
        self.assertEqual(self.counts(), (1, 2))

        status, body = self.post("/api/folder/remove", {"folder_path": folder})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["photos_removed"], 1)
        self.assertEqual(body["faces_removed"], 2)
        self.assertEqual(self.counts(), (0, 0))

    def test_reports_the_face_work_that_was_discarded(self):
        """Removal throws away manual names and exclusions; say so rather than not."""
        folder, _ = self.seed_folder()
        _, body = self.post("/api/folder/remove", {"folder_path": folder})
        self.assertEqual(body["manual_lost"], 1)
        self.assertEqual(body["excluded_lost"], 1)

    def test_leaves_the_photo_files_on_disk(self):
        folder, photo = self.seed_folder()
        self.post("/api/folder/remove", {"folder_path": folder})
        self.assertTrue(os.path.exists(photo), "removal deleted the actual photo file")

    def test_leaves_other_folders_alone(self):
        keep_folder, _ = self.seed_folder("keep")
        drop_folder, _ = self.seed_folder("drop")
        self.assertEqual(self.counts(), (2, 4))

        self.post("/api/folder/remove", {"folder_path": drop_folder})
        self.assertEqual(self.counts(), (1, 2), "removal reached outside its folder")

    def test_an_unknown_folder_removes_nothing(self):
        self.seed_folder()
        _, body = self.post("/api/folder/remove", {"folder_path": "D:/nowhere/at/all"})
        self.assertEqual(body["photos_removed"], 0)
        self.assertEqual(self.counts(), (1, 2))

    def test_rejects_a_missing_folder_path(self):
        status, _ = self.post("/api/folder/remove", {})
        self.assertEqual(status, 400)


class TestSingleIndexJob(TunerAPITestBase):
    """Only one index runs at a time, and a page can find out that one is running."""

    def tearDown(self):
        self.clear_index_status()

    def test_nothing_active_when_idle(self):
        body = self.get("/api/folder/index-active")
        self.assertFalse(body["busy"])
        self.assertEqual(body["active"], [])

    def test_a_running_job_is_reported(self):
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        import tempfile

        folder = tempfile.mkdtemp(prefix="tuner_active_")
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(folder)] = {
            "status": "running", "percent": 42, "message": "Generating embeddings: 42% (21/50)",
        }
        set_active_db_path(None)

        body = self.get("/api/folder/index-active")
        self.assertTrue(body["busy"], "a running job was not reported")
        self.assertEqual(body["active"][0]["percent"], 42)
        self.assertIn("42%", body["active"][0]["message"])

    def test_a_second_folder_waits_its_turn_rather_than_being_refused(self):
        """Indexing is GPU-bound: two at once make each other crawl.

        That reason argues for running one at a time, not for turning the second
        request away. It used to answer 409, which meant choosing a season of shoots
        was one native dialog per folder with a wait beside the machine between each.
        The folder is queued instead, and still nothing runs in parallel.
        """
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        import tempfile

        busy_folder = tempfile.mkdtemp(prefix="tuner_busy_")
        other_folder = tempfile.mkdtemp(prefix="tuner_other_")
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(busy_folder)] = {
            "status": "running", "percent": 10, "message": "working",
            "folder": busy_folder,
        }
        set_active_db_path(None)

        with patch("tuner_server.TunerHTTPRequestHandler._ensure_queue_runner"):
            status, body = self.post(
                "/api/folder/index-start", {"folder_path": other_folder}
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["queued"], [other_folder])

        active = self.get("/api/folder/index-active")
        self.assertEqual(len(active["active"]), 1, "two folders were running at once")
        self.assertEqual(active["active"][0]["folder"], busy_folder)
        self.assertEqual([q["name"] for q in active["queued"]],
                         [os.path.basename(other_folder)])

    def test_restarting_the_same_folder_is_still_accepted(self):
        """Asking again for the folder already running is harmless, not an error."""
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        import tempfile

        folder = tempfile.mkdtemp(prefix="tuner_same_")
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(folder)] = {
            "status": "running", "percent": 10, "message": "working",
        }
        set_active_db_path(None)

        status, body = self.post("/api/folder/index-start", {"folder_path": folder})
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("status"), "running")

    def test_a_finished_job_does_not_block_the_next_one(self):
        from tuner_server import TunerHTTPRequestHandler, set_active_db_path
        import paths
        from unittest.mock import patch, MagicMock
        import tempfile

        done_folder = tempfile.mkdtemp(prefix="tuner_done_")
        next_folder = tempfile.mkdtemp(prefix="tuner_next_")
        set_active_db_path(self.TEST_DB)
        TunerHTTPRequestHandler.index_status[paths.key(done_folder)] = {
            "status": "completed", "percent": 100, "message": "done",
        }
        set_active_db_path(None)

        proc = MagicMock()
        proc.returncode = 0
        proc.stdout.readline.side_effect = ["done\n", ""]
        with patch("subprocess.Popen", return_value=proc):
            status, body = self.post("/api/folder/index-start", {"folder_path": next_folder})
        self.assertEqual(status, 200, body)


def forward_slashes(path):
    """A path typed the way a person or a browser tends to: with forward slashes."""
    import pathlib
    return pathlib.PurePath(path).as_posix()


def other_spelling(path):
    """The same file named another way: forward slashes and, where the filesystem
    ignores case, the case turned over."""
    import paths
    spelled = forward_slashes(path)
    return spelled.swapcase() if paths.CASE_INSENSITIVE else spelled


class TestPathsMatchTheRowsTheIndexerWrote(TunerAPITestBase):
    """Every fixture here is seeded as the indexer writes it: absolute and native.

    The lookups used to go through `= ?` and then `LIKE ?`: the equality missed any
    other spelling of the same file, and the LIKE retry read "_" -- which most camera
    filenames contain -- as any character, reaching photos it was never asked about.
    """

    def test_unmatch_all_leaves_a_photo_whose_name_differs_only_at_an_underscore(self):
        target = self.add_photo(self.make_photo_file("IMG_1234.jpg"), people=["Rowan Thackeray"])
        sibling = self.add_photo(self.make_photo_file("IMG-1234.jpg"), people=["Tamsin Okafor"])
        mine = self.add_face(target, unit_vector(900), name="Rowan Thackeray")
        theirs = self.add_face(sibling, unit_vector(901), name="Tamsin Okafor")

        status, body = self.post("/api/photo/unmatch-all", {"photo_path": target})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.face_name(mine))
        self.assertEqual(self.face_name(theirs), "Tamsin Okafor",
                         "unmatching one photo cleared a name in a different photo")
        self.assertEqual(self.photo_people(sibling), ["Tamsin Okafor"])

    def test_unmatch_all_finds_the_photo_whichever_way_it_is_spelled(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Rowan Thackeray"])
        face = self.add_face(photo, unit_vector(902), name="Rowan Thackeray")

        status, body = self.post("/api/photo/unmatch-all", {"photo_path": other_spelling(photo)})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.face_name(face))
        self.assertEqual(self.photo_people(photo), [])

    def test_photo_details_finds_the_faces_whichever_way_the_photo_is_spelled(self):
        photo = self.add_photo(self.make_photo_file("a.jpg"), people=["Rowan Thackeray"])
        self.add_face(photo, unit_vector(903), name="Rowan Thackeray")

        data = self.get("/api/photo-details?path=%s" % urllib.parse.quote(other_spelling(photo)))
        self.assertEqual([f["name"] for f in data["faces"]], ["Rowan Thackeray"])
        self.assertIn("Rowan Thackeray", data["people"])


class TestFolderRemovalBySpelling(TunerAPITestBase):
    def seed(self):
        import tempfile
        folder = tempfile.mkdtemp(prefix="tuner_spelling_")
        self.addCleanup(shutil.rmtree, folder, True)
        os.makedirs(os.path.join(folder, "Day_2"))
        photo = self.add_photo(os.path.join(folder, "Day_2", "IMG_0001.jpg"))
        self.add_face(photo, unit_vector(910), name="Rowan Thackeray")
        return folder, photo

    def count(self, sql, params=()):
        conn = sqlite3.connect(self.TEST_DB)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def test_a_folder_named_in_another_spelling_is_still_removed(self):
        folder, _ = self.seed()
        status, body = self.post("/api/folder/remove", {"folder_path": other_spelling(folder)})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["photos_removed"], body["faces_removed"]), (1, 1))
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces"), 0)

    def test_faces_whose_path_is_spelled_apart_from_their_photo_go_too(self):
        """Faces are matched on their own photo_path, not through the photo rows."""
        import paths
        if not paths.CASE_INSENSITIVE:
            self.skipTest("two spellings of one file need a case-insensitive filesystem")
        folder, photo = self.seed()
        self.add_face(photo.swapcase(), unit_vector(911), name="Tamsin Okafor")

        status, body = self.post("/api/folder/remove", {"folder_path": folder})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces"), 0,
                         "a face of a removed photo was left behind")
        self.assertEqual(body["faces_removed"], 2)

    def test_a_sibling_folder_sharing_the_prefix_is_left_alone(self):
        folder, _ = self.seed()
        sibling = self.add_photo(folder + "_extra" + os.sep + "IMG_0002.jpg")
        self.add_face(sibling, unit_vector(912), name="Tamsin Okafor")

        self.post("/api/folder/remove", {"folder_path": folder})
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces"), 1)


class TestSaveMetadataKeepsTheIndex(TunerAPITestBase):
    """Saving a photo's tags and caption writes them to its row -- and follows a rename.

    The update named a `title` column the photos table does not have, so it failed
    after the file had already been written; and a rename rewrote the photo row with
    the path as given while its faces stayed behind at the old name.
    """

    def save(self, photo, renamed_to=None, title="Harbour at dusk", tags=("Places/Harbour",)):
        from unittest.mock import patch, MagicMock

        def sync_title_to_filename(path, title, executable):
            # What the real one does when the title calls for a new filename.
            if renamed_to:
                os.rename(path, renamed_to)
                return renamed_to
            return path

        with patch("exiftool_session.ExifToolSession", MagicMock()), \
                patch("metadata.sync_title_to_filename", side_effect=sync_title_to_filename):
            return self.post("/api/photo/save-metadata",
                             {"path": photo, "title": title, "tags": list(tags)})

    def row(self, path):
        conn = sqlite3.connect(self.TEST_DB)
        try:
            return conn.execute(
                "SELECT tags, captions FROM photos WHERE path = ?", (path,)).fetchone()
        finally:
            conn.close()

    def faces_at(self, path):
        conn = sqlite3.connect(self.TEST_DB)
        try:
            return [r[0] for r in conn.execute(
                "SELECT name FROM faces WHERE photo_path = ?", (path,))]
        finally:
            conn.close()

    def test_tags_and_caption_reach_the_photo_row(self):
        photo = self.add_photo(self.make_photo_file("IMG_0100.jpg"))
        status, body = self.save(photo)
        self.assertEqual(status, 200, body)
        tags, captions = self.row(photo)
        self.assertEqual(json.loads(tags), ["Places/Harbour"])
        self.assertEqual(json.loads(captions), ["Harbour at dusk"])
        self.assertEqual(body["index_updated"], 1)

    def test_a_rename_moves_the_photo_row_and_its_faces(self):
        photo = self.add_photo(self.make_photo_file("IMG_0101.jpg"))
        self.add_face(photo, unit_vector(920), name="Rowan Thackeray")
        renamed = os.path.join(self.tmpdir, "Harbour at dusk.jpg")

        status, body = self.save(photo, renamed_to=renamed)
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.row(photo), "the old photo row was left behind")
        self.assertIsNotNone(self.row(renamed), "no photo row at the new name")
        self.assertEqual(self.faces_at(renamed), ["Rowan Thackeray"])
        self.assertEqual(self.faces_at(photo), [])
        self.assertEqual(body["new_path"], renamed)


class TestFolderSpellingsReachingTheIndexer(TestFolderQueue):
    """The indexer stores paths in the spelling it is handed, so it is handed one."""

    def test_the_indexer_is_handed_the_stored_spelling_of_a_typed_folder(self):
        from unittest.mock import patch, MagicMock
        from tuner_server import TunerHTTPRequestHandler
        folder = self.make_folders(1)[0]
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout.readline.side_effect = ["done\n", ""]
        with patch("subprocess.Popen", return_value=proc) as popen:
            TunerHTTPRequestHandler.run_folder_index_thread(
                forward_slashes(folder), self.TEST_DB)
        self.assertEqual(popen.call_args_list[0].args[0][3], folder)

    def test_queued_and_running_jobs_name_the_folder_the_same_way(self):
        from unittest.mock import patch
        from tuner_server import TunerHTTPRequestHandler
        folders = self.make_folders(2)
        status, body = self.post_start([forward_slashes(f) for f in folders])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["queued"], folders)

        seen = []

        def look(folder, db, cluster=False):
            seen.append(self.get("/api/folder/index-active"))

        with patch.object(TunerHTTPRequestHandler, "run_folder_index_thread", side_effect=look):
            TunerHTTPRequestHandler.run_index_queue(self.TEST_DB)

        first = seen[0]
        self.assertEqual([a["folder"] for a in first["active"]], [folders[0]])
        self.assertEqual([q["folder"] for q in first["queued"]], [folders[1]])
        self.assertEqual(first["active"][0]["name"], os.path.basename(folders[0]))


class TestSubfoldersOfATypedParent(TestSubfolderListing):
    def test_children_of_a_parent_typed_with_forward_slashes_are_spelled_one_way(self):
        child = self.make_child("shoot", images=1)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO photos (path, tags, people, captions) VALUES (?, '[]', '[]', '[]')",
            (os.path.join(child, "img0.jpg"),),
        )
        conn.commit()
        conn.close()
        body = self.get("/api/folder/subfolders?path=%s"
                        % urllib.parse.quote(other_spelling(self.parent)))
        self.assertEqual(body["folders"][0]["indexed"], 1)
        import paths
        self.assertTrue(paths.same(body["folders"][0]["path"], child))
        self.assertEqual(body["folders"][0]["path"],
                         os.path.join(body["parent"], "shoot"))
        self.assertEqual(body["parent"], os.path.abspath(body["parent"]),
                         "the parent came back with mixed separators")

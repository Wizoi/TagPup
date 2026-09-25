import os
import sys
import json
import sqlite3
import time
import unittest
import numpy as np

# Add workspace and scripts directories to search path
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.services import identities  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_people, add_vector, configured_model, people_of  # noqa: E402
import own_home  # noqa: E402
import tuner_client  # noqa: E402
from handler_harness import Library  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library as PathLibrary  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.web import tuner_routes  # noqa: E402


def native(path):
    """A path as the indexer stores it: absolute, with this platform's separators."""
    return os.path.abspath(path)


def face_names(photo_index):
    """The name of each face resolution reads, in its order."""
    return [row[4] for row in store_faces.for_clustering(photo_index.conn)]


class TestStability(unittest.TestCase):
    """TagTuner's matching rules, through Flask's test client (tests/tuner_client.py)."""

    DB_NAME = "test_validation_index.db"   # in a home of the class's own

    @classmethod
    def setUpClass(cls):
        cls.TEST_DB_PATH = own_home.for_class(cls, "tagpup_stability_").library(cls.DB_NAME)
        cls.app = tuner_client.app_on(cls.TEST_DB_PATH)

    def setUp(self):
        # Setup dummy data in test DB for each test to run in isolation
        if os.path.exists(self.TEST_DB_PATH):
            try:
                os.remove(self.TEST_DB_PATH)
            except Exception:
                pass
                
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        try:
            self._seed(photo_index)
        finally:
            # A failed seed must not leave its write open: the failed test's traceback
            # keeps the connection alive, and every later test then waits out the busy
            # timeout and fails "database is locked" -- 30 seconds apiece.
            photo_index.close()

        # What the server keeps of the library describes the rows the last test made.
        tuner_client.forget(self.TEST_DB_PATH)
        self.requests = tuner_client.Requests(self.app)

    def _seed(self, photo_index):
        # Insert a dummy photo with a valid embedding
        dummy_emb = np.random.rand(512).astype(np.float32)
        dummy_emb_bytes = dummy_emb.tobytes()
        
        cursor = photo_index.conn.cursor()
        
        # Clear any existing rows to prevent unique constraint failures if DB file reuse occurs
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        
        # Insert parent photo
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/test_photo.jpg",
            12345.67,
            98765,
            json.dumps(["Nature", "Forest"]),
            json.dumps(["A beautiful forest"]),
            json.dumps({"Make": "Canon", "Model": "EOS 5D"})
        ))
        add_people(cursor, "C:/photos/test_photo.jpg", ["John Doe"])
        add_vector(photo_index.conn, "C:/photos/test_photo.jpg", dummy_emb_bytes)
        
        # Insert linked face
        dummy_face_emb = np.random.rand(512).astype(np.float32).tobytes()
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/test_photo.jpg",
            json.dumps([10, 20, 50, 60]),
            dummy_face_emb,
            "John Doe",
            0.95
        ))
        
        photo_index.conn.commit()

    def tearDown(self):
        if os.path.exists(self.TEST_DB_PATH):
            try:
                os.remove(self.TEST_DB_PATH)
            except Exception:
                pass

    def test_database_reset_logic(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        
        # Verify metadata is loaded
        self.assertEqual(len(photo_index.metadata), 1, "Photo record should be loaded")
        meta = photo_index.metadata[0]
        self.assertTrue(meta["has_embedding"], "Photo should report having an embedding")
        
        # Verify face record is present
        faces = face_names(photo_index)
        self.assertEqual(len(faces), 1, "Face record should be loaded")
        self.assertEqual(faces[0], "John Doe", "Face name should be preserved")
        
        # Clear CLIP embeddings
        photo_index.clear_clip_embeddings()
        
        # Reload and verify
        photo_index.load()
        meta_after = photo_index.metadata[0]
        self.assertFalse(meta_after["has_embedding"], "Photo embedding should be cleared (None)")
        
        faces_after = face_names(photo_index)
        self.assertEqual(len(faces_after), 1, "Face record should STILL be present")
        self.assertEqual(faces_after[0], "John Doe", "Face name assignment MUST be preserved")
        
        photo_index.close()

    def test_api_unmatched_photos(self):
        data = self.requests.get("/api/photos?mode=unmatched")
        self.assertEqual(len(data), 0, "No unmatched photo should be returned if face has a name")

    def test_api_validation_malformed_json(self):
        status, _ = self.requests.post("/api/face/match", data=b"not-json-format")
        self.assertEqual(status, 400, "Server should reject malformed JSON with 400 Bad Request")

    def test_api_validation_missing_params(self):
        status, _ = self.requests.post("/api/face/match", {"face_id": 1})   # Missing person_name
        self.assertEqual(status, 400, "Server should reject missing parameters with 400 Bad Request")

    def test_api_clustering_busy_lock(self):
        # The library is being clustered, as the indexer's cluster-faces marks it.
        being_clustered = tuner_routes.clustering.of(PathLibrary(self.TEST_DB_PATH))
        being_clustered.set()
        self.addCleanup(being_clustered.clear)

        status, error_body = self.requests.post("/api/face/match", {"face_id": 1, "person_name": "Jane Doe"})
        self.assertEqual(status, 409, "Server should reject writes with 409 Conflict when clustering is active")

        # Verify JSON body in 409 error
        self.assertFalse(error_body["success"])
        self.assertIn("clustering", error_body["error"].lower())

    def test_strict_tag_enforcement_in_clustering(self):
        from tagpup.store.taxonomy import TagTaxonomy
        
        # Open the index
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Clear out setUp dummy data to start fresh for this test
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        
        # We need two face embeddings that are identical (unit vectors) to ensure they cluster together
        face_emb = np.zeros(512, dtype=np.float32)
        face_emb[0] = 1.0
        face_emb_bytes = face_emb.tobytes()
        
        # Photo 1: Tagged with "Alice"
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", 1000.0, 500, json.dumps([]), json.dumps([]), json.dumps({})
        ))
        add_people(cursor, "C:/photos/photo1.jpg", ["Alice"])
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", json.dumps([10, 10, 50, 50]), face_emb_bytes, None, 0.95
        ))
        
        # Photo 2: Untagged
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", 2000.0, 500, json.dumps([]), json.dumps([]), json.dumps({})
        ))
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([10, 10, 50, 50]), face_emb_bytes, None, 0.95
        ))
        
        photo_index.conn.commit()
        photo_index.load()
        
        # Initialize taxonomy file
        taxonomy = TagTaxonomy(self.TEST_DB_PATH)
        taxonomy.load()
        taxonomy.add_tags(["Alice"])
        taxonomy.save()
        
        # Run clustering
        identities.resolve(photo_index, max_iterations=1)
        
        # Query results from faces table
        cursor.execute("SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id")
        resolved_faces = cursor.fetchall()
        
        photo_index.close()
        
        # Assertions:
        # Photo 1 (tagged with "Alice") should have its face resolved to "Alice"
        # Photo 2 (untagged) should remain None
        self.assertEqual(len(resolved_faces), 2, "There should be 2 faces in total")
        
        face1 = next(r for r in resolved_faces if r[0] == "C:/photos/photo1.jpg")
        face2 = next(r for r in resolved_faces if r[0] == "C:/photos/photo2.jpg")
        
        self.assertEqual(face1[1], "Alice", "Face in tagged photo should be resolved to Alice")
        self.assertIsNone(face2[1], "Face in untagged photo must remain None under strict tag enforcement")

    def test_similarity_threshold_in_clustering(self):
        from tagpup.store.taxonomy import TagTaxonomy
        
        # Open the index
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Clear fresh for this test
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        
        # We need three face embeddings
        # Face 1 (Alice anchor): [1.0, 0.0, 0.0, ...]
        emb1 = np.zeros(512, dtype=np.float32)
        emb1[0] = 1.0
        # Face 2 (Unassigned low-similarity face): [0.5, 0.866, 0.0, ...] -> Cos similarity is 0.50 to Alice centroid
        emb2 = np.zeros(512, dtype=np.float32)
        emb2[0] = 0.5
        emb2[1] = 0.866
        # Face 3 (Unassigned very low-similarity face): [0.0, 1.0, 0.0, ...] -> Cos similarity is 0.00 to Alice centroid
        emb3 = np.zeros(512, dtype=np.float32)
        emb3[1] = 1.0
        
        # Photo 1: Single face, tagged with "Alice" (Anchor photo)
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", 1000.0, 500, json.dumps([]), json.dumps([]), json.dumps({})
        ))
        add_people(cursor, "C:/photos/photo1.jpg", ["Alice"])
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", json.dumps([10, 10, 50, 50]), emb1.tobytes(), None, 0.95
        ))
        
        # Photo 2: Two faces, tagged with "Alice", but both faces have similarity < 0.80 to Alice
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", 2000.0, 500, json.dumps([]), json.dumps([]), json.dumps({})
        ))
        add_people(cursor, "C:/photos/photo2.jpg", ["Alice"])
        # Face 2 (in Photo 2)
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([10, 10, 50, 50]), emb2.tobytes(), None, 0.95
        ))
        # Face 3 (in Photo 2)
        cursor.execute("""
            INSERT INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([100, 100, 150, 150]), emb3.tobytes(), None, 0.95
        ))
        
        photo_index.conn.commit()
        photo_index.load()
        
        # Initialize taxonomy
        taxonomy = TagTaxonomy(self.TEST_DB_PATH)
        taxonomy.load()
        taxonomy.add_tags(["Alice"])
        taxonomy.save()
        
        # Run clustering
        identities.resolve(photo_index, max_iterations=1)
        
        # Query results
        cursor.execute("SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id")
        resolved_faces = cursor.fetchall()
        
        photo_index.close()
        # Assertions:
        # Photo 1 (anchor) face resolved to Alice
        # Photo 2 faces (similarities 0.50 and 0.00, below 0.80) should NOT be resolved to Alice
        face1 = next(r for r in resolved_faces if r[0] == "C:/photos/photo1.jpg")
        photo2_faces = [r for r in resolved_faces if r[0] == "C:/photos/photo2.jpg"]
        
        self.assertEqual(face1[1], "Alice", "Photo 1 anchor face should resolve to Alice")
        self.assertEqual(len(photo2_faces), 2, "Photo 2 should have 2 faces")
        
        for name in [r[1] for r in photo2_faces]:
            self.assertIsNone(name, "Faces in Photo 2 must remain unmatched because similarity < 0.80")

    def test_large_lookup_performance(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Batch insert 5000 photos and 5000 faces inside a single transaction
        dummy_emb = np.random.rand(512).astype(np.float32).tobytes()
        dummy_face_emb = np.random.rand(512).astype(np.float32).tobytes()
        
        photo_rows = []
        face_rows = []
        for i in range(5000):
            path = f"C:/photos/photo_{i}.jpg"
            photo_rows.append((
                path,
                123456.0 + i,
                1000 + i,
                json.dumps(["tag"]),
                json.dumps(["caption"]),
                json.dumps({"EXIF:DateTimeOriginal": "2026:06:24 18:00:00", "Make": "Canon"})
            ))
            face_rows.append((
                path,
                json.dumps([10, 20, 50, 60]),
                dummy_face_emb,
                "John Doe",
                0.95
            ))
            
        cursor.execute("BEGIN TRANSACTION")
        cursor.executemany("""
            INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, photo_rows)
        for row in photo_rows:
            add_people(cursor, row[0], ["John Doe"])
        model = configured_model()
        cursor.executemany(
            "INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector)"
            " SELECT id, ?, mtime, size, ? FROM photos WHERE path = ?",
            [(model, dummy_emb, row[0]) for row in photo_rows])
        cursor.executemany("""
            INSERT OR REPLACE INTO faces (photo_id, box, embedding, name, prob)
            VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)
        """, face_rows)
        photo_index.conn.commit()
        photo_index.close()
        
        # Test performance of fetching a large page of 5000 faces from endpoint
        start_time = time.time()
        res_data = self.requests.get("/api/person-faces?name=John%20Doe&limit=5000")
        duration = time.time() - start_time
        
        print(f"\n[PERF TEST] Loading 5000 faces from endpoint took {duration:.4f} seconds.")
        self.assertLess(duration, 5.0)
        self.assertIn("faces", res_data)
        self.assertEqual(len(res_data["faces"]), 5000)

    def test_year_fallback_chain(self):
        from tagpup.core.dates import shown_year
        from tagpup.core import dates

        # 1. The year the library records (photos.year, tagpup.core.dates.photo_year),
        # and TagTuner's "Unknown" for none.
        raw_meta = {"EXIF:DateTimeOriginal": "2005:06:26 12:34:56"}
        self.assertEqual(dates.photo_year(raw_meta, "D:/Training/Pictures/2008/2008-06-26/2008-06-Family.jpg"), 2005)
        self.assertEqual(dates.photo_year({}, "D:/Training/Pictures/2008/family_2004.jpg"), 2004)
        self.assertEqual(dates.photo_year({}, "D:/Training/Pictures/2008/EarthDay/photo.jpg"), 2008)
        self.assertIsNone(dates.photo_year({}, "D:/Training/Pictures/NoYear/photo.jpg"))
        self.assertEqual(shown_year(None), "Unknown")
        self.assertEqual(shown_year(2008), 2008)
        
        # 2. The year of a record (tagpup.core.dates.record_year)
        meta_1 = {"raw_metadata": {"EXIF:DateTimeOriginal": "2005:06:26 12:34:56"}, "path": "D:/2008/photo.jpg"}
        self.assertEqual(dates.record_year(meta_1), 2005)
        
        meta_2 = {"raw_metadata": None, "path": "D:/Training/Pictures/2008/family_2004.jpg"}
        self.assertEqual(dates.record_year(meta_2), 2004)
        
        meta_3 = {"raw_metadata": None, "path": "D:/Training/Pictures/2008/EarthDay/photo.jpg"}
        self.assertEqual(dates.record_year(meta_3), 2008)
        
        meta_4 = {"raw_metadata": None, "path": "D:/Training/Pictures/NoYear/photo.jpg"}
        self.assertIsNone(dates.record_year(meta_4))

    def test_api_photo_automatch_unmatched(self):
        # Open database, insert a resolved face (e.g. John Doe) and an unmatched face (name = None) with similar embedding
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Insert a resolved face
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        # Insert unmatched face with same embedding (similarity = 1.0) but name IS NULL
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/automatch_test.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/john_doe.jpg"), ['John Doe'])
        
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/automatch_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        status, data = self.requests.post("/api/photo/automatch", {"photo_path": "C:/photos/automatch_test.jpg"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["success"])
        self.assertEqual(data["matched_count"], 1)
        
        # Verify the face was successfully resolved to John Doe in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (native("C:/photos/automatch_test.jpg"),))
        name = c.fetchone()[0]
        self.assertEqual(name, "John Doe")
        
        # Also verify photo's people field is updated
        people = people_of(conn, native("C:/photos/automatch_test.jpg"))
        self.assertIn("John Doe", people)
        conn.close()

    def test_api_folder_automatch(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        # Insert photos in the same folder
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo1.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo2.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/john_doe.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/folderA/john_doe.jpg"), ['John Doe'])
        
        # Insert faces
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/folderA/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo1.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo2.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger folder automatch API
        status, data = self.requests.post("/api/folder/automatch", {"folder_path": "C:/photos/folderA"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["success"])
        self.assertEqual(data["matched_count"], 2)
        self.assertIn("remaining_counts", data)
        
        # Verify the faces were successfully resolved to John Doe in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_id IN (SELECT id FROM photos WHERE path IN (?, ?))",
                  (native("C:/photos/folderA/photo1.jpg"), native("C:/photos/folderA/photo2.jpg")))
        names = [r[0] for r in c.fetchall()]
        self.assertEqual(names, ["John Doe", "John Doe"])
        conn.close()

    def test_api_photo_automatch_duplicate_protection(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/john_doe.jpg"), ['John Doe'])
        
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # Insert two unmatched faces on the same photo that both match John Doe
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        status, data = self.requests.post("/api/photo/automatch", {"photo_path": "C:/photos/duplicate_test.jpg"})
        self.assertEqual(status, 200, data)

        # Neither face should be matched (matched_count = 0)
        self.assertEqual(data["matched_count"], 0)
        
        # Verify both faces remain None in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (native("C:/photos/duplicate_test.jpg"),))
        names = [r[0] for r in c.fetchall()]
        self.assertEqual(names, [None, None])
        conn.close()

    def test_api_photo_automatch_already_tagged_protection(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/already_tagged_test.jpg"), ['John Doe'])
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/john_doe.jpg"), ['John Doe'])
        
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # One face already matched to John Doe, another unmatched but matches John Doe's embedding
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        status, data = self.requests.post("/api/photo/automatch", {"photo_path": "C:/photos/already_tagged_test.jpg"})
        self.assertEqual(status, 200, data)

        # The unmatched face should not be matched because John Doe is already tagged on this photo
        self.assertEqual(data["matched_count"], 0)
        
        # Verify the unmatched face remains None in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (native("C:/photos/already_tagged_test.jpg"),))
        names = sorted([str(r[0]) for r in c.fetchall()])
        self.assertEqual(names, ["John Doe", "None"])
        conn.close()

    def test_api_face_match_duplicate_conflict(self):
        # Open database, insert two faces in the same photo
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/conflict_test.jpg"), ['John Doe'])
        
        # Face 1 is John Doe, Face 2 is unmatched
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), "[0,0,10,10]", b"", "John Doe", 0.95))
        face1_id = cursor.lastrowid
        
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), "[20,20,30,30]", b"", None, 0.95))
        face2_id = cursor.lastrowid
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger single match API trying to tag face 2 as "John Doe"
        status, data = self.requests.post("/api/face/match", {"face_id": face2_id, "person_name": "John Doe"})
        self.assertEqual(status, 400, "API should return HTTP 400 for duplicate tag conflict")
        self.assertFalse(data["success"])
        self.assertIn("already tagged on another face", data["error"])

    def test_api_faces_match_bulk_duplicate_conflict(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Photo 1 has John Doe already, and an unmatched face
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        add_people(cursor, native("C:/photos/conflict_p1.jpg"), ['John Doe'])
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), "[0,0,10,10]", b"", "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), "[20,20,30,30]", b"", None, 0.95))
        face2_id = cursor.lastrowid
        
        # Photo 2 has another unmatched face
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p2.jpg"), 1000.0, 100, "[]", "[]", "{}"))
        cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p2.jpg"), "[0,0,10,10]", b"", None, 0.95))
        face3_id = cursor.lastrowid
        
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger match-bulk trying to assign Face 2 and Face 3 to "John Doe"
        status, data = self.requests.post("/api/faces/match-bulk",
                                          {"face_ids": [face2_id, face3_id], "person_name": "John Doe"})
        self.assertEqual(status, 400, "API should return HTTP 400 for duplicate tag conflict in bulk match")
        self.assertFalse(data["success"])
        self.assertIn("already tagged on another face", data["error"])

    def test_build_photo_ui_record(self):
        from tagpup.services.photos import page_record
        dummy_meta = {
            "tags": ["A", "B"],
            "people": ["Alice"],
            "captions": ["Caption 1"],
            "raw_metadata": {"EXIF:DateTimeOriginal": "2026:05:27 12:34:56"}
        }
        res = page_record("C:/path/to/my_photo.jpg", dummy_meta, mtime=123.45, size=999)
        self.assertEqual(res["filename"], "my_photo.jpg")
        self.assertEqual(res["year"], "2026")
        self.assertEqual(res["title"], "Caption 1")
        self.assertEqual(res["mtime"], 123.45)
        self.assertEqual(res["size"], 999)
        self.assertEqual(res["tags"], ["A", "B"])

    def test_rotate_image_file_direction_validation(self):
        import tempfile
        import shutil
        from PIL import Image, ImageOps
        from tagpup.files.metadata import rotate_image_file
        from tests.test_taxonomy_lifecycle import EXIFTOOL
        if not EXIFTOOL:
            self.skipTest("ExifTool not installed")

        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        img_path = os.path.join(temp_dir, "test_rotate.jpg")

        # Create an asymmetrical image: 10 wide, 20 high
        img = Image.new("RGB", (10, 20), color="red")
        img.save(img_path, "JPEG")

        def shown_size():
            # A rotation sets the Orientation tag; the pixels stay as stored.
            with Image.open(img_path) as rotated:
                return ImageOps.exif_transpose(rotated).size

        with self.assertRaises(ValueError):
            rotate_image_file(img_path, "sideways", EXIFTOOL)

        # Rotate left (counter-clockwise) -> shown 20 wide, 10 high
        rotate_image_file(img_path, "left", EXIFTOOL)
        self.assertEqual(shown_size(), (20, 10), "Rotating left should swap dimensions")

        # Rotate right (clockwise) -> shown 10 wide, 20 high again
        rotate_image_file(img_path, "right", EXIFTOOL)
        self.assertEqual(shown_size(), (10, 20), "Rotating right should swap dimensions back")

    def test_hierarchical_tags_cleaning(self):
        from tagpup.core.vocabulary import extract_tags
        dummy_meta = {
            "XMP:Subject": ["Family/John Doe", "John Doe", "Family", "Nature"],
            "XMP:HierarchicalSubject": ["Family/John Doe"]
        }
        res = extract_tags(dummy_meta)
        self.assertIn("Family/John Doe", res)
        self.assertIn("Nature", res)
        self.assertNotIn("John Doe", res, "Should hide redundant leaf component tag")
        self.assertNotIn("Family", res, "Should hide redundant parent component tag")

    def test_era_aware_face_centroids(self):
        photo_index = PhotoIndex(self.TEST_DB_PATH, configured_model())
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Clear tables
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        # The library flags People as holding faces, as a real library's tree does:
        # no root holds faces for its name (docs/findings.md, #66).
        from tagpup.store import taxonomy as store_taxonomy
        store_taxonomy.add_path(photo_index.conn, "People", root_has_face=1)

        # We will create two eras:
        # Era 1 (2010): 6 photos/faces of Wren as a child (embedding: [1.0, 0.0, 0.0, ...])
        # Era 2 (2026): 6 photos/faces of Wren as a teen (embedding: [0.0, 1.0, 0.0, ...])
        emb_child = np.zeros(512, dtype=np.float32)
        emb_child[0] = 1.0
        emb_child_bytes = emb_child.tobytes()
        
        emb_teen = np.zeros(512, dtype=np.float32)
        emb_teen[1] = 1.0
        emb_teen_bytes = emb_teen.tobytes()
        
        # Insert Era 1 child faces (2010). Each photo names her in its keywords: a name
        # only on the face is clustering's own guess, which is not evidence for itself.
        for i in range(6):
            path = f"C:/photos/2010_child_{i}.jpg"
            raw_meta = {"EXIF:DateTimeOriginal": "2010:06:01 12:00:00"}
            cursor.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, 1000.0, 10, '[\"People/Wren\"]', '[]', ?)", (path, json.dumps(raw_meta)))
            add_people(cursor, path, ["Wren"])
            cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), '[10, 10, 50, 50]', ?, 'Wren', 0.99)", (path, emb_child_bytes))
            
        # Insert Era 2 teen faces (2026)
        for i in range(6):
            path = f"C:/photos/2026_teen_{i}.jpg"
            raw_meta = {"EXIF:DateTimeOriginal": "2026:06:01 12:00:00"}
            cursor.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, 2000.0, 10, '[\"People/Wren\"]', '[]', ?)", (path, json.dumps(raw_meta)))
            add_people(cursor, path, ["Wren"])
            cursor.execute("INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), '[10, 10, 50, 50]', ?, 'Wren', 0.99)", (path, emb_teen_bytes))
            
        photo_index.conn.commit()
        
        # Re-load photo_index metadata
        photo_index.load()
        
        # Now run identity resolution
        identities.resolve(photo_index, max_iterations=2)
        
        # Verify that teen faces from 2026 were NOT unassigned
        cursor.execute("SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE p.path LIKE '%2026%'")
        rows = cursor.fetchall()
        for path, name in rows:
            self.assertEqual(name, "Wren", f"Teen face {path} should remain resolved to Wren under era-aware centroids")
            
        # Verify `/api/person-faces` returns high similarity for both eras when queried
        response_data = self.requests.get("/api/person-faces?name=Wren")

        faces = response_data["faces"]
        self.assertEqual(len(faces), 12, "Should return all 12 faces")
        for f in faces:
            self.assertGreaterEqual(f["similarity"], 0.95, f"Similarity for face {f['photo_path']} should be high (>= 0.95) owing to era-aware centroids")
            
        photo_index.close()

class TestPhotoActions(unittest.TestCase):
    """Time shift, Smart Rename and delete, on the TagPup app.

    These ran against TagTuner's copies of the routes, which no page ever called and
    which are gone; TagPup's are the ones people use. Its library lives in a folder of
    its own (tests/handler_harness.Library), and the app is asked through Flask's test
    client: no server, no port, no sleep.
    """

    def setUp(self):
        self.lib = Library(self, "photo_actions")
        self.TEST_DB_PATH = self.lib.db_path

    def post(self, path, body):
        status, reply = self.lib.post(path, body)
        self.assertEqual(200, status, reply)
        return reply

    def cache(self, folder, photos):
        """File `photos` the way the folder scan files them: under the folder's key,
        then each photo's."""
        self.lib.folders().put(folder, photos)

    def test_api_folder_time_shift(self):
        import tempfile
        from PIL import Image

        temp_dir = tempfile.mkdtemp(dir=self.lib.root)
        img_path = os.path.join(temp_dir, "test_shift.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img_path, "JPEG")

        self.cache(temp_dir, {
            paths.key(img_path): {
                "path": img_path,
                "raw_metadata": {
                    "EXIF:Model": "Test Camera",
                    "EXIF:DateTimeOriginal": "2026:01:01 12:00:00",
                    "EXIF:CreateDate": "2026:01:01 12:00:00"
                }
            }
        })

        data = self.post("/api/folder/time-shift", {
            "folder_path": temp_dir, "camera_model": "Test Camera", "shift_minutes": 30})
        self.assertTrue(data["success"])
        self.assertIn("updated_photos", data)
        # The page is handed the photo's own path, not the lower-cased key it is
        # filed under in the cache.
        self.assertEqual([p["path"] for p in data["updated_photos"]], [img_path])

    def test_api_folder_time_shift_scans_an_uncached_folder_into_real_paths(self):
        """With nothing cached, the shift scans the folder itself -- and still hands
        back each photo's path as it is, not the lower-cased key it files it under."""
        import tempfile
        from PIL import Image

        temp_dir = os.path.join(tempfile.mkdtemp(dir=self.lib.root), "Shoot_Day")
        os.makedirs(temp_dir)
        img_path = os.path.join(temp_dir, "IMG_Shift.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img_path, "JPEG")

        data = self.post("/api/folder/time-shift", {
            "folder_path": temp_dir, "camera_model": "All Cameras", "shift_minutes": 30})
        self.assertTrue(data["success"], data)
        self.assertEqual([p["path"] for p in data["updated_photos"]], [img_path])

    def _seed_rename_photo(self, conn, path, mtime, taken, face_name=None):
        """A photo row -- and optionally a named face -- as the indexer writes them."""
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (path, mtime, 100, "[]", "[]",
             json.dumps({"EXIF:DateTimeOriginal": taken} if taken else {})))
        add_people(conn, path, [face_name] if face_name else [])
        if face_name:
            conn.execute(
                "INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, ?)",
                (path, "[0,0,10,10]", b"", face_name, 0.9))

    def _post_rename(self, temp_dir, photo_paths):
        return self.post("/api/folder/rename-photos", {
            "folder_path": temp_dir, "photo_paths": photo_paths, "grouping": "TestGroup"})

    def _rows(self, sql, params=()):
        return self.lib.rows(sql, params)

    def _record(self, path, taken, mtime):
        return photo_actions.page_record(
            path, {"path": path, "raw_metadata": {"EXIF:DateTimeOriginal": taken} if taken else {}}, mtime, 100)

    def test_api_folder_rename_photos(self):
        import tempfile
        from PIL import Image

        temp_dir = tempfile.mkdtemp(dir=self.lib.root)
        p1 = os.path.join(temp_dir, "file_A.jpg")
        p2 = os.path.join(temp_dir, "file_B.jpg")

        im = Image.new("RGB", (10, 10), "blue")
        im.save(p1)
        im.save(p2)

        conn = tagpup_db.connect(self.TEST_DB_PATH)
        self._seed_rename_photo(conn, p1, 2000.0, "2026:06:27 12:00:00", "Rowan Thackeray")
        self._seed_rename_photo(conn, p2, 1000.0, "2026:06:27 11:00:00", "Tamsin Okafor")
        conn.commit()
        conn.close()
        faces_before = self._rows("SELECT COUNT(*) FROM faces")[0][0]

        self.cache(temp_dir, {
            paths.key(p1): self._record(p1, "2026:06:27 12:00:00", 2000.0),
            paths.key(p2): self._record(p2, "2026:06:27 11:00:00", 1000.0),
        })

        data = self._post_rename(temp_dir, [p1, p2])
        self.assertTrue(data["success"])

        expected_p2_new = os.path.join(temp_dir, "TestGroup - 1.jpg")
        expected_p1_new = os.path.join(temp_dir, "TestGroup - 2.jpg")

        self.assertTrue(os.path.exists(expected_p2_new))
        self.assertTrue(os.path.exists(expected_p1_new))
        self.assertFalse(os.path.exists(p1))
        self.assertFalse(os.path.exists(p2))

        # The index follows the files: photo rows at the new names, spelled as
        # the indexer spells them, and nothing left at the old ones.
        db_paths = sorted(r[0] for r in self._rows("SELECT path FROM photos"))
        self.assertIn(expected_p1_new, db_paths)
        self.assertIn(expected_p2_new, db_paths)
        self.assertNotIn(p1, db_paths)
        self.assertNotIn(p2, db_paths)

        # And the faces go with them, names kept, none duplicated.
        faces = dict(self._rows("SELECT p.path, f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE f.name IS NOT NULL"
                                " AND p.path IN (?, ?, ?, ?)",
                                (p1, p2, expected_p1_new, expected_p2_new)))
        self.assertEqual(faces, {expected_p1_new: "Rowan Thackeray",
                                 expected_p2_new: "Tamsin Okafor"})
        self.assertEqual(self._rows("SELECT COUNT(*) FROM faces")[0][0], faces_before)
        self.assertEqual(data["index_rows_moved"], 2)
        self.assertEqual(data["index_skipped"], [])

    def test_api_folder_rename_photos_does_not_merge_onto_rows_already_there(self):
        """A stale row at the new name is not merged into: that duplicates faces."""
        import tempfile
        from PIL import Image

        temp_dir = tempfile.mkdtemp(dir=self.lib.root)
        p1 = os.path.join(temp_dir, "file_A.jpg")
        Image.new("RGB", (10, 10), "blue").save(p1)
        stale = os.path.join(temp_dir, "TestGroup - 1.jpg")  # no file, only rows

        conn = tagpup_db.connect(self.TEST_DB_PATH)
        self._seed_rename_photo(conn, p1, 2000.0, "2026:06:27 12:00:00", "Rowan Thackeray")
        self._seed_rename_photo(conn, stale, 500.0, None, "Tamsin Okafor")
        conn.commit()
        conn.close()

        data = self._post_rename(temp_dir, [p1])
        self.assertTrue(data["success"])
        self.assertTrue(os.path.exists(stale), "the file itself was still renamed")

        self.assertEqual(data["index_skipped"], [stale])
        self.assertEqual(data["index_rows_moved"], 0)
        self.assertEqual(
            self._rows("SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (stale,)),
            [("Tamsin Okafor",)], "faces were merged onto the rows already there")
        self.assertEqual(
            self._rows("SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (p1,)),
            [("Rowan Thackeray",)])

    def test_api_folder_rename_photos_conflict_resolution(self):
        import tempfile
        from PIL import Image

        temp_dir = tempfile.mkdtemp(dir=self.lib.root)
        # Create two selected files
        p1 = os.path.join(temp_dir, "file_A.jpg")
        p2 = os.path.join(temp_dir, "file_B.jpg")
        # Create conflicting file occupant (this one is NOT in our renaming selection)
        p_conflict = os.path.join(temp_dir, "TestGroup - 1.jpg")

        im = Image.new("RGB", (10, 10), "blue")
        im.save(p1)
        im.save(p2)
        im.save(p_conflict)

        conn = tagpup_db.connect(self.TEST_DB_PATH)
        self._seed_rename_photo(conn, p1, 2000.0, "2026:06:27 12:00:00", "Rowan Thackeray")
        self._seed_rename_photo(conn, p2, 1000.0, "2026:06:27 11:00:00", "Tamsin Okafor")
        self._seed_rename_photo(conn, p_conflict, 500.0, None, "Ellis Marchetti")
        conn.commit()
        conn.close()

        # Pre-populate the server's cache
        self.cache(temp_dir, {
            paths.key(p1): self._record(p1, "2026:06:27 12:00:00", 2000.0),
            paths.key(p2): self._record(p2, "2026:06:27 11:00:00", 1000.0),
            paths.key(p_conflict): self._record(p_conflict, None, 500.0),
        })

        data = self._post_rename(temp_dir, [p1, p2])
        self.assertTrue(data["success"])

        # Check targets were successfully created
        expected_p2_new = os.path.join(temp_dir, "TestGroup - 1.jpg")
        expected_p1_new = os.path.join(temp_dir, "TestGroup - 2.jpg")
        expected_conflict_new = os.path.join(temp_dir, "TestGroup - 1_conflict_1.jpg")

        self.assertTrue(os.path.exists(expected_p2_new), f"Should have created {expected_p2_new}")
        self.assertTrue(os.path.exists(expected_p1_new), f"Should have created {expected_p1_new}")
        self.assertTrue(os.path.exists(expected_conflict_new), f"Should have moved conflicting occupant to {expected_conflict_new}")

        # Verify original selected files and old conflict files are gone from their old paths
        self.assertFalse(os.path.exists(p1))
        self.assertFalse(os.path.exists(p2))
        # Note that p_conflict old path was occupied by expected_p2_new, so the old path now has the new file content.

        # Verify DB paths
        db_paths = [r[0] for r in self._rows("SELECT path FROM photos")]
        self.assertIn(expected_p1_new, db_paths)
        self.assertIn(expected_p2_new, db_paths)
        self.assertIn(expected_conflict_new, db_paths)

        # Each face followed its own photo: the occupant's to where it was moved
        # aside, the renamed photo's onto the name the occupant gave up.
        faces = dict(self._rows("SELECT f.name, p.path FROM faces f JOIN photos p ON p.id = f.photo_id WHERE f.name IS NOT NULL"))
        self.assertEqual(faces["Ellis Marchetti"], expected_conflict_new)
        self.assertEqual(faces["Tamsin Okafor"], expected_p2_new)
        self.assertEqual(faces["Rowan Thackeray"], expected_p1_new)
        self.assertEqual(data["index_rows_moved"], 3)

    def test_api_photo_delete(self):
        import tempfile
        from unittest import mock

        temp_dir = tempfile.mkdtemp(dir=self.lib.root)
        p = os.path.join(temp_dir, "to_delete.jpg")
        with open(p, "wb") as f:
            f.write(b"fake jpeg content")

        # Seed the database the way the indexer writes it: native paths.
        conn = tagpup_db.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags) VALUES (?, 1.0, 10, '[]')", (p,))
        c.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector) SELECT id, 'm|p|cropped|1.0|100', 1.0, 10, ? FROM photos WHERE path = ?", (b'\x00'*512, p))
        c.execute("INSERT INTO faces (photo_id, box, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), '[]', 'Rowan Thackeray', 1.0)", (p,))
        conn.commit()
        conn.close()

        # Populate the server's folder cache
        folder_key = paths.key(temp_dir)
        photo_key = paths.key(p)
        self.cache(temp_dir, {photo_key: {"path": p, "filename": "to_delete.jpg", "tags": []}})

        # Removed outright here rather than sent to this machine's Recycle Bin, which
        # the old test filled with a file per run.
        def remove(path):
            os.remove(path)
            return True

        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=remove):
            data = self.post("/api/photo/delete", {"path": p})
        self.assertTrue(data["success"])

        # Verify file is not on disk (moved to recycle bin)
        self.assertFalse(os.path.exists(p))

        # Verify records are gone from DB
        photos_count = self._rows("SELECT COUNT(*) FROM photos WHERE path = ?", (p,))[0][0]
        # By the photo's path, or pointing at no photo row at all: a vector whose
        # photo went while it stayed would otherwise count as gone.
        cache_count = self._rows("SELECT COUNT(*) FROM embeddings e LEFT JOIN photos p ON p.id = e.photo_id"
                                 " WHERE p.path = ? OR p.id IS NULL", (p,))[0][0]
        faces_count = self._rows("SELECT COUNT(*) FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (p,))[0][0]

        self.assertEqual(photos_count, 0)
        self.assertEqual(cache_count, 0)
        self.assertEqual(faces_count, 0)

        # Verify the folder cache's entry is evicted
        self.assertNotIn(photo_key, self.lib.folders().get(folder_key))

if __name__ == "__main__":
    unittest.main()

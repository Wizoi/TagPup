import os
import sys
import json
import sqlite3
import urllib.request
import urllib.error
import threading
import time
import unittest
import numpy as np

# Add workspace and scripts directories to search path
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex
from tuner_server import start_server, TunerHTTPRequestHandler
from tagpup_server import TagPupHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from free_port import free_port  # noqa: E402


def native(path):
    """A path as the indexer stores it: absolute, with this platform's separators."""
    return os.path.abspath(path)


class TestStability(unittest.TestCase):
    TEST_DB_PATH = os.path.join(WORKSPACE_DIR, "data", "test_validation_index.db")
    TEST_PORT = free_port()
    server_thread = None

    @classmethod
    def setUpClass(cls):
        # Its own port: subclasses inherit the attribute, and a port
        # already held by the last class's server is refused.
        cls.TEST_PORT = free_port()
        # Start the server once in a background thread
        cls.server_thread = threading.Thread(
            target=start_server,
            kwargs={"port": cls.TEST_PORT, "db_path": cls.TEST_DB_PATH, "gui_dir": os.path.join(WORKSPACE_DIR, "gui")},
            daemon=True
        )
        cls.server_thread.start()
        time.sleep(1.0) # Wait for server to bind

    def setUp(self):
        # Setup dummy data in test DB for each test to run in isolation
        if os.path.exists(self.TEST_DB_PATH):
            try:
                os.remove(self.TEST_DB_PATH)
            except Exception:
                pass
                
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        
        # Insert a dummy photo with a valid embedding
        dummy_emb = np.random.rand(512).astype(np.float32)
        dummy_emb_bytes = dummy_emb.tobytes()
        
        cursor = photo_index.conn.cursor()
        
        # Clear any existing rows to prevent unique constraint failures if DB file reuse occurs
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        
        # Insert parent photo
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/test_photo.jpg",
            12345.67,
            98765,
            json.dumps(["Nature", "Forest"]),
            json.dumps(["John Doe"]),
            json.dumps(["A beautiful forest"]),
            json.dumps({"Make": "Canon", "Model": "EOS 5D"}),
            dummy_emb_bytes
        ))
        
        # Insert linked face
        dummy_face_emb = np.random.rand(512).astype(np.float32).tobytes()
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/test_photo.jpg",
            json.dumps([10, 20, 50, 60]),
            dummy_face_emb,
            "John Doe",
            0.95
        ))
        
        photo_index.conn.commit()
        photo_index.close()

        # Reset handler state
        TunerHTTPRequestHandler.clustering_in_progress = False

    def tearDown(self):
        from tuner_server import set_active_db_path
        set_active_db_path(None)
        if os.path.exists(self.TEST_DB_PATH):
            try:
                os.remove(self.TEST_DB_PATH)
            except Exception:
                pass

    def test_database_reset_logic(self):
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        
        # Verify metadata is loaded
        self.assertEqual(len(photo_index.metadata), 1, "Photo record should be loaded")
        meta = photo_index.metadata[0]
        self.assertTrue(meta["has_embedding"], "Photo should report having an embedding")
        
        # Verify face record is present
        faces = photo_index.get_all_faces()
        self.assertEqual(len(faces), 1, "Face record should be loaded")
        self.assertEqual(faces[0]["name"], "John Doe", "Face name should be preserved")
        
        # Clear CLIP embeddings
        photo_index.clear_clip_embeddings()
        
        # Reload and verify
        photo_index.load()
        meta_after = photo_index.metadata[0]
        self.assertFalse(meta_after["has_embedding"], "Photo embedding should be cleared (None)")
        
        faces_after = photo_index.get_all_faces()
        self.assertEqual(len(faces_after), 1, "Face record should STILL be present")
        self.assertEqual(faces_after[0]["name"], "John Doe", "Face name assignment MUST be preserved")
        
        photo_index.close()

    def test_api_unmatched_photos(self):
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/photos?mode=unmatched"
        response = urllib.request.urlopen(url)
        data = json.loads(response.read().decode('utf-8'))
        self.assertEqual(len(data), 0, "No unmatched photo should be returned if face has a name")

    def test_api_validation_malformed_json(self):
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/face/match"
        req = urllib.request.Request(
            url,
            data=b"not-json-format",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400, "Server should reject malformed JSON with 400 Bad Request")

    def test_api_validation_missing_params(self):
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/face/match"
        req = urllib.request.Request(
            url,
            data=json.dumps({"face_id": 1}).encode('utf-8'), # Missing person_name
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400, "Server should reject missing parameters with 400 Bad Request")

    def test_api_clustering_busy_lock(self):
        # Force clustering_in_progress to True to simulate active clustering
        TunerHTTPRequestHandler.clustering_in_progress = True
        
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/face/match"
        req = urllib.request.Request(
            url,
            data=json.dumps({"face_id": 1, "person_name": "Jane Doe"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 409, "Server should reject writes with 409 Conflict when clustering is active")
        
        # Verify JSON body in 409 error
        error_body = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertFalse(error_body["success"])
        self.assertIn("clustering", error_body["error"].lower())

    def test_strict_tag_enforcement_in_clustering(self):
        from faces import FaceProcessor
        from taxonomy import TagTaxonomy
        
        # Open the index
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
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
            INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", 1000.0, 500, json.dumps([]), json.dumps(["Alice"]), json.dumps([]), json.dumps({}), None
        ))
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", json.dumps([10, 10, 50, 50]), face_emb_bytes, None, 0.95
        ))
        
        # Photo 2: Untagged
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", 2000.0, 500, json.dumps([]), json.dumps([]), json.dumps([]), json.dumps({}), None
        ))
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([10, 10, 50, 50]), face_emb_bytes, None, 0.95
        ))
        
        photo_index.conn.commit()
        photo_index.load()
        
        # Initialize taxonomy file
        tax_path = self.TEST_DB_PATH.replace(".db", ".json")
        if os.path.exists(tax_path):
            os.remove(tax_path)
        taxonomy = TagTaxonomy(file_path=tax_path)
        taxonomy.load()
        taxonomy.add_tags(["Alice"])
        taxonomy.save()
        
        # Run clustering
        processor = FaceProcessor()
        processor.resnet = None # Prevent neural model load to keep unit tests fast
        
        processor.cluster_and_resolve_identities(photo_index, taxonomy, max_iterations=1)
        
        # Query results from faces table
        cursor.execute("SELECT photo_path, name FROM faces")
        resolved_faces = cursor.fetchall()
        
        photo_index.close()
        
        # Clean up taxonomy
        if os.path.exists(tax_path):
            os.remove(tax_path)
            
        # Assertions:
        # Photo 1 (tagged with "Alice") should have its face resolved to "Alice"
        # Photo 2 (untagged) should remain None
        self.assertEqual(len(resolved_faces), 2, "There should be 2 faces in total")
        
        face1 = next(r for r in resolved_faces if r[0] == "C:/photos/photo1.jpg")
        face2 = next(r for r in resolved_faces if r[0] == "C:/photos/photo2.jpg")
        
        self.assertEqual(face1[1], "Alice", "Face in tagged photo should be resolved to Alice")
        self.assertIsNone(face2[1], "Face in untagged photo must remain None under strict tag enforcement")

    def test_similarity_threshold_in_clustering(self):
        from faces import FaceProcessor
        from taxonomy import TagTaxonomy
        
        # Open the index
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
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
            INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", 1000.0, 500, json.dumps([]), json.dumps(["Alice"]), json.dumps([]), json.dumps({}), None
        ))
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo1.jpg", json.dumps([10, 10, 50, 50]), emb1.tobytes(), None, 0.95
        ))
        
        # Photo 2: Two faces, tagged with "Alice", but both faces have similarity < 0.80 to Alice
        cursor.execute("""
            INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", 2000.0, 500, json.dumps([]), json.dumps(["Alice"]), json.dumps([]), json.dumps({}), None
        ))
        # Face 2 (in Photo 2)
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([10, 10, 50, 50]), emb2.tobytes(), None, 0.95
        ))
        # Face 3 (in Photo 2)
        cursor.execute("""
            INSERT INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "C:/photos/photo2.jpg", json.dumps([100, 100, 150, 150]), emb3.tobytes(), None, 0.95
        ))
        
        photo_index.conn.commit()
        photo_index.load()
        
        # Initialize taxonomy
        tax_path = self.TEST_DB_PATH.replace(".db", ".json")
        if os.path.exists(tax_path):
            os.remove(tax_path)
        taxonomy = TagTaxonomy(file_path=tax_path)
        taxonomy.load()
        taxonomy.add_tags(["Alice"])
        taxonomy.save()
        
        # Run clustering
        processor = FaceProcessor()
        processor.resnet = None
        
        processor.cluster_and_resolve_identities(photo_index, taxonomy, max_iterations=1)
        
        # Query results
        cursor.execute("SELECT photo_path, name FROM faces")
        resolved_faces = cursor.fetchall()
        
        photo_index.close()
        if os.path.exists(tax_path):
            os.remove(tax_path)
            
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
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
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
                json.dumps(["John Doe"]),
                json.dumps(["caption"]),
                json.dumps({"EXIF:DateTimeOriginal": "2026:06:24 18:00:00", "Make": "Canon"}),
                dummy_emb
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
            INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, photo_rows)
        cursor.executemany("""
            INSERT OR REPLACE INTO faces (photo_path, box, embedding, name, prob)
            VALUES (?, ?, ?, ?, ?)
        """, face_rows)
        photo_index.conn.commit()
        photo_index.close()
        
        # Test performance of fetching a large page of 5000 faces from endpoint
        import urllib.request
        import urllib.parse
        
        start_time = time.time()
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/person-faces?name=John%20Doe&limit=5000"
        response = urllib.request.urlopen(url)
        res_data = json.loads(response.read().decode('utf-8'))
        duration = time.time() - start_time
        
        print(f"\n[PERF TEST] Loading 5000 faces from endpoint took {duration:.4f} seconds.")
        self.assertLess(duration, 5.0)
        self.assertIn("faces", res_data)
        self.assertEqual(len(res_data["faces"]), 5000)

    def test_year_fallback_chain(self):
        import sys
        sys.path.append("scripts")
        from tuner_server import get_year_from_mtime_or_meta
        from metadata import parse_year_from_metadata
        
        # 1. Test tuner_server's get_year_from_mtime_or_meta
        # Metadata has year
        raw_meta = json.dumps({"EXIF:DateTimeOriginal": "2005:06:26 12:34:56"})
        year = get_year_from_mtime_or_meta(123456789.0, raw_meta, "D:/Training/Pictures/2008/2008-06-26/2008-06-Family.jpg")
        self.assertEqual(year, 2005)
        
        # Metadata is empty/None, filename has year
        year = get_year_from_mtime_or_meta(123456789.0, None, "D:/Training/Pictures/2008/family_2004.jpg")
        self.assertEqual(year, 2004)
        
        # Metadata is empty/None, filename has no year, containing folder has year
        year = get_year_from_mtime_or_meta(123456789.0, None, "D:/Training/Pictures/2008/EarthDay/photo.jpg")
        self.assertEqual(year, 2008)
        
        # None of them have year
        year = get_year_from_mtime_or_meta(123456789.0, None, "D:/Training/Pictures/NoYear/photo.jpg")
        self.assertEqual(year, "Unknown")
        
        # 2. Test metadata's parse_year_from_metadata
        meta_1 = {"raw_metadata": {"EXIF:DateTimeOriginal": "2005:06:26 12:34:56"}, "path": "D:/2008/photo.jpg"}
        self.assertEqual(parse_year_from_metadata(meta_1), 2005)
        
        meta_2 = {"raw_metadata": None, "path": "D:/Training/Pictures/2008/family_2004.jpg"}
        self.assertEqual(parse_year_from_metadata(meta_2), 2004)
        
        meta_3 = {"raw_metadata": None, "path": "D:/Training/Pictures/2008/EarthDay/photo.jpg"}
        self.assertEqual(parse_year_from_metadata(meta_3), 2008)
        
        meta_4 = {"raw_metadata": None, "path": "D:/Training/Pictures/NoYear/photo.jpg"}
        self.assertIsNone(parse_year_from_metadata(meta_4))

    def test_api_photo_automatch_unmatched(self):
        # Open database, insert a resolved face (e.g. John Doe) and an unmatched face (name = None) with similar embedding
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Insert a resolved face
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        # Insert unmatched face with same embedding (similarity = 1.0) but name IS NULL
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/automatch_test.jpg"), 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/automatch_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/photo/automatch"
        req = urllib.request.Request(
            url,
            data=json.dumps({"photo_path": "C:/photos/automatch_test.jpg"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))
        self.assertTrue(data["success"])
        self.assertEqual(data["matched_count"], 1)
        
        # Verify the face was successfully resolved to John Doe in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_path = ?", (native("C:/photos/automatch_test.jpg"),))
        name = c.fetchone()[0]
        self.assertEqual(name, "John Doe")
        
        # Also verify photo's people field is updated
        c.execute("SELECT people FROM photos WHERE path = ?", (native("C:/photos/automatch_test.jpg"),))
        people = json.loads(c.fetchone()[0])
        self.assertIn("John Doe", people)
        conn.close()

    def test_api_folder_automatch(self):
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        # Insert photos in the same folder
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo1.jpg"), 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo2.jpg"), 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/john_doe.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        # Insert faces
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo1.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/folderA/photo2.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger folder automatch API
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/folder/automatch"
        req = urllib.request.Request(
            url,
            data=json.dumps({"folder_path": "C:/photos/folderA"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))
        self.assertTrue(data["success"])
        self.assertEqual(data["matched_count"], 2)
        self.assertIn("remaining_counts", data)
        
        # Verify the faces were successfully resolved to John Doe in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_path IN (?, ?)",
                  (native("C:/photos/folderA/photo1.jpg"), native("C:/photos/folderA/photo2.jpg")))
        names = [r[0] for r in c.fetchall()]
        self.assertEqual(names, ["John Doe", "John Doe"])
        conn.close()

    def test_api_photo_automatch_duplicate_protection(self):
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # Insert two unmatched faces on the same photo that both match John Doe
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/duplicate_test.jpg"), "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/photo/automatch"
        req = urllib.request.Request(
            url,
            data=json.dumps({"photo_path": "C:/photos/duplicate_test.jpg"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))
        
        # Neither face should be matched (matched_count = 0)
        self.assertEqual(data["matched_count"], 0)
        
        # Verify both faces remain None in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_path = ?", (native("C:/photos/duplicate_test.jpg"),))
        names = [r[0] for r in c.fetchall()]
        self.assertEqual(names, [None, None])
        conn.close()

    def test_api_photo_automatch_already_tagged_protection(self):
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        resolved_emb = np.ones(512, dtype=np.float32)
        norm = np.linalg.norm(resolved_emb)
        resolved_emb /= norm
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/john_doe.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # One face already matched to John Doe, another unmatched but matches John Doe's embedding
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/already_tagged_test.jpg"), "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger automatch API
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/photo/automatch"
        req = urllib.request.Request(
            url,
            data=json.dumps({"photo_path": "C:/photos/already_tagged_test.jpg"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))
        
        # The unmatched face should not be matched because John Doe is already tagged on this photo
        self.assertEqual(data["matched_count"], 0)
        
        # Verify the unmatched face remains None in the DB
        conn = sqlite3.connect(self.TEST_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM faces WHERE photo_path = ?", (native("C:/photos/already_tagged_test.jpg"),))
        names = sorted([str(r[0]) for r in c.fetchall()])
        self.assertEqual(names, ["John Doe", "None"])
        conn.close()

    def test_api_face_match_duplicate_conflict(self):
        # Open database, insert two faces in the same photo
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        # Face 1 is John Doe, Face 2 is unmatched
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), "[0,0,10,10]", b"", "John Doe", 0.95))
        face1_id = cursor.lastrowid
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_test.jpg"), "[20,20,30,30]", b"", None, 0.95))
        face2_id = cursor.lastrowid
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger single match API trying to tag face 2 as "John Doe"
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/face/match"
        req = urllib.request.Request(
            url,
            data=json.dumps({"face_id": face2_id, "person_name": "John Doe"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            response = urllib.request.urlopen(req)
            self.fail("API should return HTTP 400 for duplicate tag conflict")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode('utf-8'))
            self.assertFalse(data["success"])
            self.assertIn("already tagged on another face", data["error"])

    def test_api_faces_match_bulk_duplicate_conflict(self):
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Photo 1 has John Doe already, and an unmatched face
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), "[0,0,10,10]", b"", "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p1.jpg"), "[20,20,30,30]", b"", None, 0.95))
        face2_id = cursor.lastrowid
        
        # Photo 2 has another unmatched face
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p2.jpg"), 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       (native("C:/photos/conflict_p2.jpg"), "[0,0,10,10]", b"", None, 0.95))
        face3_id = cursor.lastrowid
        
        photo_index.conn.commit()
        photo_index.close()
        
        # Trigger match-bulk trying to assign Face 2 and Face 3 to "John Doe"
        url = f"http://127.0.0.1:{self.TEST_PORT}/api/faces/match-bulk"
        req = urllib.request.Request(
            url,
            data=json.dumps({"face_ids": [face2_id, face3_id], "person_name": "John Doe"}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            response = urllib.request.urlopen(req)
            self.fail("API should return HTTP 400 for duplicate tag conflict in bulk match")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode('utf-8'))
            self.assertFalse(data["success"])
            self.assertIn("already tagged on another face", data["error"])

    def test_build_photo_ui_record(self):
        from metadata import build_photo_ui_record
        dummy_meta = {
            "tags": ["A", "B"],
            "people": ["Alice"],
            "captions": ["Caption 1"],
            "raw_metadata": {"EXIF:DateTimeOriginal": "2026:05:27 12:34:56"}
        }
        res = build_photo_ui_record("C:/path/to/my_photo.jpg", dummy_meta, mtime=123.45, size=999)
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
        from metadata import rotate_image_file
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
        from metadata import extract_tags
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
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        # Clear tables
        cursor.execute("DELETE FROM faces")
        cursor.execute("DELETE FROM photos")
        
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
            cursor.execute("INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, 1000.0, 10, '[\"People/Wren\"]', '[\"Wren\"]', '[]', ?)", (path, json.dumps(raw_meta)))
            cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, '[10, 10, 50, 50]', ?, 'Wren', 0.99)", (path, emb_child_bytes))
            
        # Insert Era 2 teen faces (2026)
        for i in range(6):
            path = f"C:/photos/2026_teen_{i}.jpg"
            raw_meta = {"EXIF:DateTimeOriginal": "2026:06:01 12:00:00"}
            cursor.execute("INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, 2000.0, 10, '[\"People/Wren\"]', '[\"Wren\"]', '[]', ?)", (path, json.dumps(raw_meta)))
            cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, '[10, 10, 50, 50]', ?, 'Wren', 0.99)", (path, emb_teen_bytes))
            
        photo_index.conn.commit()
        
        # Re-load photo_index metadata
        photo_index.load()
        
        # Now run FaceProcessor.cluster_and_resolve_identities()
        from faces import FaceProcessor
        fp = FaceProcessor()
        fp.cluster_and_resolve_identities(photo_index, None, max_iterations=2)
        
        # Verify that teen faces from 2026 were NOT unassigned
        cursor.execute("SELECT photo_path, name FROM faces WHERE photo_path LIKE '%2026%'")
        rows = cursor.fetchall()
        for path, name in rows:
            self.assertEqual(name, "Wren", f"Teen face {path} should remain resolved to Wren under era-aware centroids")
            
        # Verify `/api/person-faces` returns high similarity for both eras when queried
        from tuner_server import TunerHTTPRequestHandler
        import io
        class MockHandler(TunerHTTPRequestHandler):
            def __init__(self, db_path):
                self.db_path = db_path
                self.wfile = io.BytesIO()
            def send_response(self, code):
                self.code = code
            def send_header(self, keyword, value):
                pass
            def end_headers(self):
                pass
                
        handler = MockHandler(self.TEST_DB_PATH)
        handler.handle_get_person_faces({"name": ["Wren"]})
        handler.wfile.seek(0)
        response_data = json.loads(handler.wfile.read().decode('utf-8'))
        
        faces = response_data["faces"]
        self.assertEqual(len(faces), 12, "Should return all 12 faces")
        for f in faces:
            self.assertGreaterEqual(f["similarity"], 0.95, f"Similarity for face {f['photo_path']} should be high (>= 0.95) owing to era-aware centroids")
            
        photo_index.close()


class TestPhotoActions(unittest.TestCase):
    """Time shift, Smart Rename and delete, on the TagPup server.

    These ran against TagTuner's copies of the routes, which no page ever called and
    which are gone; TagPup's are the ones people use. Its library lives in a folder of
    its own, not the checkout's data/.
    """

    @classmethod
    def setUpClass(cls):
        import hashlib
        import shutil
        import tempfile
        from tagpup_server import start_server as start_tagpup_server

        cls.TEST_PORT = free_port()
        # One folder per checkout, cleared here: the server holds its library open until
        # the process ends, so a run cannot delete its own, but the next one can.
        checkout = hashlib.md5(WORKSPACE_DIR.lower().encode("utf-8")).hexdigest()[:8]
        cls.home = os.path.join(tempfile.gettempdir(), "tagpup_photo_actions_" + checkout)
        shutil.rmtree(cls.home, ignore_errors=True)
        os.makedirs(cls.home, exist_ok=True)
        cls.TEST_DB_PATH = os.path.join(cls.home, "photo_actions.db")
        PhotoIndex(db_path=cls.TEST_DB_PATH).load()
        threading.Thread(
            target=start_tagpup_server,
            kwargs={"port": cls.TEST_PORT, "db_path": cls.TEST_DB_PATH,
                    "gui_dir": os.path.join(WORKSPACE_DIR, "gui_tagpup")},
            daemon=True,
        ).start()
        time.sleep(1.0)  # Wait for server to bind

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.home, ignore_errors=True)   # the next run clears what this cannot

    def setUp(self):
        from tagpup_server import set_active_db_path

        conn = sqlite3.connect(self.TEST_DB_PATH)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM embedding_cache")
        conn.commit()
        conn.close()
        set_active_db_path(self.TEST_DB_PATH)
        TagPupHTTPRequestHandler.folder_cache.clear()
        set_active_db_path(None)

    def test_api_folder_time_shift(self):
        import tempfile
        import shutil
        from PIL import Image
        
        import paths

        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, "test_shift.jpg")
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(img_path, "JPEG")

        # Filed the way the folder scan files it: the folder's key, then each photo's.
        TagPupHTTPRequestHandler.folder_cache[paths.key(temp_dir)] = {
            paths.key(img_path): {
                "path": img_path,
                "raw_metadata": {
                    "EXIF:Model": "Test Camera",
                    "EXIF:DateTimeOriginal": "2026:01:01 12:00:00",
                    "EXIF:CreateDate": "2026:01:01 12:00:00"
                }
            }
        }

        url = f"http://127.0.0.1:{self.TEST_PORT}/api/folder/time-shift"
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "folder_path": temp_dir,
                "camera_model": "Test Camera",
                "shift_minutes": 30
            }).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            response = urllib.request.urlopen(req)
            data = json.loads(response.read().decode('utf-8'))
            self.assertTrue(data["success"])
            self.assertIn("updated_photos", data)
        except Exception as e:
            shutil.rmtree(temp_dir)
            self.fail(f"Time shift API request failed: {e}")

        shutil.rmtree(temp_dir)
        # The page is handed the photo's own path, not the lower-cased key it is
        # filed under in the cache.
        self.assertEqual([p["path"] for p in data["updated_photos"]], [img_path])

    def test_api_folder_time_shift_scans_an_uncached_folder_into_real_paths(self):
        """With nothing cached, the shift scans the folder itself -- and still hands
        back each photo's path as it is, not the lower-cased key it files it under."""
        import tempfile
        import shutil
        from PIL import Image

        temp_dir = os.path.join(tempfile.mkdtemp(), "Shoot_Day")
        os.makedirs(temp_dir)
        self.addCleanup(shutil.rmtree, os.path.dirname(temp_dir), True)
        img_path = os.path.join(temp_dir, "IMG_Shift.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img_path, "JPEG")

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/folder/time-shift",
            data=json.dumps({
                "folder_path": temp_dir,
                "camera_model": "All Cameras",
                "shift_minutes": 30
            }).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        data = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
        self.assertTrue(data["success"], data)
        self.assertEqual([p["path"] for p in data["updated_photos"]], [img_path])

    def _seed_rename_photo(self, conn, path, mtime, taken, face_name=None):
        """A photo row -- and optionally a named face -- as the indexer writes them."""
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, mtime, 100, "[]", json.dumps([face_name] if face_name else []), "[]",
             json.dumps({"EXIF:DateTimeOriginal": taken} if taken else {})))
        if face_name:
            conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                (path, "[0,0,10,10]", b"", face_name, 0.9))

    def _post_rename(self, temp_dir, photo_paths):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/folder/rename-photos",
            data=json.dumps({
                "folder_path": temp_dir,
                "photo_paths": photo_paths,
                "grouping": "TestGroup"
            }).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        return json.loads(urllib.request.urlopen(req).read().decode('utf-8'))

    def _rows(self, sql, params=()):
        conn = sqlite3.connect(self.TEST_DB_PATH)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_api_folder_rename_photos(self):
        import tempfile
        import shutil
        from PIL import Image
        import paths

        temp_dir = tempfile.mkdtemp()
        try:
            p1 = os.path.join(temp_dir, "file_A.jpg")
            p2 = os.path.join(temp_dir, "file_B.jpg")

            im = Image.new("RGB", (10, 10), "blue")
            im.save(p1)
            im.save(p2)

            conn = sqlite3.connect(self.TEST_DB_PATH)
            self._seed_rename_photo(conn, p1, 2000.0, "2026:06:27 12:00:00", "Rowan Thackeray")
            self._seed_rename_photo(conn, p2, 1000.0, "2026:06:27 11:00:00", "Tamsin Okafor")
            conn.commit()
            conn.close()
            faces_before = self._rows("SELECT COUNT(*) FROM faces")[0][0]

            from metadata import build_photo_ui_record
            TagPupHTTPRequestHandler.folder_cache[paths.key(temp_dir)] = {
                paths.key(p1): build_photo_ui_record(p1, {"path": p1, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"}}, 2000.0, 100),
                paths.key(p2): build_photo_ui_record(p2, {"path": p2, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"}}, 1000.0, 100)
            }

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
            faces = dict(self._rows("SELECT photo_path, name FROM faces WHERE name IS NOT NULL"
                                    " AND photo_path IN (?, ?, ?, ?)",
                                    (p1, p2, expected_p1_new, expected_p2_new)))
            self.assertEqual(faces, {expected_p1_new: "Rowan Thackeray",
                                     expected_p2_new: "Tamsin Okafor"})
            self.assertEqual(self._rows("SELECT COUNT(*) FROM faces")[0][0], faces_before)
            self.assertEqual(data["index_rows_moved"], 2)
            self.assertEqual(data["index_skipped"], [])

        finally:
            shutil.rmtree(temp_dir)

    def test_api_folder_rename_photos_does_not_merge_onto_rows_already_there(self):
        """A stale row at the new name is not merged into: that duplicates faces."""
        import tempfile
        import shutil
        from PIL import Image

        temp_dir = tempfile.mkdtemp()
        try:
            p1 = os.path.join(temp_dir, "file_A.jpg")
            Image.new("RGB", (10, 10), "blue").save(p1)
            stale = os.path.join(temp_dir, "TestGroup - 1.jpg")  # no file, only rows

            conn = sqlite3.connect(self.TEST_DB_PATH)
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
                self._rows("SELECT name FROM faces WHERE photo_path = ?", (stale,)),
                [("Tamsin Okafor",)], "faces were merged onto the rows already there")
            self.assertEqual(
                self._rows("SELECT name FROM faces WHERE photo_path = ?", (p1,)),
                [("Rowan Thackeray",)])
        finally:
            shutil.rmtree(temp_dir)

    def test_api_folder_rename_photos_conflict_resolution(self):
        import tempfile
        import shutil
        from PIL import Image
        import paths

        temp_dir = tempfile.mkdtemp()
        try:
            # Create two selected files
            p1 = os.path.join(temp_dir, "file_A.jpg")
            p2 = os.path.join(temp_dir, "file_B.jpg")
            # Create conflicting file occupant (this one is NOT in our renaming selection)
            p_conflict = os.path.join(temp_dir, "TestGroup - 1.jpg")

            im = Image.new("RGB", (10, 10), "blue")
            im.save(p1)
            im.save(p2)
            im.save(p_conflict)

            # Setup DB record cache
            conn = sqlite3.connect(self.TEST_DB_PATH)
            self._seed_rename_photo(conn, p1, 2000.0, "2026:06:27 12:00:00", "Rowan Thackeray")
            self._seed_rename_photo(conn, p2, 1000.0, "2026:06:27 11:00:00", "Tamsin Okafor")
            self._seed_rename_photo(conn, p_conflict, 500.0, None, "Ellis Marchetti")
            conn.commit()
            conn.close()

            # Pre-populate server cache
            from metadata import build_photo_ui_record
            TagPupHTTPRequestHandler.folder_cache[paths.key(temp_dir)] = {
                paths.key(p1): build_photo_ui_record(p1, {"path": p1, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"}}, 2000.0, 100),
                paths.key(p2): build_photo_ui_record(p2, {"path": p2, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"}}, 1000.0, 100),
                paths.key(p_conflict): build_photo_ui_record(p_conflict, {"path": p_conflict, "raw_metadata": {}}, 500.0, 100)
            }

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
            faces = dict(self._rows("SELECT name, photo_path FROM faces WHERE name IS NOT NULL"))
            self.assertEqual(faces["Ellis Marchetti"], expected_conflict_new)
            self.assertEqual(faces["Tamsin Okafor"], expected_p2_new)
            self.assertEqual(faces["Rowan Thackeray"], expected_p1_new)
            self.assertEqual(data["index_rows_moved"], 3)

        finally:
            shutil.rmtree(temp_dir)

    def test_api_photo_delete(self):
        import tempfile
        import shutil
        import paths
        temp_dir = tempfile.mkdtemp()
        try:
            p = os.path.join(temp_dir, "to_delete.jpg")
            with open(p, "wb") as f:
                f.write(b"fake jpeg content")

            # Seed the database the way the indexer writes it: native paths.
            conn = sqlite3.connect(self.TEST_DB_PATH)
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags) VALUES (?, 1.0, 10, '[]')", (p,))
            c.execute("INSERT OR REPLACE INTO embedding_cache (path, mtime, size, model_name, pretrained, preserve_full_frame, max_aspect_ratio, force_image_size, embedding) VALUES (?, 1.0, 10, 'm', 'p', 0, 1.0, 100, ?)", (p, b'\x00'*512))
            c.execute("INSERT INTO faces (photo_path, box, name, prob) VALUES (?, '[]', 'Rowan Thackeray', 1.0)", (p,))
            conn.commit()
            conn.close()

            # Populate server folder cache
            folder_key = paths.key(temp_dir)
            photo_key = paths.key(p)
            TagPupHTTPRequestHandler.folder_cache[folder_key] = {
                photo_key: {"path": p, "filename": "to_delete.jpg", "tags": []}
            }

            # Send POST delete request
            url = f"http://127.0.0.1:{self.TEST_PORT}/api/photo/delete"
            req = urllib.request.Request(
                url,
                data=json.dumps({"path": p}).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            response = urllib.request.urlopen(req)
            data = json.loads(response.read().decode('utf-8'))

            self.assertTrue(data["success"])

            # Verify file is not on disk (moved to recycle bin)
            self.assertFalse(os.path.exists(p))

            # Verify records are gone from DB
            photos_count = self._rows("SELECT COUNT(*) FROM photos WHERE path = ?", (p,))[0][0]
            cache_count = self._rows("SELECT COUNT(*) FROM embedding_cache WHERE path = ?", (p,))[0][0]
            faces_count = self._rows("SELECT COUNT(*) FROM faces WHERE photo_path = ?", (p,))[0][0]

            self.assertEqual(photos_count, 0)
            self.assertEqual(cache_count, 0)
            self.assertEqual(faces_count, 0)

            # Verify folder_cache entry is evicted
            self.assertNotIn(photo_key, TagPupHTTPRequestHandler.folder_cache[folder_key])

        finally:
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    unittest.main()

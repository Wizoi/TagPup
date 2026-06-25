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

class TestStability(unittest.TestCase):
    TEST_DB_PATH = os.path.join(WORKSPACE_DIR, "data", "test_validation_index.db")
    TEST_PORT = 9898
    server_thread = None

    @classmethod
    def setUpClass(cls):
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
            self.assertTrue(name is None or name == "Non Person", "Faces in Photo 2 must remain unmatched because similarity < 0.80")

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
        self.assertLess(duration, 2.0)
        self.assertIn("faces", res_data)
        self.assertEqual(len(res_data["faces"]), 5000)

if __name__ == "__main__":
    unittest.main()

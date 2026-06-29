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
                       ("C:/photos/automatch_test.jpg", 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/automatch_test.jpg", "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
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
        c.execute("SELECT name FROM faces WHERE photo_path = 'C:/photos/automatch_test.jpg'")
        name = c.fetchone()[0]
        self.assertEqual(name, "John Doe")
        
        # Also verify photo's people field is updated
        c.execute("SELECT people FROM photos WHERE path = 'C:/photos/automatch_test.jpg'")
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
                       ("C:/photos/folderA/photo1.jpg", 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/folderA/photo2.jpg", 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/folderA/john_doe.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        # Insert faces
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/folderA/john_doe.jpg", "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/folderA/photo1.jpg", "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/folderA/photo2.jpg", "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
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
        c.execute("SELECT name FROM faces WHERE photo_path IN ('C:/photos/folderA/photo1.jpg', 'C:/photos/folderA/photo2.jpg')")
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
                       ("C:/photos/duplicate_test.jpg", 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # Insert two unmatched faces on the same photo that both match John Doe
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/duplicate_test.jpg", "[0,0,10,10]", resolved_emb.tobytes(), None, 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/duplicate_test.jpg", "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
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
        c.execute("SELECT name FROM faces WHERE photo_path = 'C:/photos/duplicate_test.jpg'")
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
                       ("C:/photos/already_tagged_test.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/john_doe.jpg", "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        # One face already matched to John Doe, another unmatched but matches John Doe's embedding
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/already_tagged_test.jpg", "[0,0,10,10]", resolved_emb.tobytes(), "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/already_tagged_test.jpg", "[20,20,30,30]", resolved_emb.tobytes(), None, 0.95))
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
        c.execute("SELECT name FROM faces WHERE photo_path = 'C:/photos/already_tagged_test.jpg'")
        names = sorted([str(r[0]) for r in c.fetchall()])
        self.assertEqual(names, ["John Doe", "None"])
        conn.close()

    def test_api_face_match_duplicate_conflict(self):
        # Open database, insert two faces in the same photo
        photo_index = PhotoIndex(db_path=self.TEST_DB_PATH)
        photo_index.load()
        cursor = photo_index.conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_test.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        
        # Face 1 is John Doe, Face 2 is unmatched
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_test.jpg", "[0,0,10,10]", b"", "John Doe", 0.95))
        face1_id = cursor.lastrowid
        
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_test.jpg", "[20,20,30,30]", b"", None, 0.95))
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
                       ("C:/photos/conflict_p1.jpg", 1000.0, 100, "[]", "[\"John Doe\"]", "[]", "{}"))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_p1.jpg", "[0,0,10,10]", b"", "John Doe", 0.95))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_p1.jpg", "[20,20,30,30]", b"", None, 0.95))
        face2_id = cursor.lastrowid
        
        # Photo 2 has another unmatched face
        cursor.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_p2.jpg", 1000.0, 100, "[]", "[]", "[]", "{}"))
        cursor.execute("INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
                       ("C:/photos/conflict_p2.jpg", "[0,0,10,10]", b"", None, 0.95))
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

    def test_api_folder_time_shift(self):
        import tempfile
        import shutil
        from PIL import Image
        import tuner_server
        
        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, "test_shift.jpg")
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(img_path, "JPEG")
        
        tuner_server.TunerHTTPRequestHandler.folder_cache[temp_dir] = {
            img_path: {
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
        from PIL import Image
        from metadata import rotate_image_file
        
        temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(temp_dir, "test_rotate.jpg")
        
        # Create an asymmetrical image: 10 wide, 20 high
        img = Image.new("RGB", (10, 20), color="red")
        img.save(img_path, "JPEG")
        
        # Rotate left (counter-clockwise) -> should become 20 wide, 10 high
        rotate_image_file(img_path, "left")
        with Image.open(img_path) as rotated:
            self.assertEqual(rotated.size, (20, 10), "Rotating left should swap dimensions")
            
        # Rotate right (clockwise) -> should become 10 wide, 20 high again
        rotate_image_file(img_path, "right")
        with Image.open(img_path) as rotated:
            self.assertEqual(rotated.size, (10, 20), "Rotating right should swap dimensions back")
            
        shutil.rmtree(temp_dir)

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

    def test_api_folder_rename_photos(self):
        import tempfile
        import shutil
        from PIL import Image
        
        temp_dir = tempfile.mkdtemp().replace("\\", "/")
        try:
            p1 = os.path.join(temp_dir, "file_A.jpg").replace("\\", "/")
            p2 = os.path.join(temp_dir, "file_B.jpg").replace("\\", "/")
            
            im = Image.new("RGB", (10, 10), "blue")
            im.save(p1)
            im.save(p2)
            
            conn = sqlite3.connect(self.TEST_DB_PATH)
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (p1, 2000.0, 100, "[]", "[]", "[]", json.dumps({"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"})))
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (p2, 1000.0, 100, "[]", "[]", "[]", json.dumps({"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"})))
            conn.commit()
            conn.close()
            
            from metadata import build_photo_ui_record
            TunerHTTPRequestHandler.folder_cache[temp_dir] = {
                p1: build_photo_ui_record(p1, {"path": p1, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"}}, 2000.0, 100),
                p2: build_photo_ui_record(p2, {"path": p2, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"}}, 1000.0, 100)
            }
            
            url = f"http://127.0.0.1:{self.TEST_PORT}/api/folder/rename-photos"
            req = urllib.request.Request(
                url,
                data=json.dumps({
                    "folder_path": temp_dir,
                    "photo_paths": [p1, p2],
                    "grouping": "TestGroup"
                }).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            response = urllib.request.urlopen(req)
            data = json.loads(response.read().decode('utf-8'))
            
            self.assertTrue(data["success"])
            
            expected_p2_new = os.path.join(temp_dir, "TestGroup - 1.jpg").replace("\\", "/")
            expected_p1_new = os.path.join(temp_dir, "TestGroup - 2.jpg").replace("\\", "/")
            
            self.assertTrue(os.path.exists(expected_p2_new))
            self.assertTrue(os.path.exists(expected_p1_new))
            self.assertFalse(os.path.exists(p1))
            self.assertFalse(os.path.exists(p2))
            
            conn = sqlite3.connect(self.TEST_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT path FROM photos")
            db_paths = [r[0].replace("\\", "/") for r in c.fetchall()]
            conn.close()
            
            self.assertIn(expected_p1_new, db_paths)
            self.assertIn(expected_p2_new, db_paths)
            
        finally:
            shutil.rmtree(temp_dir)

    def test_api_folder_rename_photos_conflict_resolution(self):
        import tempfile
        import shutil
        from PIL import Image
        
        temp_dir = tempfile.mkdtemp().replace("\\", "/")
        try:
            # Create two selected files
            p1 = os.path.join(temp_dir, "file_A.jpg").replace("\\", "/")
            p2 = os.path.join(temp_dir, "file_B.jpg").replace("\\", "/")
            # Create conflicting file occupant (this one is NOT in our renaming selection)
            p_conflict = os.path.join(temp_dir, "TestGroup - 1.jpg").replace("\\", "/")
            
            im = Image.new("RGB", (10, 10), "blue")
            im.save(p1)
            im.save(p2)
            im.save(p_conflict)
            
            # Setup DB record cache
            conn = sqlite3.connect(self.TEST_DB_PATH)
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (p1, 2000.0, 100, "[]", "[]", "[]", json.dumps({"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"})))
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (p2, 1000.0, 100, "[]", "[]", "[]", json.dumps({"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"})))
            c.execute("INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (p_conflict, 500.0, 100, "[]", "[]", "[]", json.dumps({})))
            conn.commit()
            conn.close()
            
            # Pre-populate server cache
            from metadata import build_photo_ui_record
            TunerHTTPRequestHandler.folder_cache[temp_dir] = {
                p1: build_photo_ui_record(p1, {"path": p1, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 12:00:00"}}, 2000.0, 100),
                p2: build_photo_ui_record(p2, {"path": p2, "raw_metadata": {"EXIF:DateTimeOriginal": "2026:06:27 11:00:00"}}, 1000.0, 100),
                p_conflict: build_photo_ui_record(p_conflict, {"path": p_conflict, "raw_metadata": {}}, 500.0, 100)
            }
            
            # Trigger renaming POST request
            url = f"http://127.0.0.1:{self.TEST_PORT}/api/folder/rename-photos"
            req = urllib.request.Request(
                url,
                data=json.dumps({
                    "folder_path": temp_dir,
                    "photo_paths": [p1, p2],
                    "grouping": "TestGroup"
                }).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            response = urllib.request.urlopen(req)
            data = json.loads(response.read().decode('utf-8'))
            
            self.assertTrue(data["success"])
            
            # Check targets were successfully created
            expected_p2_new = os.path.join(temp_dir, "TestGroup - 1.jpg").replace("\\", "/")
            expected_p1_new = os.path.join(temp_dir, "TestGroup - 2.jpg").replace("\\", "/")
            expected_conflict_new = os.path.join(temp_dir, "TestGroup - 1_conflict_1.jpg").replace("\\", "/")
            
            self.assertTrue(os.path.exists(expected_p2_new), f"Should have created {expected_p2_new}")
            self.assertTrue(os.path.exists(expected_p1_new), f"Should have created {expected_p1_new}")
            self.assertTrue(os.path.exists(expected_conflict_new), f"Should have moved conflicting occupant to {expected_conflict_new}")
            
            # Verify original selected files and old conflict files are gone from their old paths
            self.assertFalse(os.path.exists(p1))
            self.assertFalse(os.path.exists(p2))
            # Note that p_conflict old path was occupied by expected_p2_new, so the old path now has the new file content.
            
            # Verify DB paths
            conn = sqlite3.connect(self.TEST_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT path FROM photos")
            db_paths = [r[0].replace("\\", "/") for r in c.fetchall()]
            conn.close()
            
            self.assertIn(expected_p1_new, db_paths)
            self.assertIn(expected_p2_new, db_paths)
            self.assertIn(expected_conflict_new, db_paths)
            
        finally:
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    unittest.main()

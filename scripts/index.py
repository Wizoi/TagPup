# index.py
import os
import json
import sqlite3
import logging
import hashlib
from typing import List, Dict, Any, Tuple, Optional, Set
import numpy as np
import faiss

logger = logging.getLogger("tagpup.index")

class PathLocker:
    def __init__(self, lock_dir: str = "data/locks"):
        self.lock_dir = lock_dir
        os.makedirs(self.lock_dir, exist_ok=True)
        self.locked_paths = set()

    def _get_lock_path(self, path: str) -> str:
        abs_path = os.path.abspath(path)
        path_hash = hashlib.md5(abs_path.encode('utf-8')).hexdigest()
        return os.path.join(self.lock_dir, f"{path_hash}.lock")

    def acquire(self, path: str) -> bool:
        """Try to acquire a lock for a specific photo path. Returns True if acquired, False if already locked."""
        lock_file = self._get_lock_path(path)
        try:
            # Exclusive file creation serves as an atomic lock
            with open(lock_file, "x", encoding="utf-8") as f:
                f.write(os.path.abspath(path))
            self.locked_paths.add(path)
            return True
        except FileExistsError:
            return False
        except Exception as e:
            logger.warning(f"Failed to create lock for {path}: {e}")
            return False

    def release(self, path: str):
        """Release the lock for a specific photo path."""
        lock_file = self._get_lock_path(path)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.warning(f"Failed to remove lock for {path}: {e}")
        self.locked_paths.discard(path)

    def release_all(self):
        """Release all locks held by this process instance."""
        for path in list(self.locked_paths):
            self.release(path)

class PhotoIndex:
    def __init__(self, db_path: str = "data/photo_index.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []
        self.dim = 512  # Default

    def _create_table(self):
        """Create the schema table if it does not exist."""
        if self.conn is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                path TEXT PRIMARY KEY,
                mtime REAL,
                size INTEGER,
                tags TEXT,
                people TEXT,
                captions TEXT,
                raw_metadata TEXT,
                embedding BLOB
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_path TEXT,
                box TEXT,
                embedding BLOB,
                name TEXT,
                crop_image BLOB,
                prob REAL,
                FOREIGN KEY(photo_path) REFERENCES photos(path) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                path TEXT PRIMARY KEY,
                mtime REAL,
                size INTEGER,
                model_name TEXT,
                pretrained TEXT,
                preserve_full_frame INTEGER,
                max_aspect_ratio REAL,
                force_image_size INTEGER,
                embedding BLOB
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_faces_photo_path ON faces(photo_path)
        """)
        self.conn.commit()

    def load(self) -> bool:
        """Connect to SQLite database and build in-memory FAISS index."""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
                
            # Set a 30-second timeout to handle concurrent lock waiting gracefully
            self.conn = sqlite3.connect(self.db_path, timeout=30.0)
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self._create_table()
            
            # Dynamic migration: add crop_image and prob columns to faces table if they don't exist
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(faces)")
            columns = [info[1] for info in cursor.fetchall()]
            if "crop_image" not in columns:
                logger.info("Migrating faces table: Adding crop_image column...")
                cursor.execute("ALTER TABLE faces ADD COLUMN crop_image BLOB")
                self.conn.commit()
            if "prob" not in columns:
                logger.info("Migrating faces table: Adding prob column...")
                cursor.execute("ALTER TABLE faces ADD COLUMN prob REAL")
                self.conn.commit()
            
            cursor.execute("SELECT path, mtime, size, tags, people, captions, raw_metadata, embedding FROM photos")
            rows = cursor.fetchall()
            
            self.metadata = []
            embeddings = []
            
            for path, mtime, size, tags_json, people_json, captions_json, raw_meta_json, emb_bytes in rows:
                try:
                    tags = json.loads(tags_json)
                    people = json.loads(people_json)
                    captions = json.loads(captions_json)
                    raw_meta = json.loads(raw_meta_json)
                except Exception:
                    tags, people, captions, raw_meta = [], [], [], {}
                    
                self.metadata.append({
                    "path": path,
                    "mtime": mtime,
                    "size": size,
                    "tags": tags,
                    "people": people,
                    "captions": captions,
                    "raw_metadata": raw_meta
                })
                
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                embeddings.append(emb)
                
            if embeddings:
                self.dim = len(embeddings[0])
                embedding_matrix = np.array(embeddings, dtype=np.float32)
                
                # L2 normalize vectors to guarantee accurate cosine similarity via FlatIP
                norms = np.linalg.norm(embedding_matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                embedding_matrix = embedding_matrix / norms
                
                self.index = faiss.IndexFlatIP(self.dim)
                self.index.add(embedding_matrix)
                logger.info(f"Loaded {len(self.metadata)} index entries from SQLite.")
            else:
                self.index = None
                
            return True
        except Exception as e:
            logger.error(f"Error loading SQLite database: {e}", exc_info=True)
            self.index = None
            self.metadata = []
            return False

    def build_or_update(self, embeddings: List[List[float]], metas: List[Dict[str, Any]], dim: int = 512, reload: bool = True):
        """Batch insert/update photos inside the SQLite database (transaction-safe)."""
        if not embeddings or self.conn is None:
            return

        try:
            cursor = self.conn.cursor()
            for meta, emb in zip(metas, embeddings):
                emb_bytes = np.array(emb, dtype=np.float32).tobytes()
                cursor.execute("""
                    INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    meta["path"],
                    meta.get("mtime", 0.0),
                    meta.get("size", 0),
                    json.dumps(meta.get("tags", [])),
                    json.dumps(meta.get("people", [])),
                    json.dumps(meta.get("captions", [])),
                    json.dumps(meta.get("raw_metadata", {})),
                    emb_bytes
                ))
            self.conn.commit()
            
            if reload:
                # Reload to rebuild the in-memory FAISS index to reflect the updates
                self.load()
        except Exception as e:
            logger.error(f"Error saving batch to SQLite: {e}")
            self.conn.rollback()
            raise e

    def save(self):
        """No-op because SQLite changes are committed incrementally. Kept for signature compatibility."""
        pass

    def search(self, query_vector: List[float], k: int = 15) -> List[Tuple[float, Dict[str, Any]]]:
        """Search the in-memory FAISS index for the k most similar vectors."""
        if self.index is None or self.index.ntotal == 0:
            return []

        query_np = np.array([query_vector], dtype=np.float32)
        norm = np.linalg.norm(query_np)
        if norm > 0:
            query_np = query_np / norm

        k = min(k, self.index.ntotal)
        if k == 0:
            return []

        scores, indices = self.index.search(query_np, k)
        
        results = []
        for sim, idx in zip(scores[0], indices[0]):
            if idx == -1 or idx >= len(self.metadata):
                continue
            results.append((float(sim), self.metadata[idx]))
            
        return results

    def remove_paths(self, paths_to_remove: Set[str]):
        """Remove specific paths from the SQLite database and reload."""
        if self.conn is None or not paths_to_remove:
            return

        try:
            cursor = self.conn.cursor()
            # SQLite deletes in chunks or individual queries
            for path in paths_to_remove:
                cursor.execute("DELETE FROM photos WHERE path = ?", (path,))
            self.conn.commit()
            # Rebuild in-memory index
            self.load()
        except Exception as e:
            logger.error(f"Error deleting paths from SQLite: {e}")
            self.conn.rollback()
            raise e

    def close(self):
        """Close SQLite connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def remove_faces_for_path(self, photo_path: str):
        """Delete all faces associated with a photo path."""
        if self.conn is None:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM faces WHERE photo_path = ?", (photo_path,))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error removing faces for {photo_path}: {e}")
            self.conn.rollback()

    def save_faces_for_path(self, photo_path: str, faces: List[Dict[str, Any]]):
        """Save a list of detected faces for a photo. 
        Each face dict has keys: 'box' (list of float/int), 'embedding' (list of floats)."""
        if self.conn is None:
            return
        try:
            cursor = self.conn.cursor()
            # First clean up old face records for this photo
            cursor.execute("DELETE FROM faces WHERE photo_path = ?", (photo_path,))
            
            for face in faces:
                box_json = json.dumps(face["box"])
                emb_bytes = np.array(face["embedding"], dtype=np.float32).tobytes()
                crop_bytes = face.get("crop_image")
                cursor.execute("""
                    INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (photo_path, box_json, emb_bytes, face.get("name"), crop_bytes, face.get("prob")))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving faces for {photo_path}: {e}")
            self.conn.rollback()

    def get_all_faces(self) -> List[Dict[str, Any]]:
        """Retrieve all indexed face coordinates and embeddings from the DB."""
        if self.conn is None:
            return []
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, photo_path, box, embedding, name, prob FROM faces")
            rows = cursor.fetchall()
            
            faces = []
            for face_id, photo_path, box_json, emb_bytes, name, prob in rows:
                box = json.loads(box_json)
                embedding = np.frombuffer(emb_bytes, dtype=np.float32)
                faces.append({
                    "id": face_id,
                    "photo_path": photo_path,
                    "box": box,
                    "embedding": embedding,
                    "name": name,
                    "prob": prob
                })
            return faces
        except Exception as e:
            logger.error(f"Error retrieving faces: {e}")
            return []

    def save_face_names(self, face_updates: List[Tuple[Optional[str], int]]):
        """Batch update the resolved names of faces by their record ID."""
        if self.conn is None or not face_updates:
            return
        try:
            cursor = self.conn.cursor()
            cursor.executemany("UPDATE faces SET name = ? WHERE id = ?", face_updates)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error updating face names: {e}")
            self.conn.rollback()
            raise e

    def reset_face_assignments(self):
        """Reset all face name assignments in faces table and restore original people metadata in database."""
        if self.conn is None:
            return False
        try:
            cursor = self.conn.cursor()
            # 1. Reset faces name
            cursor.execute("UPDATE faces SET name = NULL")
            
            # 2. Reset photos.people to original tags extracted from raw_metadata
            cursor.execute("SELECT path, raw_metadata, tags FROM photos")
            rows = cursor.fetchall()
            
            from metadata import extract_people
            
            updates = []
            for path, raw_meta_json, tags_json in rows:
                try:
                    raw_meta = json.loads(raw_meta_json) if raw_meta_json else {}
                    tags = json.loads(tags_json) if tags_json else []
                except Exception:
                    raw_meta = {}
                    tags = []
                orig_people = extract_people(raw_meta, tags)
                updates.append((json.dumps(orig_people), path))
                
            if updates:
                cursor.executemany("UPDATE photos SET people = ? WHERE path = ?", updates)
                
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error resetting face assignments in database: {e}")
            if self.conn:
                self.conn.rollback()
            raise e


    def save_faces_batch(self, batch_faces: Dict[str, List[Dict[str, Any]]]):
        """Save detected faces for a batch of photos in a single transaction."""
        if self.conn is None or not batch_faces:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            for photo_path, faces in batch_faces.items():
                cursor.execute("DELETE FROM faces WHERE photo_path = ?", (photo_path,))
                for face in faces:
                    box_json = json.dumps(face["box"])
                    emb_bytes = np.array(face["embedding"], dtype=np.float32).tobytes()
                    crop_bytes = face.get("crop_image")
                    cursor.execute("""
                        INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (photo_path, box_json, emb_bytes, face.get("name"), crop_bytes, face.get("prob")))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error saving faces batch to SQLite: {e}")
            self.conn.rollback()
            raise e

    def migrate_disk_cache_to_sqlite(self, cache_dir: str):
        """Read existing cache .json files, insert them into embedding_cache table, and delete disk files."""
        if self.conn is None or not os.path.exists(cache_dir):
            return

        try:
            json_files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        except Exception as e:
            logger.warning(f"Failed to scan cache directory '{cache_dir}': {e}")
            return

        if not json_files:
            return

        logger.info(f"Found {len(json_files)} cache files in '{cache_dir}'. Starting database migration...")
        
        batch_size = 1000
        cursor = self.conn.cursor()
        
        for idx in range(0, len(json_files), batch_size):
            batch = json_files[idx:idx + batch_size]
            migrated_files = []
            
            try:
                cursor.execute("BEGIN TRANSACTION")
                for filename in batch:
                    filepath = os.path.join(cache_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            
                        # Extract and validate fields
                        path = data.get("path")
                        mtime = data.get("mtime")
                        size = data.get("size")
                        model_name = data.get("model_name")
                        pretrained = data.get("pretrained")
                        preserve_full_frame = 1 if data.get("preserve_full_frame") else 0
                        max_aspect_ratio = data.get("max_aspect_ratio")
                        force_image_size = data.get("force_image_size")
                        embedding = data.get("embedding")
                        
                        if path and embedding:
                            emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                            cursor.execute("""
                                INSERT OR REPLACE INTO embedding_cache (
                                    path, mtime, size, model_name, pretrained, 
                                    preserve_full_frame, max_aspect_ratio, force_image_size, embedding
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                path, mtime, size, model_name, pretrained,
                                preserve_full_frame, max_aspect_ratio, force_image_size, emb_bytes
                            ))
                            migrated_files.append(filepath)
                    except Exception as e:
                        # Log error and clean up corrupt file to avoid blocking future migrations
                        logger.warning(f"Corrupt or invalid cache file {filename}: {e}. Removing file.")
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
                
                self.conn.commit()
                
                # Delete files from disk only after successful DB commit
                for filepath in migrated_files:
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        logger.warning(f"Failed to delete migrated cache file {filepath}: {e}")
                        
                logger.info(f"Successfully migrated and cleaned up {len(migrated_files)} cache files.")
            except Exception as e:
                logger.error(f"Failed to migrate batch of cache files: {e}")
                self.conn.rollback()



# tuner_server.py
import os
import json
import sqlite3
import urllib.parse
import io
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingTCPServer
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
import numpy as np

logger = logging.getLogger("tagtuner.server")

import re

YEAR_RE = re.compile(r"^(\d{4})")
DATE_KEYS = [
    "EXIF:DateTimeOriginal", "DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate",
    "XMP:CreateDate",
    "EXIF:ModifyDate", "ModifyDate",
    "XMP:ModifyDate"
]

_year_cache = {}

def parse_year_from_raw_metadata(raw_meta):
    if not raw_meta:
        return None
    for key in DATE_KEYS:
        val = raw_meta.get(key)
        if val:
            if isinstance(val, list) and val:
                val = val[0]
            val_str = str(val).strip()
            match = YEAR_RE.match(val_str)
            if match:
                try:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        return year
                except ValueError:
                    pass
    return None

def compute_geometric_median(X, eps=1e-5, max_iter=20):
    if len(X) == 0:
        return None
    if len(X) <= 2:
        return np.mean(X, axis=0)
    y = np.mean(X, axis=0)
    for _ in range(max_iter):
        distances = np.linalg.norm(X - y, axis=1)
        zero_mask = distances < 1e-10
        if np.any(zero_mask):
            distances = np.where(zero_mask, 1e-10, distances)
        weights = 1.0 / distances
        weights_sum = np.sum(weights)
        next_y = np.sum(X * weights[:, np.newaxis], axis=0) / weights_sum
        if np.linalg.norm(next_y - y) < eps:
            break
        y = next_y
    return y

_metadata_year_cache = {}

def get_year_from_mtime_or_meta(mtime, raw_meta_json):
    parsed_year = None
    if raw_meta_json:
        if raw_meta_json in _metadata_year_cache:
            parsed_year = _metadata_year_cache[raw_meta_json]
        else:
            try:
                if isinstance(raw_meta_json, str):
                    raw_meta = json.loads(raw_meta_json)
                else:
                    raw_meta = raw_meta_json
                parsed_year = parse_year_from_raw_metadata(raw_meta)
                _metadata_year_cache[raw_meta_json] = parsed_year
            except Exception:
                pass
                
    if not parsed_year and mtime:
        try:
            import time
            parsed_year = time.gmtime(mtime).tm_year
        except Exception:
            try:
                import datetime
                parsed_year = datetime.datetime.fromtimestamp(mtime).year
            except Exception:
                pass
    return parsed_year if parsed_year else "Unknown"

class TunerHTTPRequestHandler(BaseHTTPRequestHandler):
    db_path = "data/photo_index.db"
    gui_dir = "gui"
    clustering_in_progress = False

    def log_message(self, format, *args):
        # Suppress request spam logging in console unless error
        pass

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Static files serving
        if path == "/" or path == "/index.html":
            self.serve_static_file("index.html", "text/html")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css")
        elif path == "/app.js":
            self.serve_static_file("app.js", "application/javascript")
            
        # API: get list of photos with unmatched faces
        elif path == "/api/photos":
            self.handle_get_photos(query)
            
        # API: get details of a specific photo
        elif path == "/api/photo-details":
            self.handle_get_photo_details(query)
            
        # API: serve original photo file
        elif path == "/api/photo-file":
            self.handle_serve_photo_file(query)
            
        # API: serve cropped face image on-the-fly
        elif path == "/api/face-crop":
            self.handle_serve_face_crop(query)

        # API: get list of all known people
        elif path == "/api/people":
            self.handle_get_people()

        # API: get list of all known people with face counts
        elif path == "/api/people-with-counts":
            self.handle_get_people_with_counts()

        # API: get faces for a specific name
        elif path == "/api/person-faces":
            self.handle_get_person_faces(query)

        # API: get top 5 similar matched faces for a selected face
        elif path == "/api/face-matches":
            self.handle_get_face_matches(query)
            
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
        if TunerHTTPRequestHandler.clustering_in_progress:
            self.send_json_error(409, "Server is currently clustering faces. Please try again later.")
            return

        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/face/match":
            self.handle_post_match()
        elif path == "/api/face/unmatch":
            self.handle_post_unmatch()
        elif path == "/api/faces/unmatch-bulk":
            self.handle_post_unmatch_bulk()
        elif path == "/api/faces/match-bulk":
            self.handle_post_match_bulk()
        elif path == "/api/faces/recluster":
            self.handle_post_recluster()
        elif path == "/api/photo/unmatch-all":
            self.handle_post_unmatch_all()
        elif path == "/api/photo/automatch":
            self.handle_post_automatch()
        else:
            self.send_error(404, "Endpoint Not Found")

    def serve_static_file(self, filename, content_type):
        filepath = os.path.join(self.gui_dir, filename)
        if not os.path.exists(filepath):
            self.send_error(404, f"File {filename} not found")
            return

        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal server error: {e}")

    def handle_get_photos(self, query):
        mode = query.get("mode", ["unmatched"])[0]
        hide_notperson = query.get("hide_notperson", ["false"])[0].lower() == "true"

        if not os.path.exists(self.db_path):
            self.send_json([])
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            if mode == "unmatched":
                if hide_notperson:
                    cursor.execute("""
                        SELECT f.photo_path, COUNT(*), p.mtime, p.raw_metadata
                        FROM faces f
                        LEFT JOIN photos p ON p.path = f.photo_path
                        WHERE f.name IS NULL
                        GROUP BY f.photo_path
                        ORDER BY p.mtime DESC
                    """)
                else:
                    cursor.execute("""
                        SELECT f.photo_path, COUNT(*), p.mtime, p.raw_metadata
                        FROM faces f
                        LEFT JOIN photos p ON p.path = f.photo_path
                        WHERE f.name IS NULL OR f.name = 'Non Person'
                        GROUP BY f.photo_path
                        ORDER BY p.mtime DESC
                    """)
                rows = cursor.fetchall()
                photos = []
                for row in rows:
                    p_path = row[0]
                    unmatched_count = row[1]
                    mtime = row[2] if row[2] is not None else 0.0
                    raw_meta_json = row[3]
                    
                    year = get_year_from_mtime_or_meta(mtime, raw_meta_json)
                    
                    photos.append({
                        "path": p_path,
                        "filename": os.path.basename(p_path),
                        "unmatched_count": unmatched_count,
                        "mtime": mtime,
                        "year": year,
                        "folder": os.path.dirname(p_path)
                    })
                self.send_json(photos)
            else:
                self.send_json([])
        except Exception as e:
            logger.error(f"Error fetching photos: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_photo_details(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return

        photo_path = urllib.parse.unquote(photo_path_list[0])

        if not os.path.exists(self.db_path):
            self.send_error(404, "Database not found")
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch metadata from photos table
            cursor.execute("SELECT people, tags, captions FROM photos WHERE path = ?", (photo_path,))
            photo_row = cursor.fetchone()
            
            # Fallback for Windows path casing mismatches
            if not photo_row:
                cursor.execute("SELECT people, tags, captions FROM photos WHERE path LIKE ?", (photo_path,))
                photo_row = cursor.fetchone()

            people = []
            tags = []
            caption = None

            if photo_row:
                try:
                    people = json.loads(photo_row[0]) if photo_row[0] else []
                except Exception:
                    people = []
                try:
                    tags = json.loads(photo_row[1]) if photo_row[1] else []
                except Exception:
                    tags = []
                try:
                    captions = json.loads(photo_row[2]) if photo_row[2] else []
                    caption = captions[0] if captions else None
                except Exception:
                    caption = None

            # 2. Fetch face detections from faces table
            cursor.execute("SELECT id, box, name FROM faces WHERE photo_path = ?", (photo_path,))
            face_rows = cursor.fetchall()
            
            # Fallback casing mismatch
            if not face_rows:
                cursor.execute("SELECT id, box, name FROM faces WHERE photo_path LIKE ?", (photo_path,))
                face_rows = cursor.fetchall()

            faces = []
            for f_row in face_rows:
                fid = f_row[0]
                box_str = f_row[1]
                fname = f_row[2]
                
                try:
                    box = json.loads(box_str)
                except Exception:
                    # Clean brackets and split if stored directly
                    try:
                        box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                    except Exception:
                        box = [0, 0, 0, 0]
                        
                faces.append({
                    "id": fid,
                    "box": box,
                    "name": fname
                })

            # Compile details response
            details = {
                "path": photo_path,
                "filename": os.path.basename(photo_path),
                "people": people,
                "tags": tags,
                "caption": caption,
                "faces": faces
            }
            self.send_json(details)

        except Exception as e:
            logger.error(f"Error fetching photo details for {photo_path}: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_serve_photo_file(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return

        photo_path = urllib.parse.unquote(photo_path_list[0])

        if not os.path.exists(photo_path):
            self.send_error(404, f"Photo file not found: {photo_path}")
            return

        try:
            # Check if size parameter is present to resize dynamically and speed up loading
            size_param = query.get("size")
            content_type = "image/jpeg"
            
            if size_param:
                try:
                    max_size = int(size_param[0])
                    with Image.open(photo_path) as img:
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        
                        # Handle Pillow version compatibility for resampling filter
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            try:
                                resample = Image.LANCZOS
                            except AttributeError:
                                resample = Image.ANTIALIAS
                                
                        img.thumbnail((max_size, max_size), resample)
                        buffer = io.BytesIO()
                        img.save(buffer, format="JPEG", quality=85)
                        content = buffer.getvalue()
                except Exception as resize_err:
                    logger.warning(f"Failed to resize image {photo_path}: {resize_err}. Falling back to original.")
                    with open(photo_path, "rb") as f:
                        content = f.read()
                    ext = os.path.splitext(photo_path)[1].lower()
                    if ext == ".png":
                        content_type = "image/png"
                    elif ext == ".webp":
                        content_type = "image/webp"
            else:
                # Guess content type based on extension
                ext = os.path.splitext(photo_path)[1].lower()
                if ext == ".png":
                    content_type = "image/png"
                elif ext == ".webp":
                    content_type = "image/webp"
                
                with open(photo_path, "rb") as f:
                    content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error(f"Error serving file {photo_path}: {e}")
            self.send_error(500, f"Error serving file: {e}")

    def handle_serve_face_crop(self, query):
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return

        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return

        if not os.path.exists(self.db_path):
            self.send_error(404, "Database not found")
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute("SELECT photo_path, box, crop_image FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()

            if not row:
                self.send_error(404, f"Face ID {face_id} not found in DB")
                return

            photo_path = row[0]
            box_str = row[1]
            crop_image = row[2]

            if crop_image is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(crop_image)))
                self.end_headers()
                self.wfile.write(crop_image)
                return

            # Fallback if crop_image is None (older records)
            if not os.path.exists(photo_path):
                self.send_error(404, f"Original photo file not found: {photo_path}")
                return

            try:
                box = json.loads(box_str)
            except Exception:
                try:
                    box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                except Exception:
                    self.send_error(500, "Invalid bounding box format stored in DB")
                    return

            x1, y1, x2, y2 = box
            
            # Crop image on the fly using PIL
            with Image.open(photo_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                width, height = img.size
                
                # Clamp coordinates to safety
                x1, y1 = max(0, int(x1)), max(0, int(y1))
                x2, y2 = min(width, int(x2)), min(height, int(y2))
                
                if (x2 - x1) <= 0 or (y2 - y1) <= 0:
                    # Return a fallback empty thumbnail if box coordinates are corrupt
                    crop_img = Image.new("RGB", (100, 100), color=(50, 50, 50))
                else:
                    crop_img = img.crop((x1, y1, x2, y2))
                
                # Downscale to max 256px if larger to match faces.py behavior
                if max(crop_img.size) > 256:
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        try:
                            resample = Image.LANCZOS
                        except AttributeError:
                            resample = Image.ANTIALIAS
                    crop_img.thumbnail((256, 256), resample)
                
                # Save crop to in-memory buffer
                buffer = io.BytesIO()
                crop_img.save(buffer, format="JPEG", quality=90)
                crop_bytes = buffer.getvalue()

            # Cache the crop image back into the DB
            try:
                cursor.execute("UPDATE faces SET crop_image = ? WHERE id = ?", (sqlite3.Binary(crop_bytes), face_id))
                conn.commit()
            except Exception as cache_err:
                logger.warning(f"Failed to cache face crop in database for face ID {face_id}: {cache_err}")

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(crop_bytes)))
            self.end_headers()
            self.wfile.write(crop_bytes)

        except Exception as e:
            logger.error(f"Error serving face crop for ID {face_id}: {e}")
            self.send_error(500, f"Error cropping face: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_people(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Get people from faces table
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")
            faces_names = {row[0] for row in cursor.fetchall()}
            
            # Get people from photos table
            cursor.execute("SELECT people FROM photos")
            photos_people = set()
            for row in cursor.fetchall():
                if row[0]:
                    try:
                        names = json.loads(row[0])
                        for name in names:
                            if name:
                                photos_people.add(name)
                    except Exception:
                        pass
                        
            all_people = sorted([p for p in faces_names.union(photos_people) if p != "Non Person"])
            self.send_json(all_people)
        except Exception as e:
            logger.error(f"Error fetching people: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_face_matches(self, query):
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except ValueError:
            self.send_error(400, "Invalid 'id' parameter")
            return

        if not os.path.exists(self.db_path):
            self.send_json([])
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            
            # Fetch target embedding
            cursor.execute("SELECT embedding, name FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()
            if not row:
                self.send_error(404, "Face not found")
                return
                
            target_emb_bytes = row[0]
            target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
            
            # Fetch all resolved faces (excluding the current face if it's already resolved, and excluding 'Non Person')
            cursor.execute("SELECT name, embedding FROM faces WHERE name IS NOT NULL AND name != 'Non Person' AND id != ?", (face_id,))
            faces_rows = cursor.fetchall()
            
            if not faces_rows:
                self.send_json([])
                return
                
            names = []
            embeddings_list = []
            for name, emb_bytes in faces_rows:
                names.append(name)
                embeddings_list.append(np.frombuffer(emb_bytes, dtype=np.float32))
                
            embeddings_matrix = np.array(embeddings_list, dtype=np.float32)
            
            # Calculate similarities (dot product since they are L2 normalized)
            similarities = np.dot(embeddings_matrix, target_emb)
            
            # Sort indices descending
            sorted_indices = np.argsort(similarities)[::-1]
            
            # Extract top 5 unique names with similarity scores
            top_matches = []
            for idx in sorted_indices:
                name = names[idx]
                if name not in [m["name"] for m in top_matches]:
                    top_matches.append({
                        "name": name,
                        "similarity": float(similarities[idx])
                    })
                    if len(top_matches) >= 5:
                        break
                        
            self.send_json(top_matches)
            
        except Exception as e:
            logger.error(f"Error finding face matches: {e}")
            self.send_error(500, f"Error finding matches: {e}")
        finally:
            if conn:
                conn.close()

    def read_json_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        return json.loads(body.decode('utf-8'))

    def handle_post_match(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_id = data.get("face_id")
            person_name = data.get("person_name")
            
            if face_id is None or not person_name:
                self.send_error(400, "Missing face_id or person_name")
                return
                
            try:
                face_id = int(face_id)
                person_name = str(person_name).strip()
            except (ValueError, TypeError):
                self.send_error(400, "Invalid parameters")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                self.send_error(404, "Face ID not found")
                return
                
            photo_path, old_name = face_row
            
            # If name is unchanged, just return success
            if old_name == person_name:
                self.send_json({"success": True})
                return

            # 2. Update faces table
            cursor.execute("UPDATE faces SET name = ? WHERE id = ?", (person_name, face_id))

            # 3. Update photos table people list
            cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
            photo_row = cursor.fetchone()
            
            # Fallback for Windows path casing mismatches
            if not photo_row:
                cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                photo_row = cursor.fetchone()
                
            actual_photo_path = photo_path
            people = []
            if photo_row:
                actual_photo_path = photo_row[0]
                if photo_row[1]:
                    try:
                        people = json.loads(photo_row[1])
                    except Exception:
                        people = []

            # Append the new person name if missing
            if person_name not in people and person_name != "Non Person":
                people.append(person_name)

            # Check if old name is no longer matched to any other faces in the photo
            if old_name and old_name != person_name:
                cursor.execute("SELECT count(*) FROM faces WHERE photo_path = ? AND name = ? AND id != ?", (photo_path, old_name, face_id))
                count_row = cursor.fetchone()
                if not count_row:
                    cursor.execute("SELECT count(*) FROM faces WHERE photo_path LIKE ? AND name = ? AND id != ?", (photo_path, old_name, face_id))
                    count_row = cursor.fetchone()
                
                other_count = count_row[0] if count_row else 0
                if other_count == 0:
                    # Remove old name from people list
                    people = [p for p in people if p != old_name]

            # Save the updated people list
            people_json = json.dumps(people)
            cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (people_json, actual_photo_path))

            conn.commit()
            self.send_json({"success": True})
            
        except Exception as e:
            logger.error(f"Error in handle_post_match: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_unmatch(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_id = data.get("face_id")
            
            if face_id is None:
                self.send_error(400, "Missing face_id")
                return
                
            try:
                face_id = int(face_id)
            except (ValueError, TypeError):
                self.send_error(400, "Invalid face_id")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                self.send_error(404, "Face ID not found")
                return
                
            photo_path, old_name = face_row
            
            if old_name is None:
                # Already unmatched
                self.send_json({"success": True})
                return

            # 2. Update faces table
            cursor.execute("UPDATE faces SET name = NULL WHERE id = ?", (face_id,))

            # 3. Check if old name is no longer matched to any other faces in the photo
            cursor.execute("SELECT count(*) FROM faces WHERE photo_path = ? AND name = ? AND id != ?", (photo_path, old_name, face_id))
            count_row = cursor.fetchone()
            if not count_row:
                cursor.execute("SELECT count(*) FROM faces WHERE photo_path LIKE ? AND name = ? AND id != ?", (photo_path, old_name, face_id))
                count_row = cursor.fetchone()
                
            other_count = count_row[0] if count_row else 0
            if other_count == 0:
                # Remove old name from people list in photos table
                cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
                photo_row = cursor.fetchone()
                if not photo_row:
                    cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                    photo_row = cursor.fetchone()
                    
                actual_photo_path = photo_path
                people = []
                if photo_row:
                    actual_photo_path = photo_row[0]
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []
                        
                people = [p for p in people if p != old_name]
                people_json = json.dumps(people)
                
                cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (people_json, actual_photo_path))

            conn.commit()
            self.send_json({"success": True})
            
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_recluster(self):
        import threading
        
        try:
            data = self.read_json_body()
        except Exception:
            data = {}
            
        reset = data.get("reset", False)
        max_iterations = data.get("max_iterations", 5)
        # Validate type
        try:
            max_iterations = int(max_iterations)
        except (ValueError, TypeError):
            max_iterations = 5
        
        def run_recluster_bg(db_path, reset_db, max_iters):
            photo_index = None
            try:
                # Lazy imports to avoid startup dependencies
                from index import PhotoIndex
                from taxonomy import TagTaxonomy
                from faces import FaceProcessor

                db_dir = os.path.dirname(db_path)
                db_file = os.path.basename(db_path)
                tax_file = db_file.replace("photo_index", "photo_taxonomy").replace(".db", ".json")
                tax_path = os.path.join(db_dir, tax_file)

                logger.info(f"Background thread: Starting face re-clustering (reset={reset_db}, max_iterations={max_iters})...")
                photo_index = PhotoIndex(db_path=db_path)
                if not photo_index.load():
                    logger.error("Background thread: Failed to load photo index")
                    return

                if reset_db:
                    try:
                        photo_index.reset_face_assignments()
                        logger.info("Background thread: Successfully reset face assignments and restored original people metadata.")
                    except Exception as e:
                        logger.error(f"Background thread: Failed to reset database: {e}")
                        return

                taxonomy = TagTaxonomy(file_path=tax_path)
                taxonomy.load()

                processor = FaceProcessor()
                resolved_stats = processor.cluster_and_resolve_identities(photo_index, taxonomy, max_iterations=max_iters)

                # Sync newly resolved face names back to photos table people arrays
                conn = photo_index.conn
                if conn:
                    cursor = conn.cursor()
                    
                    # Fetch all faces with a name (excluding "Non Person")
                    cursor.execute("SELECT photo_path, name FROM faces WHERE name IS NOT NULL AND name != 'Non Person'")
                    faces_by_photo = {}
                    for p_path, name in cursor.fetchall():
                        if p_path not in faces_by_photo:
                            faces_by_photo[p_path] = set()
                        faces_by_photo[p_path].add(name)

                    # Fetch all photos
                    cursor.execute("SELECT path, people FROM photos")
                    photos_rows = cursor.fetchall()
                    
                    for path, people_json in photos_rows:
                        if path in faces_by_photo:
                            try:
                                people = json.loads(people_json) if people_json else []
                            except Exception:
                                people = []
                            
                            updated = False
                            for name in faces_by_photo[path]:
                                if name not in people:
                                    people.append(name)
                                    updated = True
                                    
                            if updated:
                                cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(people), path))
                    
                    conn.commit()

                logger.info("Background thread: Re-clustering and sync completed successfully.")
            except Exception as e:
                logger.error(f"Background thread: Error during re-clustering: {e}", exc_info=True)
            finally:
                if photo_index:
                    photo_index.close()
                TunerHTTPRequestHandler.clustering_in_progress = False

        try:
            # Start background thread to avoid HTTP request timeouts on large databases
            TunerHTTPRequestHandler.clustering_in_progress = True
            t = threading.Thread(target=run_recluster_bg, args=(self.db_path, reset, max_iterations), daemon=True)
            t.start()
            self.send_json({
                "success": True,
                "message": "Clustering started in background"
            })
        except Exception as e:
            TunerHTTPRequestHandler.clustering_in_progress = False
            logger.error(f"Error starting re-clustering thread: {e}")
            self.send_error(500, f"Internal error: {e}")

    def handle_post_unmatch_all(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.conn.cursor() if hasattr(conn, 'conn') else conn.cursor()

            # 1. Fetch currently matched names for faces in this photo
            cursor.execute("SELECT DISTINCT name FROM faces WHERE photo_path = ? AND name IS NOT NULL", (photo_path,))
            matched_names = {row[0] for row in cursor.fetchall()}
            
            # Fallback for Windows path casing mismatches
            if not matched_names:
                cursor.execute("SELECT DISTINCT name FROM faces WHERE photo_path LIKE ? AND name IS NOT NULL", (photo_path,))
                matched_names = {row[0] for row in cursor.fetchall()}

            # 2. Update faces table: set name = NULL
            cursor.execute("UPDATE faces SET name = NULL WHERE photo_path = ?", (photo_path,))
            cursor.execute("UPDATE faces SET name = NULL WHERE photo_path LIKE ?", (photo_path,))

            # 3. Update photos table: remove the matched names from people metadata
            if matched_names:
                cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
                photo_row = cursor.fetchone()
                if not photo_row:
                    cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                    photo_row = cursor.fetchone()
                
                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    # Filter out names that were matched
                    people = [p for p in people if p not in matched_names]
                    cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(people), actual_photo_path))

            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_all: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_automatch(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            # 1. Fetch unmatched faces in this photo
            cursor.execute("SELECT id, embedding FROM faces WHERE photo_path = ? AND name IS NULL", (photo_path,))
            unmatched_rows = cursor.fetchall()
            
            # Fallback for Windows path casing mismatches
            if not unmatched_rows:
                cursor.execute("SELECT id, embedding FROM faces WHERE photo_path LIKE ? AND name IS NULL", (photo_path,))
                unmatched_rows = cursor.fetchall()

            if not unmatched_rows:
                self.send_json({"success": True, "matched_count": 0})
                return

            # 2. Fetch all resolved face embeddings in the database (faces that have a non-null name, excluding 'Non Person')
            cursor.execute("SELECT name, embedding FROM faces WHERE name IS NOT NULL AND name != 'Non Person'")
            resolved_rows = cursor.fetchall()

            if not resolved_rows:
                self.send_json({"success": True, "matched_count": 0})
                return

            # Compile resolved embeddings matrix and names
            resolved_names = []
            resolved_embs = []
            for name, emb_bytes in resolved_rows:
                resolved_names.append(name)
                resolved_embs.append(np.frombuffer(emb_bytes, dtype=np.float32))

            resolved_matrix = np.array(resolved_embs, dtype=np.float32)

            # 3. For each unmatched face, find the best match
            matched_count = 0
            newly_matched_names = set()

            for face_id, target_emb_bytes in unmatched_rows:
                target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
                
                # Calculate similarities
                similarities = np.dot(resolved_matrix, target_emb)
                best_idx = np.argmax(similarities)
                best_sim = similarities[best_idx]

                # High confidence threshold for auto-matching (cosine similarity >= 0.8)
                if best_sim >= 0.8:
                    matched_name = resolved_names[best_idx]
                    
                    # Update face record
                    cursor.execute("UPDATE faces SET name = ? WHERE id = ?", (matched_name, face_id))
                    newly_matched_names.add(matched_name)
                    matched_count += 1

            # 4. Append newly matched names to photos table people list
            if newly_matched_names:
                cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
                photo_row = cursor.fetchone()
                if not photo_row:
                    cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                    photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    updated = False
                    for name in newly_matched_names:
                        if name not in people and name != "Non Person":
                            people.append(name)
                            updated = True

                    if updated:
                        cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(people), actual_photo_path))

            conn.commit()
            self.send_json({"success": True, "matched_count": matched_count})
        except Exception as e:
            logger.error(f"Error in handle_post_automatch: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_people_with_counts(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            # Fetch people with matched counts
            cursor.execute("""
                SELECT name, COUNT(*) as count
                FROM faces
                WHERE name IS NOT NULL AND name != 'Non Person'
                GROUP BY name
                ORDER BY count DESC
            """)
            rows = cursor.fetchall()
            people_counts = [{"name": r[0], "count": r[1]} for r in rows]

            # Fetch unmatched count
            cursor.execute("""
                SELECT COUNT(*)
                FROM faces
                WHERE name IS NULL OR name = 'Non Person'
            """)
            unmatched_row = cursor.fetchone()
            unmatched_count = unmatched_row[0] if unmatched_row else 0

            if unmatched_count > 0:
                people_counts.insert(0, {"name": "Unmatched", "count": unmatched_count})

            self.send_json(people_counts)
        except Exception as e:
            logger.error(f"Error fetching people with counts: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_get_person_faces(self, query):
        name_list = query.get("name")
        if not name_list:
            self.send_error(400, "Missing 'name' parameter")
            return
        name = urllib.parse.unquote(name_list[0])

        limit = 100
        try:
            if "limit" in query:
                limit = int(query["limit"][0])
        except Exception:
            pass

        page = 1
        try:
            if "page" in query:
                page = int(query["page"][0])
        except Exception:
            pass
        offset = (page - 1) * limit

        if not os.path.exists(self.db_path):
            self.send_json({"faces": [], "total_count": 0, "has_more": False})
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            faces = []
            
            if name == "Unmatched":
                cursor.execute("SELECT COUNT(*) FROM faces WHERE name IS NULL OR name = 'Non Person'")
                total_count = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.name, p.raw_metadata
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    WHERE f.name IS NULL OR f.name = 'Non Person'
                    LIMIT ? OFFSET ?
                """, (limit, offset))
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        box = json.loads(r[2]) if r[2] else []
                    except Exception:
                        box = []
                        
                    year = get_year_from_mtime_or_meta(r[4], r[6])
                    
                    faces.append({
                        "id": r[0],
                        "photo_path": r[1],
                        "filename": os.path.basename(r[1]),
                        "box": box,
                        "prob": r[3],
                        "mtime": r[4] if r[4] is not None else 0.0,
                        "year": year,
                        "similarity": 1.0,
                        "name": r[5]
                    })
            else:
                cursor.execute("SELECT COUNT(*) FROM faces WHERE name = ?", (name,))
                total_count = cursor.fetchone()[0]

                # Fetch all embeddings for centroid calculation (from faces table directly)
                cursor.execute("SELECT embedding FROM faces WHERE name = ?", (name,))
                emb_rows = cursor.fetchall()
                embeddings = []
                for r in emb_rows:
                    if r[0] is not None and len(r[0]) > 0:
                        embeddings.append(np.frombuffer(r[0], dtype=np.float32))
                        
                centroid = None
                if len(embeddings) > 0:
                    centroid = compute_geometric_median(embeddings)
                    norm = np.linalg.norm(centroid)
                    if norm > 0:
                        centroid /= norm

                cursor.execute("""
                    SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.embedding, p.raw_metadata
                    FROM faces f
                    LEFT JOIN photos p ON p.path = f.photo_path
                    WHERE f.name = ?
                    LIMIT ? OFFSET ?
                """, (name, limit, offset))
                rows = cursor.fetchall()
                        
                for r in rows:
                    try:
                        box = json.loads(r[2]) if r[2] else []
                    except Exception:
                        box = []
                        
                    similarity = 1.0
                    if centroid is not None and r[5] is not None and len(r[5]) > 0:
                        emb = np.frombuffer(r[5], dtype=np.float32)
                        emb_norm = np.linalg.norm(emb)
                        if emb_norm > 0:
                            emb = emb / emb_norm
                        similarity = float(np.dot(emb, centroid))
                        
                    year = get_year_from_mtime_or_meta(r[4], r[6])
                    
                    faces.append({
                        "id": r[0],
                        "photo_path": r[1],
                        "filename": os.path.basename(r[1]),
                        "box": box,
                        "prob": r[3],
                        "mtime": r[4] if r[4] is not None else 0.0,
                        "year": year,
                        "similarity": similarity
                    })

            has_more = False
            if limit >= 0:
                has_more = (offset + len(faces)) < total_count

            self.send_json({
                "faces": faces,
                "total_count": total_count,
                "has_more": has_more,
                "page": page,
                "limit": limit
            })
        except Exception as e:
            logger.error(f"Error fetching faces for person {name}: {e}")
            self.send_error(500, f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_unmatch_bulk(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_ids = data.get("face_ids")
            if not face_ids or not isinstance(face_ids, list):
                self.send_error(400, "Missing or invalid face_ids")
                return

            try:
                face_ids = [int(fid) for fid in face_ids]
            except (ValueError, TypeError):
                self.send_error(400, "Invalid face_ids format")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            photos_to_check = {}

            for face_id in face_ids:
                cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
                row = cursor.fetchone()
                if row:
                    photo_path, old_name = row
                    if old_name:
                        if photo_path not in photos_to_check:
                            photos_to_check[photo_path] = set()
                        photos_to_check[photo_path].add(old_name)

            placeholders = ",".join("?" for _ in face_ids)
            cursor.execute(f"UPDATE faces SET name = NULL WHERE id IN ({placeholders})", face_ids)

            for photo_path, old_names in photos_to_check.items():
                cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
                photo_row = cursor.fetchone()
                if not photo_row:
                    cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                    photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    updated_people = list(people)
                    for old_name in old_names:
                        cursor.execute("SELECT count(*) FROM faces WHERE photo_path = ? AND name = ?", (photo_path, old_name))
                        count_row = cursor.fetchone()
                        if not count_row:
                            cursor.execute("SELECT count(*) FROM faces WHERE photo_path LIKE ? AND name = ?", (photo_path, old_name))
                            count_row = cursor.fetchone()

                        other_count = count_row[0] if count_row else 0
                        if other_count == 0:
                            updated_people = [p for p in updated_people if p != old_name]

                    if updated_people != people:
                        cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(updated_people), actual_photo_path))

            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def handle_post_match_bulk(self):
        conn = None
        try:
            try:
                data = self.read_json_body()
            except Exception as json_err:
                self.send_error(400, f"Malformed JSON: {json_err}")
                return

            face_ids = data.get("face_ids")
            person_name = data.get("person_name")
            
            if not face_ids or not isinstance(face_ids, list) or not person_name:
                self.send_error(400, "Missing or invalid face_ids or person_name")
                return
                
            try:
                face_ids = [int(fid) for fid in face_ids]
                person_name = str(person_name).strip()
            except (ValueError, TypeError):
                self.send_error(400, "Invalid parameters format")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()

            photos_to_check = {}

            # 1. Fetch photo details for each face_id to check if previous names are unused now
            for face_id in face_ids:
                cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
                row = cursor.fetchone()
                if row:
                    photo_path, old_name = row
                    if photo_path not in photos_to_check:
                        photos_to_check[photo_path] = set()
                    if old_name and old_name != person_name:
                        photos_to_check[photo_path].add(old_name)

            # 2. Update faces table in one transaction
            placeholders = ",".join("?" for _ in face_ids)
            cursor.execute(f"UPDATE faces SET name = ? WHERE id IN ({placeholders})", [person_name] + face_ids)

            # 3. Update photos table people list for each affected photo
            for photo_path, old_names in photos_to_check.items():
                cursor.execute("SELECT path, people FROM photos WHERE path = ?", (photo_path,))
                photo_row = cursor.fetchone()
                if not photo_row:
                    cursor.execute("SELECT path, people FROM photos WHERE path LIKE ?", (photo_path,))
                    photo_row = cursor.fetchone()

                if photo_row:
                    actual_photo_path = photo_row[0]
                    people = []
                    if photo_row[1]:
                        try:
                            people = json.loads(photo_row[1])
                        except Exception:
                            people = []

                    # Append the new person name if missing and not "Non Person"
                    if person_name not in people and person_name != "Non Person":
                        people.append(person_name)

                    # Remove old names if they are no longer matched to any other faces in the photo
                    updated_people = list(people)
                    for old_name in old_names:
                        cursor.execute("SELECT count(*) FROM faces WHERE photo_path = ? AND name = ?", (photo_path, old_name))
                        count_row = cursor.fetchone()
                        if not count_row:
                            cursor.execute("SELECT count(*) FROM faces WHERE photo_path LIKE ? AND name = ?", (photo_path, old_name))
                            count_row = cursor.fetchone()

                        other_count = count_row[0] if count_row else 0
                        if other_count == 0:
                            updated_people = [p for p in updated_people if p != old_name]

                    if updated_people != people:
                        cursor.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(updated_people), actual_photo_path))

            conn.commit()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_match_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")
        finally:
            if conn:
                conn.close()

    def send_json(self, data):
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def send_json_error(self, status_code, message):
        content = json.dumps({"success": False, "error": message}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

class ThreadedHTTPServer(ThreadingTCPServer):
    allow_reuse_address = True

def start_server(port=8080, db_path="data/photo_index.db", gui_dir="gui"):
    TunerHTTPRequestHandler.db_path = db_path
    TunerHTTPRequestHandler.gui_dir = gui_dir

    # Automatically check and apply schema migration on startup
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(faces)")
        columns = [info[1] for info in cursor.fetchall()]
        if "crop_image" not in columns:
            logger.info("Migrating faces table: Adding crop_image column...")
            cursor.execute("ALTER TABLE faces ADD COLUMN crop_image BLOB")
            conn.commit()
        if "prob" not in columns:
            logger.info("Migrating faces table: Adding prob column...")
            cursor.execute("ALTER TABLE faces ADD COLUMN prob REAL")
            conn.commit()
        
        # Ensure faces(name) index exists
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
        conn.commit()
    except Exception as e:
        logger.error(f"Error checking/migrating database schema: {e}")
    finally:
        if conn:
            conn.close()

    server_address = ("", port)
    server = ThreadedHTTPServer(server_address, TunerHTTPRequestHandler)
    logger.info(f"TagTuner server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
        server.shutdown()
        server.server_close()

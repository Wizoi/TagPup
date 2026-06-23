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

class TunerHTTPRequestHandler(BaseHTTPRequestHandler):
    db_path = "data/photo_index.db"
    gui_dir = "gui"

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
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal server error: {e}")

    def handle_get_photos(self, query):
        mode = query.get("mode", ["unmatched"])[0]

        if not os.path.exists(self.db_path):
            self.send_json([])
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if mode == "unmatched":
                # Find photos having at least one face record where name is null
                # Join with photos table to retrieve mtime for sorting
                cursor.execute("""
                    SELECT f.photo_path, COUNT(*), p.mtime
                    FROM faces f
                    LEFT JOIN photos p ON f.photo_path = p.path
                    WHERE f.name IS NULL
                    GROUP BY f.photo_path
                    ORDER BY p.mtime DESC
                """)
                rows = cursor.fetchall()
                photos = []
                for row in rows:
                    p_path = row[0]
                    unmatched_count = row[1]
                    mtime = row[2] if row[2] is not None else 0.0
                    photos.append({
                        "path": p_path,
                        "filename": os.path.basename(p_path),
                        "unmatched_count": unmatched_count,
                        "mtime": mtime,
                        "folder": os.path.dirname(p_path)
                    })
                self.send_json(photos)
            else:
                self.send_json([])
                
            conn.close()
        except Exception as e:
            logger.error(f"Error fetching photos: {e}")
            self.send_error(500, f"Database error: {e}")

    def handle_get_photo_details(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return

        photo_path = urllib.parse.unquote(photo_path_list[0])

        if not os.path.exists(self.db_path):
            self.send_error(404, "Database not found")
            return

        try:
            conn = sqlite3.connect(self.db_path)
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

            conn.close()

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

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT photo_path, box, crop_image FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                self.send_error(404, f"Face ID {face_id} not found in DB")
                return

            photo_path = row[0]
            box_str = row[1]
            crop_image = row[2]

            if crop_image is not None:
                conn.close()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(crop_image)))
                self.end_headers()
                self.wfile.write(crop_image)
                return

            # Fallback if crop_image is None (older records)
            if not os.path.exists(photo_path):
                conn.close()
                self.send_error(404, f"Original photo file not found: {photo_path}")
                return

            try:
                box = json.loads(box_str)
            except Exception:
                try:
                    box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                except Exception:
                    conn.close()
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
            finally:
                conn.close()

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(crop_bytes)))
            self.end_headers()
            self.wfile.write(crop_bytes)

        except Exception as e:
            logger.error(f"Error serving face crop for ID {face_id}: {e}")
            self.send_error(500, f"Error cropping face: {e}")

    def handle_get_people(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        try:
            conn = sqlite3.connect(self.db_path)
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
            conn.close()
            self.send_json(all_people)
        except Exception as e:
            logger.error(f"Error fetching people: {e}")
            self.send_error(500, f"Database error: {e}")

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

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Fetch target embedding
            cursor.execute("SELECT embedding, name FROM faces WHERE id = ?", (face_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_error(404, "Face not found")
                return
                
            target_emb_bytes = row[0]
            target_emb = np.frombuffer(target_emb_bytes, dtype=np.float32)
            
            # Fetch all resolved faces (excluding the current face if it's already resolved)
            cursor.execute("SELECT name, embedding FROM faces WHERE name IS NOT NULL AND id != ?", (face_id,))
            faces_rows = cursor.fetchall()
            
            if not faces_rows:
                conn.close()
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
                        
            conn.close()
            self.send_json(top_matches)
            
        except Exception as e:
            logger.error(f"Error finding face matches: {e}")
            self.send_error(500, f"Error finding matches: {e}")

    def read_json_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        return json.loads(body.decode('utf-8'))

    def handle_post_match(self):
        try:
            data = self.read_json_body()
            face_id = data.get("face_id")
            person_name = data.get("person_name")
            
            if face_id is None or not person_name:
                self.send_error(400, "Missing face_id or person_name")
                return
                
            try:
                face_id = int(face_id)
                person_name = str(person_name).strip()
            except ValueError:
                self.send_error(400, "Invalid parameters")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                conn.close()
                self.send_error(404, "Face ID not found")
                return
                
            photo_path, old_name = face_row
            
            # If name is unchanged, just return success
            if old_name == person_name:
                conn.close()
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
            conn.close()
            
            self.send_json({"success": True})
            
        except Exception as e:
            logger.error(f"Error in handle_post_match: {e}")
            self.send_error(500, f"Internal error: {e}")

    def handle_post_unmatch(self):
        try:
            data = self.read_json_body()
            face_id = data.get("face_id")
            
            if face_id is None:
                self.send_error(400, "Missing face_id")
                return
                
            try:
                face_id = int(face_id)
            except ValueError:
                self.send_error(400, "Invalid face_id")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 1. Fetch face details: photo_path and old name
            cursor.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,))
            face_row = cursor.fetchone()
            if not face_row:
                conn.close()
                self.send_error(404, "Face ID not found")
                return
                
            photo_path, old_name = face_row
            
            if old_name is None:
                # Already unmatched
                conn.close()
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
            conn.close()
            
            self.send_json({"success": True})
            
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch: {e}")
            self.send_error(500, f"Internal error: {e}")

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
                        photo_index.close()
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

                photo_index.close()
                logger.info("Background thread: Re-clustering and sync completed successfully.")
            except Exception as e:
                logger.error(f"Background thread: Error during re-clustering: {e}", exc_info=True)

        try:
            # Start background thread to avoid HTTP request timeouts on large databases
            t = threading.Thread(target=run_recluster_bg, args=(self.db_path, reset, max_iterations), daemon=True)
            t.start()
            self.send_json({
                "success": True,
                "message": "Clustering started in background"
            })
        except Exception as e:
            logger.error(f"Error starting re-clustering thread: {e}")
            self.send_error(500, f"Internal error: {e}")


    def handle_post_unmatch_all(self):
        try:
            data = self.read_json_body()
            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

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
            conn.close()
            
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_all: {e}")
            self.send_error(500, f"Internal error: {e}")

    def handle_post_automatch(self):
        try:
            data = self.read_json_body()
            photo_path = data.get("photo_path")
            if not photo_path:
                self.send_error(400, "Missing photo_path")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 1. Fetch unmatched faces in this photo
            cursor.execute("SELECT id, embedding FROM faces WHERE photo_path = ? AND name IS NULL", (photo_path,))
            unmatched_rows = cursor.fetchall()
            
            # Fallback for Windows path casing mismatches
            if not unmatched_rows:
                cursor.execute("SELECT id, embedding FROM faces WHERE photo_path LIKE ? AND name IS NULL", (photo_path,))
                unmatched_rows = cursor.fetchall()

            if not unmatched_rows:
                conn.close()
                self.send_json({"success": True, "matched_count": 0})
                return

            # 2. Fetch all resolved face embeddings in the database (faces that have a non-null name)
            cursor.execute("SELECT name, embedding FROM faces WHERE name IS NOT NULL")
            resolved_rows = cursor.fetchall()

            if not resolved_rows:
                conn.close()
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
            conn.close()

            self.send_json({"success": True, "matched_count": matched_count})
        except Exception as e:
            logger.error(f"Error in handle_post_automatch: {e}")
            self.send_error(500, f"Internal error: {e}")

    def handle_get_people_with_counts(self):
        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name, COUNT(*) as count
                FROM faces
                WHERE name IS NOT NULL
                GROUP BY name
                ORDER BY count DESC
            """)
            rows = cursor.fetchall()
            people_counts = [{"name": r[0], "count": r[1]} for r in rows]
            conn.close()
            self.send_json(people_counts)
        except Exception as e:
            logger.error(f"Error fetching people with counts: {e}")
            self.send_error(500, f"Database error: {e}")

    def handle_get_person_faces(self, query):
        name_list = query.get("name")
        if not name_list:
            self.send_error(400, "Missing 'name' parameter")
            return
        name = urllib.parse.unquote(name_list[0])

        if not os.path.exists(self.db_path):
            self.send_json([])
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, photo_path, box, prob
                FROM faces
                WHERE name = ?
            """, (name,))
            rows = cursor.fetchall()
            faces = []
            for r in rows:
                try:
                    box = json.loads(r[2]) if r[2] else []
                except Exception:
                    box = []
                faces.append({
                    "id": r[0],
                    "photo_path": r[1],
                    "filename": os.path.basename(r[1]),
                    "box": box,
                    "prob": r[3]
                })
            conn.close()
            self.send_json(faces)
        except Exception as e:
            logger.error(f"Error fetching faces for person {name}: {e}")
            self.send_error(500, f"Database error: {e}")

    def handle_post_unmatch_bulk(self):
        try:
            data = self.read_json_body()
            face_ids = data.get("face_ids")
            if not face_ids or not isinstance(face_ids, list):
                self.send_error(400, "Missing or invalid face_ids")
                return

            try:
                face_ids = [int(fid) for fid in face_ids]
            except ValueError:
                self.send_error(400, "Invalid face_ids format")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
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
            conn.close()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_unmatch_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")

    def handle_post_match_bulk(self):
        try:
            data = self.read_json_body()
            face_ids = data.get("face_ids")
            person_name = data.get("person_name")
            
            if not face_ids or not isinstance(face_ids, list) or not person_name:
                self.send_error(400, "Missing or invalid face_ids or person_name")
                return
                
            try:
                face_ids = [int(fid) for fid in face_ids]
                person_name = str(person_name).strip()
            except ValueError:
                self.send_error(400, "Invalid parameters format")
                return

            if not os.path.exists(self.db_path):
                self.send_error(404, "Database not found")
                return

            conn = sqlite3.connect(self.db_path)
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
            conn.close()
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in handle_post_match_bulk: {e}")
            self.send_error(500, f"Internal error: {e}")

    def send_json(self, data):
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

class ThreadedHTTPServer(ThreadingTCPServer):
    allow_reuse_address = True

def start_server(port=8080, db_path="data/photo_index.db", gui_dir="gui"):
    TunerHTTPRequestHandler.db_path = db_path
    TunerHTTPRequestHandler.gui_dir = gui_dir

    # Automatically check and apply schema migration on startup
    try:
        conn = sqlite3.connect(db_path)
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
        conn.close()
    except Exception as e:
        logger.error(f"Error checking/migrating database schema: {e}")

    server_address = ("", port)
    server = ThreadedHTTPServer(server_address, TunerHTTPRequestHandler)
    logger.info(f"TagTuner server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
        server.shutdown()
        server.server_close()

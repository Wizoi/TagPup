import os
import sys
import sqlite3
import json
import io
import time
from PIL import Image

# Disable Pillow decompression limit check for large panorama/scan files
Image.MAX_IMAGE_PIXELS = None

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "photo_index.db")

def cache_all_crops(batch_size=100):
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    print("Connecting to database...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find faces with missing crops
    print("Querying faces with missing crop images...")
    cursor.execute("SELECT id, photo_path, box FROM faces WHERE crop_image IS NULL")
    rows = cursor.fetchall()
    
    total_faces = len(rows)
    if total_faces == 0:
        print("All face crops are already cached in the database!")
        conn.close()
        return

    print(f"Found {total_faces} faces with missing crops.")

    # Group faces by photo path to minimize image load/decode overhead
    faces_by_photo = {}
    for face_id, photo_path, box_str in rows:
        if photo_path not in faces_by_photo:
            faces_by_photo[photo_path] = []
        faces_by_photo[photo_path].append((face_id, box_str))

    total_photos = len(faces_by_photo)
    print(f"Grouping into {total_photos} unique photo files.")

    processed_faces = 0
    processed_photos = 0
    success_count = 0
    failed_photos = 0
    
    start_time = time.time()
    
    # Process photos
    photo_paths = list(faces_by_photo.keys())
    
    # We will accumulate updates and commit in batches of photos
    pending_updates = []
    
    for idx, photo_path in enumerate(photo_paths, 1):
        processed_photos += 1
        
        if not os.path.exists(photo_path):
            # print(f"\nWarning: Photo not found on disk: {photo_path}")
            processed_faces += len(faces_by_photo[photo_path])
            failed_photos += 1
            continue

        try:
            with Image.open(photo_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                
                width, height = img.size
                
                # Process all faces in this photo
                for face_id, box_str in faces_by_photo[photo_path]:
                    processed_faces += 1
                    try:
                        box = json.loads(box_str)
                    except Exception:
                        try:
                            box = [int(x) for x in box_str.replace('[', '').replace(']', '').split(',')]
                        except Exception:
                            continue
                            
                    x1, y1, x2, y2 = box
                    x1, y1 = max(0, int(x1)), max(0, int(y1))
                    x2, y2 = min(width, int(x2)), min(height, int(y2))
                    
                    if (x2 - x1) <= 0 or (y2 - y1) <= 0:
                        crop_img = Image.new("RGB", (100, 100), color=(50, 50, 50))
                    else:
                        crop_img = img.crop((x1, y1, x2, y2))
                        
                    # Resize to max 256px if larger to optimize database storage size
                    if max(crop_img.size) > 256:
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            try:
                                resample = Image.LANCZOS
                            except AttributeError:
                                resample = Image.ANTIALIAS
                        crop_img.thumbnail((256, 256), resample)
                        
                    buffer = io.BytesIO()
                    crop_img.save(buffer, format="JPEG", quality=90)
                    crop_bytes = buffer.getvalue()
                    
                    pending_updates.append((sqlite3.Binary(crop_bytes), face_id))
                    success_count += 1
        except Exception as e:
            # print(f"\nError opening/processing {photo_path}: {e}")
            processed_faces += len(faces_by_photo[photo_path])
            failed_photos += 1
            
        # Commit batch of updates
        if processed_photos % batch_size == 0 or processed_photos == total_photos:
            if pending_updates:
                try:
                    cursor.executemany("UPDATE faces SET crop_image = ? WHERE id = ?", pending_updates)
                    conn.commit()
                except Exception as db_err:
                    print(f"\nDatabase error during batch update: {db_err}")
                    conn.rollback()
                pending_updates = []
            
            # Print progress
            elapsed = time.time() - start_time
            rate = processed_faces / elapsed if elapsed > 0 else 0
            eta = (total_faces - processed_faces) / rate if rate > 0 else 0
            sys.stdout.write(
                f"\rProgress: {processed_photos}/{total_photos} photos ({processed_faces}/{total_faces} faces) | "
                f"Crops cached: {success_count} | Speed: {rate:.1f} faces/s | ETA: {eta/60:.1f}m"
            )
            sys.stdout.flush()

    conn.close()
    print(f"\n\nCaching completed in {time.time() - start_time:.1f} seconds!")
    print(f"Successfully cached {success_count} face crops in DB.")
    if failed_photos > 0:
        print(f"Skipped/failed {failed_photos} photo files (missing files or read errors).")

if __name__ == "__main__":
    cache_all_crops()

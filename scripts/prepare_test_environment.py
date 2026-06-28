# prepare_test_environment.py
"""
Automates setting up a clean test database and folder environment for screenshot regeneration.
Creates separate Training and New folders, seeds taxonomy with Pets as a has_face category,
extracts real animal face crops, and indexes them in test_photo_index.db.
"""

import os
import sqlite3
import json
import numpy as np
from PIL import Image
import io
import shutil
import sys

# Ensure project root is in search path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

DB_PATH = os.path.join(PROJECT_ROOT, "data", "test_photo_index.db")
TAX_PATH = os.path.join(PROJECT_ROOT, "data", "test_photo_index_taxonomy.json")

def crop_image_face(image_path, box):
    img = Image.open(image_path)
    cropped = img.crop(box)
    cropped = cropped.resize((150, 150))
    byte_arr = io.BytesIO()
    cropped.save(byte_arr, format='JPEG')
    return byte_arr.getvalue()

def main():
    print("Preparing test environment for tutorial screenshots...")
    
    # 1. Clean old test database files
    for p in [DB_PATH, TAX_PATH]:
        if os.path.exists(p):
            try:
                os.remove(p)
                print(f"Removed old test file: {p}")
            except Exception as e:
                print(f"Error removing {p}: {e}")

    # 2. Seed empty database tables
    from index import PhotoIndex
    from taxonomy import seed_taxonomy_from_db
    
    photo_index = PhotoIndex(db_path=DB_PATH)
    photo_index.load() # Creates tables
    seed_taxonomy_from_db(DB_PATH)
    print("Empty database tables initialized and seeded with default taxonomy.")

    # 3. Setup folder structure
    test_photos_dir = os.path.join(PROJECT_ROOT, "data", "test_photos")
    training_dir = os.path.join(test_photos_dir, "Training")
    new_dir = os.path.join(test_photos_dir, "New")
    
    for d in [training_dir, new_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
    
    os.makedirs(training_dir, exist_ok=True)
    os.makedirs(new_dir, exist_ok=True)
    
    shutil.copy2(os.path.join(test_photos_dir, "puppy.png"), os.path.join(training_dir, "puppy.png"))
    # Save a clean copy of puppy2.png without any metadata/EXIF to prevent "Input ingredient 0" title
    from PIL import Image
    with Image.open(os.path.join(test_photos_dir, "puppy2.png")) as img:
        img.save(os.path.join(new_dir, "puppy2.png"), "PNG")
    print(f"Training folder prepared: {training_dir}")
    print(f"New content folder prepared: {new_dir}")

    # 4. Connect to database to write mock records
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Paths (must be forward slash normalized for TagPup DB)
    puppy_path = os.path.normpath(os.path.join(training_dir, "puppy.png")).replace("\\", "/")
    puppy2_path = os.path.normpath(os.path.join(new_dir, "puppy2.png")).replace("\\", "/")

    # Embeddings (mock vectors)
    clip_emb = np.random.randn(1024).astype(np.float32)
    clip_emb /= np.linalg.norm(clip_emb)
    
    face_emb = np.random.randn(512).astype(np.float32)
    face_emb /= np.linalg.norm(face_emb)

    # Insert Pets/Puppy into tag_taxonomy
    c.execute("SELECT id FROM tag_taxonomy WHERE tag = 'Pets'")
    pets_id = c.fetchone()[0]
    c.execute("""
        INSERT OR IGNORE INTO tag_taxonomy (tag, parent_id, name, has_face)
        VALUES (?, ?, ?, 1)
    """, ("Pets/Puppy", pets_id, "Puppy"))
    print("Taxonomy seeded: Added 'Pets/Puppy' (has_face = 1).")

    # Crop real face from puppy.png (head region, approx [350, 200, 750, 600])
    puppy_face_box = [350, 200, 750, 600]
    puppy_crop = crop_image_face(os.path.join(training_dir, "puppy.png"), puppy_face_box)

    # Crop real face from puppy2.png (head region, approx [350, 200, 750, 600])
    puppy2_face_box = [350, 200, 750, 600]
    puppy2_crop = crop_image_face(os.path.join(new_dir, "puppy2.png"), puppy2_face_box)

    # Insert puppy image representing training set (already matched in database)
    c.execute("""
        INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        puppy_path, 1000000000.0, 728759, 
        json.dumps(["Pets", "Pets/Puppy"]), 
        json.dumps(["Puppy"]), 
        json.dumps(["A cute brown puppy sitting on the grass."]), 
        json.dumps({
            "EXIF:Make": "Canon",
            "EXIF:Model": "Canon EOS R5",
            "EXIF:DateTimeOriginal": "2026:06:01 12:00:00"
        }),
        clip_emb.tobytes()
    ))

    # Insert face for puppy representing already matched face
    c.execute("""
        INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        puppy_path,
        json.dumps(puppy_face_box),
        face_emb.tobytes(),
        "Puppy", # Matched to Puppy!
        puppy_crop,
        0.98
    ))

    # Insert puppy2 image representing new content (untagged, unmatched face)
    clip_emb_puppy2 = np.random.randn(1024).astype(np.float32)
    clip_emb_puppy2 /= np.linalg.norm(clip_emb_puppy2)
    
    # We set puppy2's face embedding to be identical to puppy's face embedding
    # so face recognition matches them!
    c.execute("""
        INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        puppy2_path, 1000001000.0, 728759,
        json.dumps([]),
        json.dumps([]),
        json.dumps([]),
        json.dumps({
            "EXIF:Make": "Sony",
            "EXIF:Model": "Sony A7R IV",
            "EXIF:DateTimeOriginal": "2026:06:20 15:30:00"
        }),
        clip_emb_puppy2.tobytes()
    ))

    # Insert unmatched face for puppy2
    c.execute("""
        INSERT INTO faces (photo_path, box, embedding, name, crop_image, prob)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        puppy2_path,
        json.dumps(puppy2_face_box),
        face_emb.tobytes(), # 100% match face embedding
        None, # Unmatched face!
        puppy2_crop,
        0.98
    ))

    conn.commit()
    conn.close()
    print("Database seeded with Training folder (puppy) and New folder (puppy2) successfully!")
    print("\nEnvironment is ready. To view/regenerate screenshots:")
    print("  1. Start TagPup GUI:  python tagpup_gui.py test_photo_index.db")
    print("  2. Start TagTuner:   python tagtuner.py test_photo_index.db")
    print("  3. Navigate to http://localhost:8092/?path=data/test_photos/New and run suggestions.")
    print("  4. Navigate to http://localhost:8081/ and resolve face matching.")

if __name__ == "__main__":
    main()

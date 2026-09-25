# prepare_test_environment.py
"""
Automates setting up a clean test database and folder environment for screenshot regeneration.
Creates separate Training and New folders, seeds taxonomy with Pets as a has_face category,
extracts real animal face crops, and indexes them in test_photo_index.db.
"""

import os
import numpy as np
import shutil
import sys

import _root  # noqa: F401
import db as tagpup_db
from tagpup import config as tagpup_config
from tagpup.files import images
from tagpup.store import embeddings as store_embeddings
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos

#: The vectors below are kept under the model the config names, which search reads.
MODEL = store_embeddings.model_key(**tagpup_config.embedder_settings())

# Ensure project root is in search path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

DB_PATH = os.path.join(PROJECT_ROOT, "data", "test_photo_index.db")
TAX_PATH = os.path.join(PROJECT_ROOT, "data", "test_photo_index_taxonomy.json")

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
    conn = tagpup_db.connect(DB_PATH)

    # Stored the way the indexer stores them. These rows used forward slashes, the
    # opposite of production, so the screenshots were taken of a database no real
    # index ever produces.
    import paths
    puppy_path = paths.stored(os.path.join(training_dir, "puppy.png"))
    puppy2_path = paths.stored(os.path.join(new_dir, "puppy2.png"))

    # Embeddings (mock vectors)
    clip_emb = np.random.randn(1024).astype(np.float32)
    clip_emb /= np.linalg.norm(clip_emb)
    
    face_emb = np.random.randn(512).astype(np.float32)
    face_emb /= np.linalg.norm(face_emb)

    # Insert Pets/Puppy into tag_taxonomy: Pets flagged as holding faces, so the puppy
    # below it does too. A new library flags only People.
    from tagpup.store import taxonomy as store_taxonomy
    store_taxonomy.set_branch_flags(conn, "Pets", has_face=1)
    store_taxonomy.add_node(conn, "Pets/Puppy")
    print("Taxonomy seeded: Added 'Pets/Puppy' (has_face = 1).")

    # Crop real face from puppy.png (head region, approx [350, 200, 750, 600])
    puppy_face_box = [350, 200, 750, 600]
    puppy_crop = images.face_crop(os.path.join(training_dir, "puppy.png"), puppy_face_box)

    # Crop real face from puppy2.png (head region, approx [350, 200, 750, 600])
    puppy2_face_box = [350, 200, 750, 600]
    puppy2_crop = images.face_crop(os.path.join(new_dir, "puppy2.png"), puppy2_face_box)

    # Insert puppy image representing training set (already matched in database)
    store_photos.record_indexed(conn, puppy_path, {
        "mtime": 1000000000.0, "size": 728759,
        "tags": ["Pets", "Pets/Puppy"],
        "people": ["Puppy"],
        "captions": ["A cute brown puppy sitting on the grass."],
        "raw_metadata": {
            "EXIF:Make": "Canon",
            "EXIF:Model": "Canon EOS R5",
            "EXIF:DateTimeOriginal": "2026:06:01 12:00:00"
        },
        "embedding": clip_emb.tobytes(),
    }, model=MODEL)

    # Insert face for puppy representing already matched face
    store_faces.insert(conn, puppy_path, puppy_face_box, face_emb.tobytes(),
                       name="Puppy", crop=puppy_crop, prob=0.98)  # matched to Puppy

    # Insert puppy2 image representing new content (untagged, unmatched face)
    clip_emb_puppy2 = np.random.randn(1024).astype(np.float32)
    clip_emb_puppy2 /= np.linalg.norm(clip_emb_puppy2)
    
    # We set puppy2's face embedding to be identical to puppy's face embedding
    # so face recognition matches them!
    store_photos.record_indexed(conn, puppy2_path, {
        "mtime": 1000001000.0, "size": 728759,
        "tags": [], "people": [], "captions": [],
        "raw_metadata": {
            "EXIF:Make": "Sony",
            "EXIF:Model": "Sony A7R IV",
            "EXIF:DateTimeOriginal": "2026:06:20 15:30:00"
        },
        "embedding": clip_emb_puppy2.tobytes(),
    }, model=MODEL)

    # Insert unmatched face for puppy2
    # The same face embedding as the puppy's, unnamed: face recognition matches them.
    store_faces.insert(conn, puppy2_path, puppy2_face_box, face_emb.tobytes(),
                       crop=puppy2_crop, prob=0.98)

    conn.commit()
    conn.close()
    print("Database seeded with Training folder (puppy) and New folder (puppy2) successfully!")
    print("\nEnvironment is ready. To view/regenerate screenshots:")
    print("  1. Start TagPup GUI:  python tagpup_web.py --db test_photo_index --open tagpup")
    print("  2. Start TagTuner:   python tagpup_web.py --db test_photo_index --open tuner")
    print("  3. Navigate to http://localhost:8092/?path=data/test_photos/New and run suggestions.")
    print("  4. Navigate to http://localhost:8081/ and resolve face matching.")

if __name__ == "__main__":
    main()

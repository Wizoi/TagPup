import sqlite3
import time
import numpy as np
import json
import os
import sys

sys.path.append("scripts")
from tuner_server import get_year_from_mtime_or_meta

def main():
    db_path = "data/photo_index.db"
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("""
        SELECT name, COUNT(*) as count 
        FROM faces 
        WHERE name IS NOT NULL
        GROUP BY name 
        ORDER BY count DESC 
        LIMIT 5
    """)
    counts = c.fetchall()
    print("Top names and counts:", counts)
    if not counts:
        print("No faces found in DB.")
        return
        
    name = counts[0][0]
    print(f"\n--- Testing name: {name} (faces count: {counts[0][1]}) ---")
    
    # 1. Centroid calculation
    t0 = time.time()
    c.execute("SELECT embedding FROM faces WHERE name = ?", (name,))
    embs = [np.frombuffer(r[0], dtype=np.float32) for r in c.fetchall() if r[0]]
    if embs:
        centroid = np.mean(embs, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid /= norm
    else:
        centroid = None
    print(f"1. Centroid calculation took: {time.time() - t0:.4f} seconds")
    
    # 2. Query with OR
    t0 = time.time()
    c.execute("""
        SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.embedding, p.raw_metadata 
        FROM faces f 
        LEFT JOIN photos p ON p.path = f.photo_path OR p.path = REPLACE(f.photo_path, '\\\\', '/') OR p.path = REPLACE(f.photo_path, '/', '\\\\') 
        WHERE f.name = ?
    """, (name,))
    rows_or = c.fetchall()
    print(f"2. Query with OR took: {time.time() - t0:.4f} seconds (returned {len(rows_or)} rows)")
    
    # 3. Query direct JOIN
    t0 = time.time()
    c.execute("""
        SELECT f.id, f.photo_path, f.box, f.prob, p.mtime, f.embedding, p.raw_metadata 
        FROM faces f 
        LEFT JOIN photos p ON p.path = f.photo_path 
        WHERE f.name = ?
    """, (name,))
    rows_direct = c.fetchall()
    print(f"3. Query direct JOIN took: {time.time() - t0:.4f} seconds (returned {len(rows_direct)} rows)")
    
    # 4. Processing rows (caching, parsing, distance)
    t0 = time.time()
    faces = []
    for r in rows_direct:
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
    print(f"4. Python processing of rows took: {time.time() - t0:.4f} seconds")
    
    # 5. Json serialization
    t0 = time.time()
    json_str = json.dumps(faces)
    print(f"5. JSON serialization took: {time.time() - t0:.4f} seconds (length: {len(json_str)} bytes)")
    
    conn.close()

if __name__ == "__main__":
    main()

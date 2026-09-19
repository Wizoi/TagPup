"""Restore face name assignments from a backup written before re-clustering.

Usage:  .venv\Scripts\python scripts/restore_face_names.py data/face_names_backup_<ts>.json
"""
import json
import sqlite3
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    with open(sys.argv[1], encoding="utf-8") as fh:
        payload = json.load(fh)

    db_path = payload["db"]
    faces = payload["faces"]
    print(f"Restoring {len(faces)} face names into {db_path} (backup taken {payload['taken']})...")

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.executemany("UPDATE faces SET name = ? WHERE id = ?", [(n, i) for i, n in faces])
    conn.commit()
    named = conn.execute("SELECT COUNT(*) FROM faces WHERE name IS NOT NULL").fetchone()[0]
    conn.close()
    print(f"Restored. {named} faces now carry a name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

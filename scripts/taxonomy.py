# taxonomy.py
import os
import json
import sqlite3
import logging
from typing import Set, List, Dict, Union, Optional

logger = logging.getLogger("tagpup_cli.taxonomy")

class TagTaxonomy:
    def __init__(self, file_path: Optional[str] = None, db_path: Optional[str] = None):
        if db_path is not None:
            self.db_path = db_path
            if file_path is None:
                # e.g. data/photo_index.db -> data/photo_index_taxonomy.json
                # if it is default data/photo_index.db, we want data/photo_taxonomy.json for compatibility
                if os.path.basename(db_path) == "photo_index.db":
                    self.file_path = os.path.join(os.path.dirname(db_path), "photo_taxonomy.json").replace("\\", "/")
                else:
                    self.file_path = (os.path.splitext(db_path)[0] + "_taxonomy.json").replace("\\", "/")
            else:
                self.file_path = file_path
        else:
            if file_path is None:
                file_path = "data/photo_taxonomy.json"
            self.file_path = file_path
            if file_path.endswith("photo_taxonomy.json"):
                self.db_path = os.path.join(os.path.dirname(file_path), "photo_index.db").replace("\\", "/")
            elif file_path.endswith("_taxonomy.json"):
                self.db_path = file_path.replace("_taxonomy.json", ".db")
            else:
                self.db_path = os.path.splitext(file_path)[0] + ".db"
        # Store full paths of known hierarchical tags, e.g., {"Family/Immediate/Jane Doe", "Activity/Botanical Garden"}
        self.paths: Set[str] = set()

    def load(self):
        """Load taxonomy from database tag_taxonomy table, falling back to JSON file if DB doesn't have it."""
        self.paths = set()
        loaded_from_db = False
        
        # 1. Try to load from database
        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
                if cursor.fetchone():
                    cursor.execute("SELECT tag FROM tag_taxonomy")
                    for row in cursor.fetchall():
                        self.paths.add(row[0])
                    loaded_from_db = True
                conn.close()
            except Exception as e:
                logger.error(f"Error loading taxonomy from DB: {e}")
                
        # 2. Fall back to JSON file if not loaded from DB and JSON exists
        if not loaded_from_db and os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.paths = set(data.get("paths", []))
                logger.info(f"Loaded taxonomy from JSON fallback with {len(self.paths)} paths.")
                # Since we have JSON but not DB, we can write it to DB if DB exists
                if os.path.exists(self.db_path):
                    self.save_to_db()
            except Exception as e:
                logger.error(f"Error loading taxonomy JSON fallback: {e}")

    def save(self):
        """Save taxonomy to both JSON file (for backward compatibility) and database."""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"paths": sorted(list(self.paths))}, f, indent=2)
            logger.info(f"Saved taxonomy to JSON with {len(self.paths)} paths.")
        except Exception as e:
            logger.error(f"Error saving taxonomy to JSON: {e}")
            
        self.save_to_db()

    def save_to_db(self):
        """Sync self.paths with the database tag_taxonomy table."""
        if not os.path.exists(self.db_path):
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            # Make sure table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tag_taxonomy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag TEXT UNIQUE,
                    parent_id INTEGER,
                    name TEXT,
                    is_people INTEGER DEFAULT 0,
                    hidden_from_autocomplete INTEGER DEFAULT 0,
                    FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
                )
            """)
            
            # For each path in self.paths, insert if not present
            for path in sorted(list(self.paths)):
                parts = self.normalize_tag(path).split("/")
                parent_id = None
                accumulated_path = ""
                for i, part in enumerate(parts):
                    if i == 0:
                        accumulated_path = part
                    else:
                        accumulated_path += "/" + part
                    
                    cursor.execute("SELECT id, is_people FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                    row = cursor.fetchone()
                    if row:
                        parent_id = row[0]
                    else:
                        is_p = 0
                        if i == 0:
                            if part.lower() in ["people", "family", "friends"]:
                                is_p = 1
                        else:
                            if parent_id is not None:
                                cursor.execute("SELECT is_people FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                p_row = cursor.fetchone()
                                if p_row:
                                    is_p = p_row[0]
                        cursor.execute(
                            "INSERT INTO tag_taxonomy (tag, parent_id, name, is_people) VALUES (?, ?, ?, ?)",
                            (accumulated_path, parent_id, part, is_p)
                        )
                        parent_id = cursor.lastrowid
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error syncing taxonomy paths to DB: {e}")

    @staticmethod
    def normalize_tag(tag: str) -> str:
        """Normalize a tag by replacing common hierarchy separators (e.g. '|' or '\') with '/'."""
        tag = tag.strip()
        tag = tag.replace("|", "/").replace("\\", "/")
        parts = [p.strip() for p in tag.split("/") if p.strip()]
        return "/".join(parts)

    def add_tag(self, tag: str):
        """Add a tag to the taxonomy, building all of its ancestor paths."""
        normalized = self.normalize_tag(tag)
        if not normalized:
            return
            
        parts = normalized.split("/")
        for i in range(1, len(parts) + 1):
            path = "/".join(parts[:i])
            self.paths.add(path)

    def add_tags(self, tags: List[str]):
        """Add multiple tags to the taxonomy."""
        for tag in tags:
            self.add_tag(tag)

    def expand_tag(self, tag: str) -> List[str]:
        """Given a tag, if it matches a path in the taxonomy, expand it to include all ancestors."""
        normalized = self.normalize_tag(tag)
        if not normalized:
            return []
            
        results = []
        matched_path = None
        if normalized in self.paths:
            matched_path = normalized
        else:
            sorted_paths = sorted(list(self.paths), key=len, reverse=True)
            for p in sorted_paths:
                parts = p.split("/")
                if normalized == parts[-1] or p.endswith("/" + normalized):
                    matched_path = p
                    break
        
        if matched_path:
            parts = matched_path.split("/")
            for i in range(1, len(parts) + 1):
                results.append("/".join(parts[:i]))
        else:
            parts = normalized.split("/")
            for i in range(1, len(parts) + 1):
                results.append("/".join(parts[:i]))
                
        return results

    def get_root_categories(self) -> Dict[str, int]:
        """Get count of elements under each root (top-level) category."""
        roots = {}
        for path in self.paths:
            root = path.split("/")[0]
            roots[root] = roots.get(root, 0) + 1
        return roots


def seed_taxonomy_from_db(db_path: str):
    """Seed taxonomy tree from DB index and default categories if empty."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        
        # Ensure tag_taxonomy table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tag_taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE,
                parent_id INTEGER,
                name TEXT,
                is_people INTEGER DEFAULT 0,
                hidden_from_autocomplete INTEGER DEFAULT 0,
                FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
            )
        """)
        
        # Check if already seeded
        cursor.execute("SELECT COUNT(*) FROM tag_taxonomy")
        count = cursor.fetchone()[0]
        if count > 0:
            conn.close()
            return
            
        # Seed default categories
        default_categories = ["People", "Activity", "Pets", "School", "Trips"]
        category_ids = {}
        for category in default_categories:
            is_p = 1 if category.lower() == "people" else 0
            try:
                cursor.execute(
                    "INSERT INTO tag_taxonomy (tag, parent_id, name, is_people) VALUES (?, NULL, ?, ?)",
                    (category, category, is_p)
                )
                category_ids[category] = cursor.lastrowid
            except sqlite3.IntegrityError:
                pass
                
        # Now seed tags from photos table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photos'")
        if cursor.fetchone():
            cursor.execute("SELECT tags FROM photos WHERE tags IS NOT NULL")
            all_tags = set()
            for row in cursor.fetchall():
                try:
                    tags_list = json.loads(row[0])
                    for t in tags_list:
                        all_tags.add(t)
                except Exception:
                    pass
            
            for tag in all_tags:
                normalized = TagTaxonomy.normalize_tag(tag)
                if not normalized:
                    continue
                parts = normalized.split("/")
                parent_id = None
                accumulated_path = ""
                for i, part in enumerate(parts):
                    if i == 0:
                        accumulated_path = part
                    else:
                        accumulated_path += "/" + part
                    
                    cursor.execute("SELECT id, is_people FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                    row = cursor.fetchone()
                    if row:
                        parent_id = row[0]
                    else:
                        is_p = 0
                        if i == 0:
                            if part.lower() in ["people", "family", "friends"]:
                                is_p = 1
                        else:
                            if parent_id is not None:
                                cursor.execute("SELECT is_people FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                p_row = cursor.fetchone()
                                if p_row:
                                    is_p = p_row[0]
                        cursor.execute(
                            "INSERT INTO tag_taxonomy (tag, parent_id, name, is_people) VALUES (?, ?, ?, ?)",
                            (accumulated_path, parent_id, part, is_p)
                        )
                        parent_id = cursor.lastrowid
                        
        # Seed people names from faces table if they exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faces'")
        if cursor.fetchone():
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")
            for row in cursor.fetchall():
                name = row[0].strip()
                if not name:
                    continue
                people_root_id = category_ids.get("People")
                if not people_root_id:
                    cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = 'People'")
                    root_row = cursor.fetchone()
                    if root_row:
                        people_root_id = root_row[0]
                
                path = f"People/{name}"
                cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (path,))
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO tag_taxonomy (tag, parent_id, name, is_people) VALUES (?, ?, ?, 1)",
                        (path, people_root_id, name)
                    )
                    
        # Check if taxonomy json file exists and seed from there too
        tax_path = db_path.replace(".db", "_taxonomy.json")
        if os.path.exists(tax_path):
            try:
                with open(tax_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    paths = data.get("paths", [])
                for path in paths:
                    normalized = TagTaxonomy.normalize_tag(path)
                    if not normalized:
                        continue
                    parts = normalized.split("/")
                    parent_id = None
                    accumulated_path = ""
                    for i, part in enumerate(parts):
                        if i == 0:
                            accumulated_path = part
                        else:
                            accumulated_path += "/" + part
                        
                        cursor.execute("SELECT id, is_people FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                        row = cursor.fetchone()
                        if row:
                            parent_id = row[0]
                        else:
                            is_p = 0
                            if i == 0:
                                if part.lower() in ["people", "family", "friends"]:
                                    is_p = 1
                            else:
                                if parent_id is not None:
                                    cursor.execute("SELECT is_people FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                    p_row = cursor.fetchone()
                                    if p_row:
                                        is_p = p_row[0]
                            cursor.execute(
                                "INSERT INTO tag_taxonomy (tag, parent_id, name, is_people) VALUES (?, ?, ?, ?)",
                                (accumulated_path, parent_id, part, is_p)
                            )
                            parent_id = cursor.lastrowid
            except Exception as json_err:
                logger.error(f"Error seeding from taxonomy json: {json_err}")
                
        conn.commit()
        conn.close()
        logger.info("Successfully seeded tag taxonomy database table.")
    except Exception as e:
        logger.error(f"Error seeding taxonomy from DB: {e}")

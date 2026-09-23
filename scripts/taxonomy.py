# taxonomy.py
import os
import json
try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db
import logging
from typing import Set, List, Dict, Optional

logger = logging.getLogger("tagpup_cli.taxonomy")

class TagTaxonomy:
    def __init__(self, file_path: Optional[str] = None, db_path: Optional[str] = None):
        if db_path is not None:
            self.db_path = db_path
            if file_path is None:
                # e.g. data/photo_index.db -> data/photo_index_taxonomy.json
                # if it is default data/photo_index.db, we want data/photo_taxonomy.json for compatibility
                if os.path.basename(db_path) == "photo_index.db":
                    self.file_path = os.path.join(os.path.dirname(db_path), "photo_taxonomy.json")
                else:
                    self.file_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
            else:
                self.file_path = file_path
        else:
            if file_path is None:
                file_path = "data/photo_taxonomy.json"
            self.file_path = file_path
            if file_path.endswith("photo_taxonomy.json"):
                self.db_path = os.path.join(os.path.dirname(file_path), "photo_index.db")
            elif file_path.endswith("_taxonomy.json"):
                self.db_path = file_path.replace("_taxonomy.json", ".db")
            else:
                self.db_path = os.path.splitext(file_path)[0] + ".db"
        # Store full paths of known hierarchical tags, e.g., {"Family/Immediate/Jane Doe", "Activity/Botanical Garden"}
        self.paths: Set[str] = set()
        # What the database held when this was loaded or last saved. save_to_db adds
        # only paths beyond these: everything else is either still there or was
        # deleted or renamed by someone else since, and must stay that way.
        self._in_db: Set[str] = set()

    def load(self):
        """Load taxonomy from database tag_taxonomy table, falling back to JSON file if DB doesn't have it."""
        self.paths = set()
        self._in_db = set()
        loaded_from_db = False
        
        # 1. Try to load from database
        if os.path.exists(self.db_path):
            try:
                conn = tagpup_db.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
                if cursor.fetchone():
                    cursor.execute("SELECT tag FROM tag_taxonomy")
                    for row in cursor.fetchall():
                        self.paths.add(row[0])
                    self._in_db = set(self.paths)
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
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            # Make sure table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tag_taxonomy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag TEXT UNIQUE,
                    parent_id INTEGER,
                    name TEXT,
                    has_face INTEGER DEFAULT 0,
                    hidden_from_autocomplete INTEGER DEFAULT 0,
                    FOREIGN KEY(parent_id) REFERENCES tag_taxonomy(id) ON DELETE CASCADE
                )
            """)
            
            # Insert the paths added since this was loaded, and their ancestors. Not
            # the whole set: a long-running indexer holds what it loaded, and a tag
            # deleted or renamed in the app meanwhile came back on its next save.
            added = self.paths - self._in_db
            for path in sorted(added):
                parts = self.normalize_tag(path).split("/")
                parent_id = None
                accumulated_path = ""
                for i, part in enumerate(parts):
                    if i == 0:
                        accumulated_path = part
                    else:
                        accumulated_path += "/" + part
                    
                    cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                    row = cursor.fetchone()
                    if row:
                        parent_id = row[0]
                    else:
                        is_p = 0
                        if i == 0:
                            if part.lower() in ["people", "family", "friends", "pets"]:
                                is_p = 1
                        else:
                            if parent_id is not None:
                                cursor.execute("SELECT has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                p_row = cursor.fetchone()
                                if p_row:
                                    is_p = p_row[0]
                        cursor.execute(
                            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                            (accumulated_path, parent_id, part, is_p)
                        )
                        parent_id = cursor.lastrowid
            conn.commit()
            conn.close()
            self._in_db |= added
        except Exception as e:
            logger.error(f"Error syncing taxonomy paths to DB: {e}")

    @staticmethod
    def normalize_tag(tag: str) -> str:
        """Normalize a tag by replacing common hierarchy separators (e.g. '|' or '\') with '/'."""
        tag = tag.strip()
        tag = tag.replace("|", "/").replace("\\", "/")  # not a path: tag hierarchy separators
        parts = [p.strip() for p in tag.split("/") if p.strip()]
        return "/".join(parts)

    def add_tag(self, tag: str):
        """Add a tag to the taxonomy, building all of its ancestor paths.

        A bare name that a people path already claims is not added as a root of its
        own. Photo keywords carry both forms in the wild -- a file may say
        "Cora Ingersoll" where the taxonomy says "People/Cora Ingersoll" -- and adding the
        bare one gives that person a second home, which is a choice nobody reading
        the Add Person list can make correctly. Only people are folded this way:
        "Kentridge" beside "School/Kentridge" is left alone, because deciding that
        for every tag is a different question and not this one.
        """
        normalized = self.normalize_tag(tag)
        if not normalized:
            return

        if "/" not in normalized and self.find_person_path(normalized):
            return

        parts = normalized.split("/")
        for i in range(1, len(parts) + 1):
            path = "/".join(parts[:i])
            self.paths.add(path)

    def add_tags(self, tags: List[str]):
        """Add multiple tags to the taxonomy."""
        for tag in tags:
            self.add_tag(tag)

    #: Roots that hold people. A library may use any of them, or its own.
    DEFAULT_PEOPLE_ROOTS = ("People", "Family", "Friends")

    def people_roots(self) -> Set[str]:
        """Lowercased roots this library files people under."""
        roots = {r.lower() for r in self.DEFAULT_PEOPLE_ROOTS}
        if getattr(self, "db_path", None):
            try:
                conn = tagpup_db.connect(self.db_path, timeout=30.0)
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='tag_taxonomy'"
                )
                if cur.fetchone():
                    cur.execute(
                        "SELECT name FROM tag_taxonomy "
                        "WHERE has_face = 1 AND tag NOT LIKE '%/%'"
                    )
                    for row in cur.fetchall():
                        if row[0]:
                            roots.add(row[0].strip().lower())
                conn.close()
            except Exception:
                pass
        return roots

    def find_person_path(self, name: str) -> Optional[str]:
        """An existing people path whose last segment is this name."""
        wanted = self.normalize_tag(name).split("/")[-1].strip().lower()
        if not wanted:
            return None
        roots = self.people_roots()
        for path in self.paths:
            if "/" not in path:
                continue
            if path.split("/")[0].strip().lower() not in roots:
                continue
            if path.split("/")[-1].strip().lower() == wanted:
                return path
        return None

    def find_by_leaf(self, name: str) -> Optional[str]:
        """The one existing path whose last segment is this name.

        None when the taxonomy has no such path, and also when it has two: a leaf
        under both "Trips/Boston MA" and "School/Boston MA" cannot be resolved without
        guessing which was meant, and a wrong guess files a photo under the wrong
        branch where nobody will look for it.
        """
        wanted = self.normalize_tag(name).split("/")[-1].strip().lower()
        if not wanted:
            return None
        found = None
        for path in self.paths:
            if path.split("/")[-1].strip().lower() != wanted:
                continue
            if found is not None and found != path:
                return None
            found = path
        return found

    def people_root(self) -> str:
        """The root this library files people under.

        Whichever of the usual people roots already exists, so a library using
        "Family" does not suddenly grow a "People" beside it. Falls back to People.
        """
        existing = {p.split("/")[0].strip().lower() for p in self.paths}
        for root in self.DEFAULT_PEOPLE_ROOTS:
            if root.lower() in existing:
                return root
        return self.DEFAULT_PEOPLE_ROOTS[0]

    def people_parent(self) -> str:
        """Where a newly seen person belongs, at the depth this library already uses.

        A library whose people live at Family/Immediate/<name> should not gain a
        Family/<name> beside them the first time somebody new turns up: that is a
        second, shallower home for people, which is the same fault as a bare root
        wearing different clothes. The commonest existing parent wins; the plain root
        is the fallback for a library with nobody in it yet.
        """
        roots = self.people_roots()
        parents = {}
        for path in self.paths:
            parts = path.split("/")
            if len(parts) < 2:
                continue
            if parts[0].strip().lower() not in roots:
                continue
            parent = "/".join(parts[:-1])
            parents[parent] = parents.get(parent, 0) + 1
        if not parents:
            return self.people_root()
        # Deepest among the most common, so a tie does not silently flatten.
        best = max(parents.items(), key=lambda kv: (kv[1], kv[0].count("/")))
        return best[0]

    def add_people(self, names: List[str]):
        """Record people in the taxonomy, under a people root.

        `names` are leaf names: extract_people flattens a hierarchical keyword down
        to the person it names, because that is the form used for display and for
        matching. Passing them to add_tags instead treated each as a whole path and
        minted a bare root node per person, beside the People/<name> the keyword had
        already created -- and it ran on every index, so every cleanup was undone by
        the next run.

        A name already somewhere in the taxonomy is left where it is: the point is to
        avoid a second home for it, not to move the first one.
        """
        root = self.people_parent()
        for name in names:
            normalized = self.normalize_tag(name)
            if not normalized:
                continue
            if "/" in normalized:
                self.add_tag(normalized)
                continue
            if self.find_person_path(normalized):
                continue
            self.add_tag("%s/%s" % (root, normalized))

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
        conn = tagpup_db.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        
        # Ensure tag_taxonomy table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tag_taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE,
                parent_id INTEGER,
                name TEXT,
                has_face INTEGER DEFAULT 0,
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
            is_p = 1 if category.lower() in ["people", "pets"] else 0
            try:
                cursor.execute(
                    "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, NULL, ?, ?)",
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
                    
                    cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                    row = cursor.fetchone()
                    if row:
                        parent_id = row[0]
                    else:
                        is_p = 0
                        if i == 0:
                            if part.lower() in ["people", "family", "friends", "pets"]:
                                is_p = 1
                        else:
                            if parent_id is not None:
                                cursor.execute("SELECT has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                p_row = cursor.fetchone()
                                if p_row:
                                    is_p = p_row[0]
                        cursor.execute(
                            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
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
                        "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, 1)",
                        (path, people_root_id, name)
                    )
                    
        # Check if taxonomy json file exists and seed from there too
        if os.path.basename(db_path) == "photo_index.db":
            tax_path = os.path.join(os.path.dirname(db_path), "photo_taxonomy.json")
        else:
            tax_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
            
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
                        
                        cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
                        row = cursor.fetchone()
                        if row:
                            parent_id = row[0]
                        else:
                            is_p = 0
                            if i == 0:
                                if part.lower() in ["people", "family", "friends", "pets"]:
                                    is_p = 1
                            else:
                                if parent_id is not None:
                                    cursor.execute("SELECT has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                                    p_row = cursor.fetchone()
                                    if p_row:
                                        is_p = p_row[0]
                            cursor.execute(
                                "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
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

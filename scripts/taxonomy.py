# taxonomy.py
import os
import json
import logging
from typing import Set, List, Dict, Union

logger = logging.getLogger("tagpup.taxonomy")

class TagTaxonomy:
    def __init__(self, file_path: str = "data/photo_taxonomy.json"):
        self.file_path = file_path
        # Store full paths of known hierarchical tags, e.g., {"Family/Immediate/Laurel Idzi", "Activity/Botanical Garden"}
        self.paths: Set[str] = set()

    def load(self):
        """Load taxonomy from JSON file."""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.paths = set(data.get("paths", []))
                logger.info(f"Loaded taxonomy with {len(self.paths)} paths.")
            except Exception as e:
                logger.error(f"Error loading taxonomy: {e}")
                self.paths = set()
        else:
            self.paths = set()

    def save(self):
        """Save taxonomy to JSON file."""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"paths": sorted(list(self.paths))}, f, indent=2)
            logger.info(f"Saved taxonomy with {len(self.paths)} paths.")
        except Exception as e:
            logger.error(f"Error saving taxonomy: {e}")

    @staticmethod
    def normalize_tag(tag: str) -> str:
        """Normalize a tag by replacing common hierarchy separators (e.g. '|' or '\') with '/'."""
        tag = tag.strip()
        # Replace pipe and backslash separators with standard forward slash
        tag = tag.replace("|", "/").replace("\\", "/")
        # Remove consecutive slashes and strip outer slashes
        parts = [p.strip() for p in tag.split("/") if p.strip()]
        return "/".join(parts)

    def add_tag(self, tag: str):
        """Add a tag to the taxonomy, building all of its ancestor paths."""
        normalized = self.normalize_tag(tag)
        if not normalized:
            return
            
        parts = normalized.split("/")
        # Add all ancestor paths (e.g., for A/B/C, add A, A/B, and A/B/C)
        for i in range(1, len(parts) + 1):
            path = "/".join(parts[:i])
            self.paths.add(path)

    def add_tags(self, tags: List[str]):
        """Add multiple tags to the taxonomy."""
        for tag in tags:
            self.add_tag(tag)

    def expand_tag(self, tag: str) -> List[str]:
        """Given a tag, if it matches a path in the taxonomy, expand it to include all ancestors.
        Example: 'Family/Immediate/Laurel' -> ['Family', 'Family/Immediate', 'Family/Immediate/Laurel']
        """
        normalized = self.normalize_tag(tag)
        if not normalized:
            return []
            
        # Find if this tag or any path ending with this tag exists in our taxonomy
        # If the tag is already a full path or matches one of the paths, expand it.
        results = []
        
        # If it's a direct match or we can find a matching path ending with it:
        matched_path = None
        if normalized in self.paths:
            matched_path = normalized
        else:
            # Check if any path in the taxonomy ends with the query (e.g. searching 'Laurel' matches 'Family/Immediate/Laurel')
            # Sort by length descending to match the most specific path first
            sorted_paths = sorted(list(self.paths), key=len, reverse=True)
            for p in sorted_paths:
                parts = p.split("/")
                # Check if query matches the leaf or end segments
                if normalized == parts[-1] or p.endswith("/" + normalized):
                    matched_path = p
                    break
        
        if matched_path:
            parts = matched_path.split("/")
            for i in range(1, len(parts) + 1):
                results.append("/".join(parts[:i]))
        else:
            # If not found in taxonomy, return the normalized query segments
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

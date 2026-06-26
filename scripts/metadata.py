# metadata.py
import os
import logging
from typing import List, Dict, Any, Optional
import exiftool

logger = logging.getLogger("tagpup.metadata")

# Define target fields mapped to keys we want to return
# ExifTool output keys can be namespaced or bare (without prefix).
# We check both to be safe.
METADATA_FIELDS = [
    # Keywords / tags
    "IPTC:Keywords", "Keywords",
    "XMP:Subject", "Subject",
    "XMP:HierarchicalSubject", "HierarchicalSubject",
    # People / faces
    "XMP:PersonInImage", "PersonInImage",
    "XMP:RegionName", "RegionName",
    # Caption
    "IPTC:Caption-Abstract", "Caption-Abstract",
    "XMP:Description", "Description",
    # Title
    "XMP:Title", "Title",
    "IPTC:ObjectName", "ObjectName",
    # Date taken
    "EXIF:DateTimeOriginal", "DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate",
    # Location
    "XMP:City", "City",
    "XMP:State", "State",
    "XMP:Country", "Country",
    "IPTC:Province-State", "Province-State",
    "IPTC:Country-PrimaryLocationName", "Country-PrimaryLocationName",
    # GPS
    "Composite:GPSLatitude", "GPSLatitude",
    "Composite:GPSLongitude", "GPSLongitude",
    # Camera
    "EXIF:Make", "Make",
    "EXIF:Model", "Model",
    # Rating
    "XMP:Rating", "Rating"
]

def clean_metadata_value(val: Any) -> Any:
    """Helper to convert ExifTool structures (like list of dicts, single element list, binary data etc) to simple types."""
    if val is None:
        return None
    if isinstance(val, list):
        # Convert lists of single items to that item, otherwise recursively clean items
        cleaned = [clean_metadata_value(v) for v in val if v is not None]
        if len(cleaned) == 0:
            return None
        if len(cleaned) == 1:
            return cleaned[0]
        return cleaned
    if isinstance(val, dict):
        # ExifTool sometimes returns structs, e.g. for GPS or XMP structures. Keep string values or extract if simple
        if "value" in val:
            return clean_metadata_value(val["value"])
        return {k: clean_metadata_value(v) for k, v in val.items()}
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="ignore")
        except Exception:
            return str(val)
    return val

def extract_tags(meta: Dict[str, Any]) -> List[str]:
    """Extract standard and hierarchical tags into a flat list of strings.
    Also handles taxonomy parsing."""
    tags = []
    
    # Subject / Keywords
    for key in ["XMP:Subject", "Subject", "IPTC:Keywords", "Keywords"]:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                tags.extend([str(v).strip() for v in val if v])
            else:
                tags.append(str(val).strip())
                
    # Hierarchical Subject
    for key in ["XMP:HierarchicalSubject", "HierarchicalSubject"]:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                tags.extend([str(v).strip() for v in val if v])
            else:
                tags.append(str(val).strip())
                
    # Normalize/deduplicate and filter out empty strings
    seen = set()
    unique_tags = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique_tags.append(t)
    return unique_tags

def extract_people(meta: Dict[str, Any], tags: List[str]) -> List[str]:
    """Extract people tags from PersonInImage or RegionName, and also from hierarchical tags starting with Family/ or Friends/."""
    people = []
    for key in ["XMP:PersonInImage", "PersonInImage", "XMP:RegionName", "RegionName"]:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                people.extend([str(v).strip() for v in val if v])
            else:
                people.append(str(val).strip())
                
    # Extract person name from hierarchical tags starting with Family or Friends
    for tag in tags:
        normalized = tag.replace("|", "/").replace("\\", "/")
        parts = [p.strip() for p in normalized.split("/") if p.strip()]
        if len(parts) >= 2:
            root = parts[0].lower()
            if root in ["family", "friends"]:
                # The leaf node of the tag path is the name of the person
                people.append(parts[-1])
                
    seen = set()
    unique_people = []
    for p in people:
        if p and p not in seen:
            seen.add(p)
            unique_people.append(p)
    return unique_people

def extract_captions(meta: Dict[str, Any]) -> List[str]:
    """Extract titles, captions, and descriptions."""
    captions = []
    for key in ["IPTC:Caption-Abstract", "Caption-Abstract", "XMP:Description", "Description", 
                "XMP:Title", "Title", "IPTC:ObjectName", "ObjectName"]:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                captions.extend([str(v).strip() for v in val if v])
            else:
                captions.append(str(val).strip())
    return [c for c in captions if c]

class MetadataExtractor:
    def __init__(self, exiftool_path: Optional[str] = None):
        self.exiftool_path = exiftool_path

    def batch_read(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Read metadata for a batch of files using pyexiftool."""
        if not file_paths:
            return []

        # Configure pyexiftool path if provided
        executable = self.exiftool_path
        if executable and not os.path.isabs(executable):
            # Try to resolve relative path to absolute
            executable = os.path.abspath(executable)

        results = []
        try:
            # We initialize pyexiftool client
            # ExifToolHelper manages the lifecycle
            with exiftool.ExifToolHelper(executable=executable) as et:
                # Read specific fields we care about
                # Passing tag names directly
                batch_meta = et.get_tags(file_paths, tags=METADATA_FIELDS)
                
                # Check mapping to return formatted info
                for path, meta in zip(file_paths, batch_meta):
                    # Clean all fields
                    cleaned = {}
                    for k, v in meta.items():
                        # ExifTool returns keys like 'SourceFile', 'XMP:Subject', etc.
                        cleaned[k] = clean_metadata_value(v)
                    
                    # Extract high-level aggregated lists
                    tags = extract_tags(cleaned)
                    people = extract_people(cleaned, tags)
                    captions = extract_captions(cleaned)
                    
                    # Retrieve file stats for change detection
                    try:
                        stat = os.stat(path)
                        mtime = stat.st_mtime
                        size = stat.st_size
                    except Exception:
                        mtime = 0.0
                        size = 0

                    # Create structured output
                    structured = {
                        "path": path,
                        "mtime": mtime,
                        "size": size,
                        "tags": tags,
                        "people": people,
                        "captions": captions,
                        "raw_metadata": cleaned
                    }
                    results.append(structured)
        except Exception as e:
            logger.error(f"Error reading metadata from batch: {e}", exc_info=True)
            # Return empty skeleton configs for files that failed to read so we don't break downstream flow
            for path in file_paths:
                results.append({
                    "path": path,
                    "tags": [],
                    "people": [],
                    "captions": [],
                    "raw_metadata": {}
                })
        
        return results

def parse_year_from_metadata(meta: Dict[str, Any]) -> Optional[int]:
    """Extract a 4-digit numeric year from EXIF/XMP date tags, or fallback to filename/folder."""
    import re
    import os
    date_keys = [
        "EXIF:DateTimeOriginal", "DateTimeOriginal",
        "XMP:DateTimeOriginal",
        "EXIF:CreateDate", "CreateDate"
    ]
    raw_meta = meta.get("raw_metadata", meta)
    if raw_meta:
        for key in date_keys:
            val = raw_meta.get(key)
            if val:
                if isinstance(val, list) and val:
                    val = val[0]
                val_str = str(val).strip()
                match = re.match(r"^(\d{4})", val_str)
                if match:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        return year

    def extract_year(s):
        if not s:
            return None
        matches = re.findall(r'\d{4}', s)
        for m in matches:
            val = int(m)
            if 1800 <= val <= 2100:
                return val
        return None

    path = meta.get("path")
    if path:
        # Normalize separators
        norm_path = path.replace("\\", "/")
        parts = norm_path.split("/")
        
        # Check filename
        if parts:
            filename = parts[-1]
            year = extract_year(filename)
            if year:
                return year
                
        # Check folders from right to left
        if len(parts) > 1:
            for folder in reversed(parts[:-1]):
                if not folder:
                    continue
                year = extract_year(folder)
                if year:
                    return year
                    
    return None

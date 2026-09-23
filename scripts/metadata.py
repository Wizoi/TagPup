# metadata.py
import os
try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db
try:
    from . import paths
except ImportError:  # imported as a top-level module
    import paths
import logging
from pathlib import PurePath
from typing import List, Dict, Any, Optional, Set
from exiftool_session import ExifToolSession

from identity import ensure_document_id, read_document_id

logger = logging.getLogger("tagpup_cli.metadata")

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
    "XMP:Rating", "Rating",
    # Identity. A path is a bad name for a photo -- rename the file and the index is
    # left describing something that no longer exists. DocumentID is the XMP
    # standard's per-document identifier, and most photos already carry one.
    "XMP-xmpMM:DocumentID", "XMP:DocumentID", "DocumentID"
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
            
    # Clean up redundant flat leaf or prefix nodes of hierarchical tags
    hierarchical_tags = [t for t in unique_tags if "/" in t]
    to_remove = set()
    for h in hierarchical_tags:
        parts = h.split("/")
        for part in parts:
            to_remove.add(part.strip())
            
    cleaned_tags = []
    for t in unique_tags:
        if "/" in t or t not in to_remove:
            cleaned_tags.append(t)
            
    return cleaned_tags

def get_people_roots(db_path: Optional[str] = None, conn: Any = None) -> Set[str]:
    """Retrieve lowercase names of all root categories marked as People from database."""
    roots = {"family", "friends", "people"}  # Default fallbacks
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT name FROM tag_taxonomy WHERE (parent_id IS NULL OR tag NOT LIKE '%/%') AND has_face = 1")
                for row in cursor.fetchall():
                    if row[0]:
                        roots.add(row[0].lower().strip())
        except Exception:
            pass
    elif db_path and os.path.exists(db_path):
        try:
            conn_temp = tagpup_db.connect(db_path, timeout=5.0)
            cursor = conn_temp.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT name FROM tag_taxonomy WHERE (parent_id IS NULL OR tag NOT LIKE '%/%') AND has_face = 1")
                for row in cursor.fetchall():
                    if row[0]:
                        roots.add(row[0].lower().strip())
            conn_temp.close()
        except Exception:
            pass
    return roots

def extract_people(meta: Dict[str, Any], tags: List[str], db_path: Optional[str] = None, conn: Any = None) -> List[str]:
    """Extract people tags from PersonInImage or RegionName, and also from hierarchical tags starting with People/ or custom designated categories."""
    people = []
    for key in ["XMP:PersonInImage", "PersonInImage", "XMP:RegionName", "RegionName"]:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                people.extend([str(v).strip() for v in val if v])
            else:
                people.append(str(val).strip())
                
    people_roots = get_people_roots(db_path, conn=conn)
    
    # Extract person name from hierarchical tags starting with any people roots
    for tag in tags:
        normalized = tag.replace("|", "/").replace("\\", "/")  # not a path: a keyword hierarchy
        parts = [p.strip() for p in normalized.split("/") if p.strip()]
        if len(parts) >= 2:
            root = parts[0].lower()
            if root in people_roots:
                # The leaf node of the tag path is the name of the person
                people.append(parts[-1])

    # Also resolve flat tags (e.g. "Cora Ingersoll") that exist in the taxonomy as a face category
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1")
                people_tags = cursor.fetchall()
                for tag in tags:
                    norm = tag.replace("\\", "/").strip()  # not a path: a keyword hierarchy
                    for db_tag, db_name in people_tags:
                        # A face ROOT (People, Family, Pets, ...) is a category, not a person,
                        # so a photo tagged plainly "Family" must not gain a name.
                        if db_name and db_name.lower() in people_roots and "/" not in db_tag:
                            continue
                        if norm.lower() == db_tag.lower() or norm.lower() == db_name.lower():
                            people.append(db_name)
                            break
        except Exception as e:
            logger.warning(f"Error resolving people from database taxonomy: {e}")
    else:
        # No implicit fallback to a hard-coded database: silently resolving people
        # against the default library would apply one database's taxonomy to another.
        # Callers that need taxonomy resolution pass db_path or an open connection.
        if db_path and os.path.exists(db_path):
            try:
                conn_temp = tagpup_db.connect(db_path, timeout=5.0)
                cursor = conn_temp.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
                if cursor.fetchone():
                    cursor.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1")
                    people_tags = cursor.fetchall()
                    for tag in tags:
                        norm = tag.replace("\\", "/").strip()  # not a path: a keyword hierarchy
                        for db_tag, db_name in people_tags:
                            # A face ROOT (People, Family, Pets, ...) is a category, not a person,
                            # so a photo tagged plainly "Family" must not gain a name.
                            if db_name and db_name.lower() in people_roots and "/" not in db_tag:
                                continue
                            if norm.lower() == db_tag.lower() or norm.lower() == db_name.lower():
                                people.append(db_name)
                                break
                conn_temp.close()
            except Exception as e:
                logger.warning(f"Error resolving people from database taxonomy: {e}")
                
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
    def __init__(self, exiftool_path: Optional[str] = None, mint_identities: bool = True):
        self.exiftool_path = exiftool_path
        #: Write an identity into photos that lack one, as they are read.
        #:
        #: On by default because the cost is small and the alternative is losing work:
        #: without an identity a renamed photo strands its index row, and the row holds
        #: the faces somebody named by hand. Pass False where the files must not be
        #: written -- a read-only pass over somebody else's library, or a test.
        self.mint_identities = mint_identities

    def batch_read(self, file_paths: List[str], db_path: Optional[str] = None) -> List[Dict[str, Any]]:
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
            # ExifToolSession manages the lifecycle, and cannot stall on stderr
            with ExifToolSession(executable=executable) as et:
                # Read specific fields we care about
                # Passing tag names directly
                batch_meta = et.get_tags(file_paths, tags=METADATA_FIELDS)
                
                # Check mapping to return formatted info
                for path, meta in zip(file_paths, batch_meta):
                    results.append(self._structure(path, meta, db_path))

                if self.mint_identities:
                    self._give_identities(et, results)
        except Exception as e:
            # ExifTool exits non-zero if *any* file in the batch is unreadable, and
            # pyexiftool raises on that status, so a single corrupt or unsupported
            # file used to cost every other file in the batch its metadata -- 500 at
            # a time, recorded as having no tags, people or captions at all. Read them
            # one at a time instead, so the damage is limited to the file that caused it.
            logger.warning(
                f"Batch metadata read failed ({e}); retrying {len(file_paths)} file(s) "
                f"individually so one bad file does not blank the rest."
            )
            results = self._read_one_by_one(file_paths, executable, db_path)

        return results

    def _structure(self, path, meta, db_path):
        """Turn one ExifTool record into the shape the rest of the pipeline expects."""
        cleaned = {}
        for k, v in meta.items():
            # ExifTool returns keys like 'SourceFile', 'XMP:Subject', etc.
            val_cleaned = clean_metadata_value(v)
            cleaned[k] = val_cleaned
            if ":" in k:
                cleaned[k.split(":")[-1]] = val_cleaned

        tags = extract_tags(cleaned)
        people = extract_people(cleaned, tags, db_path=db_path)
        captions = extract_captions(cleaned)

        # Retrieve file stats for change detection
        try:
            stat = os.stat(path)
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            mtime, size = 0.0, 0

        return {
            "path": path,
            "mtime": mtime,
            "size": size,
            "tags": tags,
            "people": people,
            "captions": captions,
            "raw_metadata": cleaned,
            "document_id": read_document_id(cleaned),
        }

    def _give_identities(self, et, records):
        """Write an identity into any photo that has none.

        A path is a bad name for a photo: rename it and the index describes something
        that no longer exists, while the photo looks unindexed. DocumentID is the XMP
        standard's per-document identifier and most photos already carry one, so this
        writes to very few files -- 54 of 1,129 in this library. Those that already
        have one are not touched.

        Failures are per-file and logged, never raised: an identity is an improvement
        on knowing only the path, and a photo that cannot take one indexes perfectly
        well without it.
        """
        for record in records:
            if record.get("document_id"):
                continue
            minted = ensure_document_id(et, record["path"], record.get("raw_metadata"))
            if not minted:
                continue
            record["document_id"] = minted
            record.setdefault("raw_metadata", {})["XMP:DocumentID"] = minted
            # The file changed, so the stats recorded for change detection must be the
            # ones it has now -- otherwise the next pass sees a modified file and
            # re-indexes it for a write this pass made.
            try:
                stat = os.stat(record["path"])
                record["mtime"], record["size"] = stat.st_mtime, stat.st_size
            except Exception:
                pass

    def _read_one_by_one(self, file_paths, executable, db_path):
        """Fallback for a failed batch: read each file on its own.

        A file that fails here is genuinely unreadable rather than merely unlucky in
        its batch, so it gets the empty skeleton -- but it still carries its mtime and
        size, or every later run would see it as changed and re-index it forever.
        """
        results = []
        try:
            et = ExifToolSession(executable=executable)
            et.run()
        except Exception as e:
            logger.error(f"Could not start ExifTool for the per-file retry: {e}")
            return [self._empty(path) for path in file_paths]

        try:
            for path in file_paths:
                try:
                    meta = et.get_tags([path], tags=METADATA_FIELDS)
                    results.append(self._structure(path, meta[0], db_path))
                except Exception as e:
                    logger.error(f"Unreadable metadata, indexing without it: {path} ({e})")
                    results.append(self._empty(path))
        finally:
            try:
                et.terminate()
            except Exception:
                pass
        return results

    @staticmethod
    def _empty(path):
        try:
            stat = os.stat(path)
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            mtime, size = 0.0, 0
        return {
            "path": path,
            "mtime": mtime,
            "size": size,
            "tags": [],
            "people": [],
            "captions": [],
            "raw_metadata": {},
        }

def parse_year_from_metadata(meta: Dict[str, Any]) -> Optional[int]:
    """Extract a 4-digit numeric year from EXIF/XMP date tags, or fallback to filename/folder."""
    import re
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
        # The path's own components, whichever separators it was spelled with.
        parts = PurePath(path).parts

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


def build_photo_ui_record(path: str, meta: Dict[str, Any], mtime: float = 0.0, size: int = 0) -> Dict[str, Any]:
    """Builds a standardized dictionary of photo attributes for the GUI frontend.

    "path" is the stored spelling whatever the caller had, so the browser only ever
    sees one spelling of a photo and hands back the one the index uses.
    """
    path = paths.stored(path)
    tags = meta.get("tags", [])
    people = meta.get("people", [])
    raw_meta = meta.get("raw_metadata", {})
    captions = meta.get("captions", [])
    title = captions[0] if captions else ""

    year = parse_year_from_metadata(meta)
    year_str = str(year) if year is not None else "Unknown"

    return {
        "path": path,
        "filename": os.path.basename(path),
        "tags": tags,
        "people": people,
        "title": title,
        "mtime": mtime,
        "size": size,
        "year": year_str,
        "raw_metadata": raw_meta
    }


def rotate_image_file(photo_path: str, direction: str, exiftool_path: Optional[str] = None) -> None:
    """Rotates the image at photo_path 90 degrees CCW (left) or CW (right)
    and preserves EXIF data while resetting Orientation tag to 1."""
    from PIL import Image, ImageOps
    with Image.open(photo_path) as img:
        exif_bytes = img.info.get('exif')
        img_transposed = ImageOps.exif_transpose(img)
        angle = 90 if direction == "left" else 270
        rotated = img_transposed.rotate(angle, expand=True)
        if exif_bytes:
            rotated.save(photo_path, exif=exif_bytes, quality=95)
        else:
            rotated.save(photo_path, quality=95)

    if exiftool_path:
        import exiftool
        try:
            with exiftool.ExifToolHelper(executable=exiftool_path) as et:
                et.set_tags([photo_path], tags={"Orientation": 1}, params=["-overwrite_original"])
        except Exception:
            pass


def sanitize_filename(name: str) -> str:
    """Removes or replaces invalid filesystem characters to make the filename safe."""
    invalid_chars = '<>:"/\\|?*'
    for c in invalid_chars:
        name = name.replace(c, '_')
    # Filter printable characters and strip
    name = "".join(ch for ch in name if ch.isprintable())
    return name.strip()


def sync_title_to_filename(photo_path: str, new_title: str, exiftool_path: str) -> str:
    """If the photo has an XMP-xmpMM:PreservedFileName tag set, automatically syncs 
    any changes to the title back into the filename structure.
    Returns the new path if renamed, or the original path if not renamed."""
    if not os.path.exists(photo_path):
        return photo_path

    import exiftool
    try:
        with exiftool.ExifToolHelper(executable=exiftool_path) as et:
            meta = et.get_tags([photo_path], tags=["XMP-xmpMM:PreservedFileName", "XMP:PreservedFileName"])
            meta_dict = meta[0] if meta else {}
            
        preserved = meta_dict.get("XMP-xmpMM:PreservedFileName") or meta_dict.get("XMP:PreservedFileName")
        if not preserved:
            # Not renamed in this way, do nothing
            return photo_path
            
        # Parse current filename structure
        base_name, ext = os.path.splitext(os.path.basename(photo_path))
        parts = [p.strip() for p in base_name.split(" - ")]
        
        if len(parts) >= 2:
            grouping = parts[0]
            index_str = parts[1]
            
            # Read format from config.ini
            import configparser
            config = configparser.ConfigParser()
            config_path = "config.ini"
            format_pattern = "{grouping} - {index} - {caption}"
            if os.path.exists(config_path):
                config.read(config_path)
                if config.has_section("renaming") and config.has_option("renaming", "format"):
                    format_pattern = config.get("renaming", "format")
            
            # Format new name
            new_title_clean = str(new_title).strip()
            new_base = format_pattern.replace("{grouping}", grouping).replace("{index}", index_str)
            if new_title_clean:
                new_base = new_base.replace("{caption}", new_title_clean)
            else:
                new_base = new_base.replace(" - {caption}", "").replace("- {caption}", "").replace("{caption}", "")
                
            # Sanitize
            new_base = sanitize_filename(new_base)
            new_name = new_base + ext
            
            new_path = os.path.join(os.path.dirname(photo_path), new_name)
            
            if photo_path != new_path:
                # Handle potential collision
                if os.path.exists(new_path):
                    base_part, ext_part = os.path.splitext(new_name)
                    counter = 1
                    while os.path.exists(os.path.join(os.path.dirname(photo_path), f"{base_part}_{counter}{ext_part}")):
                        counter += 1
                    new_name = f"{base_part}_{counter}{ext_part}"
                    new_path = os.path.join(os.path.dirname(photo_path), new_name)
                
                os.rename(photo_path, new_path)
                return new_path
                
    except Exception as e:
        import logging
        logging.getLogger("metadata").error(f"Error syncing title to filename: {e}")
        
    return photo_path


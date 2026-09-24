# writer.py
import os
import json
import logging
from typing import List, Optional
from exiftool_session import ExifToolSession

import _root  # noqa: F401
from tagpup.core import suggesting, vocabulary
from tagpup.files.keywords import caption_fields
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.writer")

def derive_caption_from_tags(tags: List[str], face_roots) -> Optional[str]:
    """Derive a clean, readable caption based directly on the hierarchical/flat tags.

    `face_roots` are the library's roots that hold people, lowercased, as its tree flags
    them (taxonomy.people_roots): this listed family and friends, and put everyone under
    People among the others (docs/findings.md, #66)."""
    if not tags:
        return None
        
    # The roots are tagpup.core.vocabulary's, as a new library is given them.
    activity_root = vocabulary.ACTIVITY_ROOT.lower()
    place_roots = [root.lower() for root in vocabulary.PLACE_ROOTS]
    people = []
    activities = []
    places = {root: [] for root in place_roots}
    others = []
    
    # Sort tags to ensure consistent, deterministic ordering (e.g. alphabetical)
    sorted_tags = sorted(list(set(tags)))
    
    for tag in sorted_tags:
        leaf = vocabulary.leaf_of(tag)
        root = vocabulary.root_of(tag).lower()
        
        if root in face_roots:
            people.append(leaf)
        elif root == activity_root:
            activities.append(leaf)
        elif root in places:
            places[root].append(leaf)
        else:
            others.append(leaf)
            
    # Remove duplicates from lists while preserving order
    def unique_list(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]
        
    people = unique_list(people)
    activities = unique_list(activities)
    loc_list = [leaf for root in place_roots for leaf in unique_list(places[root])]
    others = unique_list(others)

    if not people and not activities and not loc_list and not others:
        return None
        
    # Helper to join list with commas and 'and'
    def format_list(lst):
        if not lst:
            return ""
        if len(lst) == 1:
            return lst[0]
        if len(lst) == 2:
            return f"{lst[0]} and {lst[1]}"
        return ", ".join(lst[:-1]) + f", and {lst[-1]}"
        
    people_str = format_list(people)
    activity_str = format_list(activities)
    loc_str = format_list(loc_list)
    
    if people_str:
        caption = people_str
        if activity_str:
            caption += f" - {activity_str}"
        if loc_str:
            caption += f", {loc_str}"
    else:
        # No people in the tags
        if activity_str:
            caption = activity_str
            if loc_str:
                caption += f", {loc_str}"
        elif loc_str:
            caption = loc_str
        else:
            caption = format_list(others)
            
    return caption

def record_caption_in_index(db_path, photo_path, caption):
    """The caption just written, in the photo's index row."""
    import db as tagpup_db
    from tagpup.store import photos as store_photos

    return tagpup_db.write_with_connection(
        db_path, lambda conn: store_photos.set_captions(conn, photo_path, [caption]),
        label="caption for %s" % photo_path)


class MetadataWriter:
    def __init__(self, exiftool_path: Optional[str] = None):
        self.exiftool_path = exiftool_path

    def write_tags_to_photos(
        self, 
        suggestions_file: str, 
        live: bool = False, 
        min_score: float = suggesting.OFFER_A_TAG,
        nobackup: bool = False,
        db_path: Optional[str] = None
    ) -> bool:
        """Read suggestions from suggestions.json, filter by min_score, and write to files using ExifTool.

        `db_path` is the library: people are filed by its taxonomy and its index is told
        what was written. Without it keywords are still written whole, and nothing
        is recorded.
        """
        if not os.path.exists(suggestions_file):
            logger.error(f"Suggestions file not found: {suggestions_file}")
            return False

        try:
            with open(suggestions_file, "r", encoding="utf-8") as f:
                suggestions = json.load(f)
        except Exception as e:
            logger.error(f"Error loading suggestions file: {e}")
            return False

        if not isinstance(suggestions, list):
            # Might be a single entry wrapped or just invalid
            if isinstance(suggestions, dict):
                suggestions = [suggestions]
            else:
                logger.error("Invalid suggestions.json format. Expected array of objects.")
                return False

        # Filter suggestions and prepare write tasks
        write_tasks = []
        for entry in suggestions:
            path = entry.get("path")
            if not path or not os.path.exists(path):
                logger.warning(f"File path does not exist, skipping: {path}")
                continue

            suggested_tags = entry.get("suggested_tags", [])
            
            # Filter by score
            filtered_tags = [t["tag"] for t in suggested_tags if t.get("score", 0.0) >= min_score]
            
            # Derive caption dynamically from the filtered tags
            derived_caption = derive_caption_from_tags(
                filtered_tags, store_taxonomy.people_vocabulary(db_path).roots)
            
            if filtered_tags or derived_caption:
                write_tasks.append((path, filtered_tags, derived_caption))

        if not write_tasks:
            print("No tags or captions met the minimum score threshold to be written.")
            return True

        # Print summary/preview
        print("\n--- Tag & Caption Writing Preview ---")
        for path, tags, caption in write_tasks:
            print(f"File: {path}")
            if tags:
                print(f"  Tags to append: {', '.join(tags)}")
            if caption:
                print(f"  Caption to set: \"{caption}\"")
        print(f"Total files to modify: {len(write_tasks)}")
        print(f"Write Mode: {'LIVE (files will be modified)' if live else 'PREVIEW (dry-run, no files changed)'}")
        print("-------------------------------------")

        if not live:
            print("To write these tags and captions for real, run with the -Live flag.")
            return True

        # Ask for confirmation
        confirm = input("Type 'YES' to confirm and write metadata to files: ").strip()
        if confirm != "YES":
            print("Aborted. No files were modified.")
            return False

        print("Writing metadata...")
        executable = self.exiftool_path
        if executable and not os.path.isabs(executable):
            executable = os.path.abspath(executable)

        success_count = 0
        error_count = 0

        # We will write tags to XMP:Subject, IPTC:Keywords, and XMP:HierarchicalSubject
        # and captions to XMP:Description and IPTC:Caption-Abstract
        from tagpup_server import (record_file_stat_in_index, record_tags_in_index,
                                   tags_in_file, write_keyword_fields)
        from tagpup.store.embeddings import stamp_of

        try:
            with ExifToolSession(executable=executable) as et:
                for path, tags, caption in write_tasks:
                    try:
                        # The file's stamp before this write, over which its CLIP
                        # vectors are carried (tagpup.store.embeddings).
                        before = stamp_of(path)
                        # The caption first, so the stat recorded with the keywords
                        # below is the file's final one.
                        if caption:
                            et.set_tags([path], tags=caption_fields(caption),
                                        params=["-overwrite_original"] if nobackup else None)
                            if db_path:
                                record_caption_in_index(db_path, path, caption)

                        if tags:
                            # Through the one keyword writer: whole paths only, people
                            # filed where the taxonomy files them, starting from what
                            # the file holds. This had its own ExifTool code that also
                            # wrote every path's parts as loose keywords, wrote people
                            # bare, and never told the index.
                            current = tags_in_file(et, path)
                            merged = current + [t for t in tags if t not in current]
                            flat, hierarchical = write_keyword_fields(et, path, merged, db_path=db_path)
                            if db_path:
                                record_tags_in_index(db_path, path, flat, flat, hierarchical, before=before)
                        elif caption and db_path:
                            record_file_stat_in_index(db_path, path, before=before)
                        success_count += 1
                    except Exception as e:
                        logger.error(f"Failed to write metadata to {path}: {e}")
                        error_count += 1
        except Exception as e:
            logger.error(f"ExifTool writer error: {e}", exc_info=True)
            return False

        print(f"Finished writing metadata. Success: {success_count}, Errors: {error_count}")
        return error_count == 0

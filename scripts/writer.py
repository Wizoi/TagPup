# writer.py
import os
import json
import logging
from typing import List, Dict, Any, Optional
import exiftool

logger = logging.getLogger("tagpup.writer")

def derive_caption_from_tags(tags: List[str]) -> Optional[str]:
    """Derive a clean, readable caption based directly on the hierarchical/flat tags."""
    if not tags:
        return None
        
    people = []
    activities = []
    schools = []
    trips = []
    others = []
    
    # Sort tags to ensure consistent, deterministic ordering (e.g. alphabetical)
    sorted_tags = sorted(list(set(tags)))
    
    for tag in sorted_tags:
        parts = tag.split("/")
        leaf = parts[-1]
        root = parts[0].lower()
        
        if root in ["family", "friends"]:
            people.append(leaf)
        elif root == "activity":
            activities.append(leaf)
        elif root == "school":
            schools.append(leaf)
        elif root == "trips":
            trips.append(leaf)
        else:
            others.append(leaf)
            
    # Remove duplicates from lists while preserving order
    def unique_list(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]
        
    people = unique_list(people)
    activities = unique_list(activities)
    schools = unique_list(schools)
    trips = unique_list(trips)
    others = unique_list(others)
    
    if not people and not activities and not schools and not trips and not others:
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
    loc_list = schools + trips
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

class MetadataWriter:
    def __init__(self, exiftool_path: Optional[str] = None):
        self.exiftool_path = exiftool_path

    def write_tags_to_photos(
        self, 
        suggestions_file: str, 
        live: bool = False, 
        min_score: float = 0.50
    ) -> bool:
        """Read suggestions from suggestions.json, filter by min_score, and write to files using ExifTool."""
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
            derived_caption = derive_caption_from_tags(filtered_tags)
            
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
        try:
            with exiftool.ExifToolHelper(executable=executable) as et:
                for path, tags, caption in write_tasks:
                    try:
                        params = {}
                        
                        if tags:
                            flat_tags = []
                            hierarchical_tags = []
                            for tag in tags:
                                flat_tags.append(tag)
                                if "/" in tag:
                                    hierarchical_tags.append(tag)
                                    for part in tag.split("/"):
                                        flat_tags.append(part)
                                        
                            flat_tags = sorted(list(set(flat_tags)))
                            hierarchical_tags = sorted(list(set(hierarchical_tags)))

                            if flat_tags:
                                params["XMP:Subject+"] = flat_tags
                                params["IPTC:Keywords+"] = flat_tags
                                # Windows-specific XPKeywords requires a semicolon-separated string
                                params["EXIF:XPKeywords"] = ";".join(flat_tags)
                            if hierarchical_tags:
                                params["XMP:HierarchicalSubject+"] = hierarchical_tags
                        
                        if caption:
                            params["XMP:Description"] = caption
                            params["IPTC:Caption-Abstract"] = caption
                            # EXIF ImageDescription maps to System.Title (Title) in C# code
                            params["EXIF:ImageDescription"] = caption
                            # EXIF XPComment maps to System.Comment (Caption) in C# code
                            params["EXIF:XPComment"] = caption

                        if params:
                            et.set_tags([path], tags=params)
                            success_count += 1
                        else:
                            success_count += 1  # Nothing to write
                    except Exception as e:
                        logger.error(f"Failed to write metadata to {path}: {e}")
                        error_count += 1
        except Exception as e:
            logger.error(f"ExifTool writer error: {e}", exc_info=True)
            return False

        print(f"Finished writing metadata. Success: {success_count}, Errors: {error_count}")
        return error_count == 0

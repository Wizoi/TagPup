# writer.py
import os
import json
import logging
from typing import Optional
from exiftool_session import ExifToolSession

import _root  # noqa: F401
from tagpup.core import suggesting, vocabulary
from tagpup.files.keywords import caption_fields
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.writer")

#: A caption made from a photo's tags (tagpup.core.suggesting.caption_from_tags).
derive_caption_from_tags = suggesting.caption_from_tags


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
        from tagpup.files.keywords import tags_in_file, write_keywords
        from tagpup.store.embeddings import stamp_of
        from tagpup.store.photos import record_file_stat, record_tags

        # Who a bare name means, read once for the run, not once per photo.
        people = store_taxonomy.people_paths(db_path)
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
                            flat, hierarchical = write_keywords(
                                et, path, vocabulary.resolve_people(merged, people))
                            if db_path:
                                record_tags(db_path, path, flat, flat, hierarchical, before=before)
                        elif caption and db_path:
                            record_file_stat(db_path, path, before=before)
                        success_count += 1
                    except Exception as e:
                        logger.error(f"Failed to write metadata to {path}: {e}")
                        error_count += 1
        except Exception as e:
            logger.error(f"ExifTool writer error: {e}", exc_info=True)
            return False

        print(f"Finished writing metadata. Success: {success_count}, Errors: {error_count}")
        return error_count == 0

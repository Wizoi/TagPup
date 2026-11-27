"""The record the index keeps of a photo file's read (tagpup.files.field_values.Fields.whole reads the file and builds
it here, as tests/photo_rows.py builds a test's), and the name a photo takes from its caption (the caller renames it).
Nothing here starts ExifTool or reads a field: that is tagpup.files.field_values', the one face.

What the fields mean -- which hold tags, people, captions -- is tagpup.core.vocabulary's.
What the library's tag tree says about people, and the rename format its settings name,
are the caller's to pass in: this layer reads photo files and nothing else.
"""
import logging
import os
from typing import Any

from tagpup.core import fields, renaming, vocabulary
from tagpup.core.renaming import sanitize_filename  # noqa: F401  (imported from here by older code)
from tagpup.files import images


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


def structured(meta):
    """One ExifTool record as a photo's row keeps it, its raw_metadata: each value cleaned
    (clean_metadata_value), under the name ExifTool gives it ("XMP:Subject") and again
    under its bare name ("Subject"). The one shape: the indexer's read and the single
    save's read back both record it, and differed while each made its own
    (docs/findings.md, #251); a row without the bare names reads as never read
    (tagpup.services.refresh_rows)."""
    cleaned = {}
    for k, v in meta.items():
        # ExifTool returns keys like 'SourceFile', 'XMP:Subject', etc.
        val_cleaned = clean_metadata_value(v)
        cleaned[k] = val_cleaned
        if ":" in k:
            cleaned[k.split(":")[-1]] = val_cleaned
    return cleaned


#: Why a record is left unread (its `unread`): what the indexer says of the photos it did not record.
NOT_ANSWERED = "ExifTool did not answer for them (a share gone away?)"
NOT_STARTED = "ExifTool could not be started"
NO_STAT = "their file could not be looked at (its modified time and size could not be read)"
HELD = "another program held their file open (an editor?)"

#: Where ExifTool puts an error about a file in what it answers.
READ_ERROR_KEYS = ("ExifTool:Error", "Error")

def stamp_before_read(path):
    """(mtime, size) of the file, for change detection: taken BEFORE ExifTool reads it, so that a change another program
    makes during the read leaves the row older than its file, which the next scan reads again. Taken after, it stamped
    what was read before the change as the newer file. (None, None), unread, when the stat fails."""
    try:
        stat = os.stat(path)
        return stat.st_mtime, stat.st_size
    except OSError:
        return None, None


def structure(path, meta, people, stamp):
    """One ExifTool record of the file at `path` in the shape the rest of the pipeline expects; `stamp`, the file's
    (mtime, size) from before the read (stamp_before_read); `people`, what the library's tag tree says about people."""
    cleaned = structured(meta)

    tags = vocabulary.extract_tags(cleaned)
    mtime, size = stamp

    record = {
        "path": path,
        "kind": images.kind_of(path),
        "mtime": mtime,
        "size": size,
        "tags": tags,
        "people": vocabulary.extract_people(tags, people),
        "captions": fields.extract_captions(cleaned),
        "raw_metadata": cleaned,
        "document_id": fields.read_document_id(cleaned),
    }
    error = next((meta[key] for key in READ_ERROR_KEYS if meta.get(key)), None)
    if error:
        record["read_error"] = str(error)
    return record


def empty(path, error, stamp):
    """The record of a file ExifTool could not read: nothing read, and `read_error`, why -- so nothing is written into it
    (tagpup.files.field_values.IdentityWriter). `stamp` is the file's from before the read, or (None, None) for one left
    unread."""
    mtime, size = stamp
    return {
        "path": path,
        "kind": images.kind_of(path),
        "mtime": mtime,
        "size": size,
        "tags": [],
        "people": [],
        "captions": [],
        "raw_metadata": {},
        "read_error": str(error) or type(error).__name__,
    }


def title_filename(photo_path: str, new_title: str, rename_format: str, preserved: str) -> str:
    """Where a Smart-Renamed photo goes when its caption becomes `new_title`: the path whose name
    `rename_format` (the library's renaming.format setting, tagpup.services.settings) gives it, a free
    name where that one is taken; or `photo_path` when nothing is to move. A photo is Smart-Renamed
    when it holds the name it had before in `preserved` (XMP-xmpMM:PreservedFileName, "" for none). Its grouping and
    number are read by renaming.parts_of, Smart Rename's own reader: a split at " - " took the second part of a grouping
    made before one could not hold " - " ("2019-06 - Summer Camp - 03 - Start") as the number, and dropped the number.
    A name the format did not make is left as it is. Nothing is renamed here: the caller renames, through the file
    journal (docs/findings.md, #298)."""
    if not preserved or not os.path.exists(photo_path):
        return photo_path
    try:
        base_name, ext = os.path.splitext(os.path.basename(photo_path))
        parts = renaming.parts_of(rename_format, base_name)
        if parts is None:
            return photo_path
        grouping, index_str = parts
        new_name = renaming.file_base(rename_format, grouping, index_str, new_title) + ext
        folder = os.path.dirname(photo_path)
        new_path = os.path.join(folder, new_name)
        if photo_path == new_path:
            return photo_path
        if os.path.exists(new_path):
            base_part, ext_part = os.path.splitext(new_name)
            counter = 1
            while os.path.exists(os.path.join(folder, f"{base_part}_{counter}{ext_part}")):
                counter += 1
            new_path = os.path.join(folder, f"{base_part}_{counter}{ext_part}")
        return new_path
    except Exception as e:
        logging.getLogger("metadata").error(f"Error working out the name for the title: {e}")
        return photo_path


def raw_metadata(record):
    """A photo's metadata as a save records it in the index, from `record`, an ExifTool record
    of the photo read with more fields than the reader asks (tagpup.services.file_changes'
    whole read back): every field the scan reads (fields.scan_reads), in the shape the indexer
    records it in (structured). It replaces the row's raw_metadata whole."""
    cleaned = structured({key: value for key, value in record.items()
                          if key == "SourceFile" or fields.scan_reads(key)})
    return cleaned

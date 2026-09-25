"""Reading a photo file's metadata with ExifTool, and the two file operations built on
it: a quarter turn, and renaming a photo after its caption.

What the fields mean -- which hold tags, people, captions -- is tagpup.core.vocabulary's.
What the library's tag tree says about people, and the rename format its settings name,
are the caller's to pass in: this layer reads photo files and nothing else.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from tagpup.core import fields, renaming, vocabulary
from tagpup.core.fields import METADATA_FIELDS
from tagpup.core.renaming import sanitize_filename  # noqa: F401  (imported from here by older code)
from tagpup.files.exiftool_session import ExifToolSession
from tagpup.files.identity import ensure_document_id, read_document_id

logger = logging.getLogger("tagpup_cli.metadata")


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


class MetadataExtractor:
    def __init__(self, exiftool_path: Optional[str] = None, mint_identities: bool = False):
        self.exiftool_path = exiftool_path
        #: Write an identity into photos that lack one, as they are read.
        #:
        #: Only the indexer asks for this, because it records what it read -- the
        #: identity, and the file's new mtime and size -- in the index. Without an
        #: identity a renamed photo strands its row, and the row holds the faces
        #: somebody named by hand. It used to be on for every reader, so opening a
        #: folder wrote into its photos and the index rows then looked out of date.
        self.mint_identities = mint_identities

    def batch_read(self, file_paths: List[str],
                   people: Optional[vocabulary.PeopleVocabulary] = None) -> List[Dict[str, Any]]:
        """Read metadata for a batch of files using pyexiftool.

        `people` is what the library's tag tree says about people, read once for the
        batch; without it, only the usual face roots name anybody.
        """
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
                    results.append(self._structure(path, meta, people))

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
            results = self._read_one_by_one(file_paths, executable, people)

        return results

    def _structure(self, path, meta, people):
        """Turn one ExifTool record into the shape the rest of the pipeline expects."""
        cleaned = structured(meta)

        tags = vocabulary.extract_tags(cleaned)

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
            "people": vocabulary.extract_people(cleaned, tags, people),
            "captions": vocabulary.extract_captions(cleaned),
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

    def _read_one_by_one(self, file_paths, executable, people):
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
                    results.append(self._structure(path, meta[0], people))
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


#: The EXIF Orientation a photo has after a quarter turn, by the one it had before.
#: Worked out against ImageOps.exif_transpose -- what is shown for the new value is
#: what was shown for the old one, turned -- and checked the same way in
#: tests/test_rotate_keeps_the_photo.py. The mirrored values (2, 4, 5, 7) stay mirrored.
ROTATED_ORIENTATION = {
    "left": {1: 8, 2: 5, 3: 6, 4: 7, 5: 4, 6: 1, 7: 2, 8: 3},
    "right": {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1},
}


def rotate_image_file(photo_path: str, direction: str, exiftool_path: Optional[str] = None) -> int:
    """Turn a photo a quarter left or right. Returns the Orientation it now has.

    Only the EXIF Orientation tag changes; the pixels are not touched. This used to
    decode the photo with Pillow, turn it, and save it again passing only the EXIF
    block, which dropped every XMP and IPTC field -- keywords, people, caption,
    DocumentID -- and re-encoded the JPEG at quality 95 on every click. Browsers, and
    the server's previews (ImageOps.exif_transpose), show a photo by its orientation,
    so changing that turns what everybody sees and leaves everything else alone.

    The new value is composed with the one already there, so turning a phone photo
    stored sideways (Orientation 6) left makes it 1, not 8.

    Raises if ExifTool is unavailable or the file does not end up with the value
    written: a rotation that silently did nothing would be reported as done.
    """
    turns = ROTATED_ORIENTATION.get(direction)
    if turns is None:
        raise ValueError("Direction must be 'left' or 'right', not %r" % (direction,))
    if not exiftool_path:
        raise ValueError("Rotating a photo needs ExifTool, and no ExifTool path was given")

    def orientation_of(et):
        found = et.get_tags([photo_path], tags=["EXIF:Orientation"])
        value = (found[0] if found else {}).get("EXIF:Orientation")
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 8 else None

    with ExifToolSession(executable=exiftool_path) as et:
        new = turns[orientation_of(et) or 1]
        # Written without a group, so an XMP tiff:Orientation already in the file is
        # updated to match instead of being left to contradict the EXIF one.
        et.set_tags([photo_path], tags={"Orientation": new}, params=["-overwrite_original"])
        written = orientation_of(et)
    if written != new:
        raise RuntimeError("Rotated %s to orientation %s, but the file now says %s"
                           % (os.path.basename(photo_path), new, written))
    return new


def sync_title_to_filename(photo_path: str, new_title: str, exiftool_path: str,
                           rename_format: str, preserved: Optional[str] = None) -> str:
    """If the photo has an XMP-xmpMM:PreservedFileName tag set, automatically syncs
    any changes to the title back into the filename structure, in `rename_format`
    (the library's renaming.format setting, tagpup.services.settings).
    Returns the new path if renamed, or the original path if not renamed. `preserved`
    is the photo's PreservedFileName when the caller has read it ("" for none): then
    ExifTool is not started to read it again."""
    if not os.path.exists(photo_path):
        return photo_path

    try:
        if preserved is None:
            with ExifToolSession(executable=exiftool_path) as et:
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

            new_name = renaming.file_base(rename_format, grouping, index_str, new_title) + ext

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
        logging.getLogger("metadata").error(f"Error syncing title to filename: {e}")

    return photo_path


def raw_metadata(et, photo_path, record=None):
    """A photo's metadata as a save records it in the index: every field the reader
    reads, in the shape the indexer records it in (structured). Read in the session
    given -- or taken from `record`, an ExifTool record of the photo read with more
    fields than the reader asks (tagpup.services.file_changes' read back), of which
    only those the scan reads (fields.scan_reads) are kept."""
    if record is None:
        found = et.get_tags([photo_path], tags=METADATA_FIELDS)
        return structured(found[0] if found else {})
    return structured({key: value for key, value in record.items()
                       if key == "SourceFile" or fields.scan_reads(key)})

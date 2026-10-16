"""Reading a photo file's metadata with ExifTool, and the two file operations built on
it: a quarter turn, and the name a photo takes from its caption (the caller renames it).

What the fields mean -- which hold tags, people, captions -- is tagpup.core.vocabulary's.
What the library's tag tree says about people, and the rename format its settings name,
are the caller's to pass in: this layer reads photo files and nothing else.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from tagpup.core import fields, paths, renaming, vocabulary
from tagpup.core.fields import METADATA_FIELDS
from tagpup.core.renaming import sanitize_filename  # noqa: F401  (imported from here by older code)
from tagpup.files import field_values
from tagpup.files.exiftool_session import ExifToolSession
from tagpup.files.identity import ensure_document_id

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


#: Where ExifTool puts an error about a file in what it answers.
READ_ERROR_KEYS = ("ExifTool:Error", "Error")


class MetadataExtractor:
    """Reads photos' metadata; never writes into a photo. Giving a photo an identity is
    IdentityWriter's, after its picture has decoded."""

    def __init__(self, exiftool_path: Optional[str] = None):
        self.exiftool_path = exiftool_path

    def batch_read(self, file_paths: List[str],
                   people: Optional[vocabulary.PeopleVocabulary] = None) -> List[Dict[str, Any]]:
        """A record of each of `file_paths`, in order, read in one ExifTool session through the one batch reader
        (tagpup.files.field_values.read: a batch ExifTool refuses is read again a photo at a time, one it does not
        answer is not retried).

        A photo ExifTool refuses (or that is not a photo) is recorded holding nothing, with `read_error`, stamped: a
        file that cannot be read is not read again every run. A photo ExifTool did not answer for (a share gone away),
        or whose stat failed, is left UNREAD: its record has no stamp (mtime and size None), so nothing records it as
        read. `people` is what the library's tag tree says about people, read once for the batch; without it, only the
        usual face roots name anybody.
        """
        if not file_paths:
            return []

        executable = self.exiftool_path
        if executable and not os.path.isabs(executable):
            executable = os.path.abspath(executable)

        # Each file's stamp before the read (_stamp).
        stamps = [self._stamp(path) for path in file_paths]
        records = {}
        try:
            with ExifToolSession(executable=executable) as et:
                found = field_values.read(et, file_paths, [], METADATA_FIELDS, records)
        except Exception as e:
            logger.error("Could not start ExifTool to read %d file(s): %s", len(file_paths), e)
            found = {paths.key(path): field_values.NotAnswered(str(e) or type(e).__name__) for path in file_paths}
        results = []
        for path, stamp in zip(file_paths, stamps):
            held, record = found.get(paths.key(path)), records.get(paths.key(path))
            if isinstance(held, field_values.NotAnswered):
                results.append(self._empty(path, held, (None, None)))
            elif isinstance(held, Exception) or not record:
                logger.error("Unreadable metadata, indexing without it: %s (%s)", path, held or "no answer")
                results.append(self._empty(path, held or "ExifTool answered nothing for it", stamp))
            else:
                # What the reader asked to tell a photo from another kind of file is not the photo's metadata.
                record = {key: value for key, value in record.items() if key != field_values.MIME_TYPE}
                results.append(self._structure(path, record, people, stamp))
        return results

    @staticmethod
    def _stamp(path):
        """(mtime, size) of the file, for change detection: taken BEFORE ExifTool reads it, so that a change another
        program makes during the read leaves the row older than its file, which the next scan reads again. Taken after,
        it stamped what was read before the change as the newer file. (None, None), unread, when the stat fails."""
        try:
            stat = os.stat(path)
            return stat.st_mtime, stat.st_size
        except OSError:
            return None, None

    def _structure(self, path, meta, people, stamp):
        """Turn one ExifTool record into the shape the rest of the pipeline expects; `stamp`, the file's (mtime, size)
        from before the read (_stamp)."""
        cleaned = structured(meta)

        tags = vocabulary.extract_tags(cleaned)
        mtime, size = stamp

        record = {
            "path": path,
            "mtime": mtime,
            "size": size,
            "tags": tags,
            "people": vocabulary.extract_people(tags, people),
            "captions": vocabulary.extract_captions(cleaned),
            "raw_metadata": cleaned,
            "document_id": fields.read_document_id(cleaned),
        }
        error = next((meta[key] for key in READ_ERROR_KEYS if meta.get(key)), None)
        if error:
            record["read_error"] = str(error)
        return record

    @staticmethod
    def _empty(path, error, stamp):
        """The record of a file ExifTool could not read: nothing read, and `read_error`, why -- so nothing is written
        into it (IdentityWriter). `stamp` is the file's from before the read, or (None, None) for one left unread."""
        mtime, size = stamp
        return {
            "path": path,
            "mtime": mtime,
            "size": size,
            "tags": [],
            "people": [],
            "captions": [],
            "raw_metadata": {},
            "read_error": str(error) or type(error).__name__,
        }


class IdentityWriter:
    """Gives photos that have none an identity (tagpup.files.identity), one at a time, as
    the indexer reads them -- the one writer of a minted identity at index time.

    A path is a bad name for a photo: rename it and the index describes something that
    no longer exists, while the photo looks unindexed. DocumentID is the XMP standard's
    per-document identifier and most photos already carry one, so this writes to few
    files. Only the indexer mints, because it records what it read -- the identity, and
    the file's new mtime and size -- in the index; opening a folder once wrote into its
    photos and the rows then looked out of date.

    Never into a photo whose picture did not decode in full (`decoded`: the caller's to
    say, and false for a file that may be an incomplete copy, tagpup.files.images.
    ZERO_TAIL), nor one ExifTool reported an error reading (`read_error`): a truncated file
    took an identity, which changed its modified time, which the folder watcher saw,
    which queued the indexer for it again (docs/findings.md, #407). Minting used to
    happen as the batch was read, before any picture was decoded.

    Failures are per-file and logged, never raised: an identity is an improvement on
    knowing only the path, and a photo that cannot take one indexes without it. The
    ExifTool session is started for the first photo that needs one; close() ends it.
    """

    def __init__(self, exiftool_path: Optional[str] = None):
        executable = exiftool_path
        if executable and not os.path.isabs(executable):
            executable = os.path.abspath(executable)
        self.executable = executable
        self._et = None
        self._failed = False
        #: Photos given an identity.
        self.minted = 0

    def give(self, record, decoded):
        """Write an identity into the photo of `record` (MetadataExtractor's) if it has
        none, it `decoded`, and ExifTool read it without an error; update the record --
        its identity, and the file's mtime and size after the write, or the next pass
        would see a modified file and index it again for a write this pass made. The
        identity minted, or None."""
        if record.get("document_id") or not decoded or record.get("read_error"):
            return None
        et = self._session()
        if et is None:
            return None
        minted = ensure_document_id(et, record["path"], record.get("raw_metadata"))
        if not minted:
            return None
        self.minted += 1
        record["document_id"] = minted
        record.setdefault("raw_metadata", {})["XMP:DocumentID"] = minted
        try:
            stat = os.stat(record["path"])
            record["mtime"], record["size"] = stat.st_mtime, stat.st_size
        except OSError:
            pass
        return minted

    def _session(self):
        if self._et is None and not self._failed:
            try:
                et = ExifToolSession(executable=self.executable)
                et.run()
                self._et = et
            except Exception as e:
                self._failed = True
                logger.error(f"Could not start ExifTool to give photos an identity: {e}")
        return self._et

    def close(self):
        et, self._et = self._et, None
        if et is not None:
            try:
                et.terminate()
            except Exception:
                pass


def title_filename(photo_path: str, new_title: str, rename_format: str, preserved: str) -> str:
    """Where a Smart-Renamed photo goes when its caption becomes `new_title`: the path whose name
    `rename_format` (the library's renaming.format setting, tagpup.services.settings) gives it, a free
    name where that one is taken; or `photo_path` when nothing is to move. A photo is Smart-Renamed
    when it holds the name it had before in `preserved` (XMP-xmpMM:PreservedFileName, "" for none).
    Nothing is renamed here: the caller renames, through the file journal (docs/findings.md, #298)."""
    if not preserved or not os.path.exists(photo_path):
        return photo_path
    try:
        base_name, ext = os.path.splitext(os.path.basename(photo_path))
        parts = [p.strip() for p in base_name.split(" - ")]
        if len(parts) < 2:
            return photo_path
        grouping, index_str = parts[0], parts[1]
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

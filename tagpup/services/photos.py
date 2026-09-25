"""Actions on photo files, and reading a folder of them for the page."""
import json
import logging
import os

from tagpup.core import dates, fields, paths, renaming, validation, vocabulary
from tagpup.core.result import NotFound, Refused, Result
from tagpup.files import images, metadata, names, recycle_bin, times
from tagpup.store import db, embeddings, faces, photos, taxonomy

logger = logging.getLogger(__name__)

#: Photos read from disk in one ExifTool call. A folder of thousands is read in these.
READ_BATCH = 500


# ---- A folder, as the page sees it --------------------------------------------------------

def page_record(path, meta, mtime=0.0, size=0):
    """One photo as the TagPup page reads it, from what was read of it: `meta` is a
    record from the file reader or an index row (tags, people, captions, raw_metadata).

    "path" is the stored spelling whatever the caller had, so the browser only ever
    sees one spelling of a photo and hands back the one the index uses. `taken` is when
    it was taken as the library records it, which the page reads rather than fields of
    its own (docs/findings.md, #67). This was scripts/metadata.build_photo_ui_record.
    """
    path = paths.stored(path)
    raw_meta = meta.get("raw_metadata", {})
    captions = meta.get("captions", [])
    year = meta["year"] if "year" in meta else dates.photo_year(raw_meta, path)
    return {
        "path": path,
        "filename": os.path.basename(path),
        "tags": meta.get("tags", []),
        "people": meta.get("people", []),
        "title": captions[0] if captions else "",
        "mtime": mtime,
        "size": size,
        "year": dates.shown_year(None if year is None else str(year)),
        "taken": dates.date_taken(raw_meta),
        "raw_metadata": raw_meta,
    }


def taken_order(record):
    """The folder view's order for a page record: when it was taken, then file time."""
    return dates.date_taken_sort_key(record.get("raw_metadata", {}), record.get("mtime", 0.0))


def scan_folder(library, folder, exiftool_path):
    """The photos under `folder`, at any depth, as page records keyed by paths.key:
    opening a folder in TagPup.

    A photo whose index row matches the file's mtime and size is taken from the row;
    the rest are read with ExifTool, in batches. A row with no stamp -- made for a
    photo Suggest saw but the index never read -- is read now (docs/findings.md, #94).
    The reader is told what the library says about people, as the indexer is; the
    server's scan used the default face roots alone.
    """
    folder = paths.stored(folder)
    image_files = images.photos_under(folder)
    if not image_files:
        return {}

    known = {}
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            for path, mtime, size, tags, people, captions, raw_meta in photos.rows_under(conn, folder):
                known[paths.key(path)] = {
                    "path": paths.stored(path), "mtime": mtime, "size": size,
                    "tags": json.loads(tags) if tags else [],
                    "people": json.loads(people) if people else [],
                    "captions": json.loads(captions) if captions else [],
                    "raw_metadata": json.loads(raw_meta) if raw_meta else {},
                }
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to query index DB for folder scan cache: %s", e)

    found, to_read = {}, []
    for file in image_files:
        try:
            stat = os.stat(file)
        except OSError:
            continue
        row = known.get(paths.key(file))
        if (row and row["mtime"] is not None and row["size"] is not None
                and abs(row["mtime"] - stat.st_mtime) < 0.1 and row["size"] == stat.st_size):
            found[paths.key(file)] = page_record(row["path"], row, stat.st_mtime, stat.st_size)
        else:
            to_read.append((file, stat.st_mtime, stat.st_size))

    if to_read:
        logger.info("Scan found %d new/modified files in %s. Running ExifTool...", len(to_read), folder)
        try:
            found.update(_read(library, [(f, mt, sz) for f, mt, sz in to_read], exiftool_path))
        except Exception as e:
            logger.error("Error running ExifTool during scan: %s", e)
    return found


def read_folder(library, folder, exiftool_path):
    """Every photo under `folder` read from its file, whatever the index holds, as page
    records keyed by paths.key: the folder after Smart Rename, and a time shift on a
    folder never opened. `mtime` and `size` come from the reader."""
    folder = paths.stored(folder)
    image_files = images.photos_under(folder)
    if not image_files:
        return {}
    return _read(library, [(f, None, None) for f in image_files], exiftool_path)


def _read(library, files, exiftool_path):
    """{paths.key: page record} of `files`, each (path, mtime, size), mtime and size the
    reader's when None, read with ExifTool in batches of READ_BATCH."""
    people = taxonomy.people_vocabulary(library.path)
    reader = metadata.MetadataExtractor(exiftool_path=exiftool_path)
    found = {}
    for start in range(0, len(files), READ_BATCH):
        batch = files[start:start + READ_BATCH]
        for (file, mtime, size), meta in zip(batch, reader.batch_read([f for f, _, _ in batch], people=people)):
            found[paths.key(file)] = page_record(
                file, meta, meta.get("mtime", 0.0) if mtime is None else mtime,
                meta.get("size", 0) if size is None else size)
    return found


def people_of(library, raw_meta, tags, photo_path):
    """Everyone in a photo, by its metadata and its named faces
    (tagpup.core.vocabulary.people_in_photo), for a page record just written."""
    return vocabulary.people_in_photo(raw_meta, tags, faces.face_names(photo_path, db_path=library.path),
                                      taxonomy.people_vocabulary(library.path))


def record_written(library, record, path, tags, flat, hierarchical):
    """Bring a page record up to date with the keywords just written to its photo: its
    tags, every keyword field of its raw metadata, and its people.

    Every keyword field, not just the XMP pair: the page re-derives tags from the raw
    metadata, and a stale IPTC:Keywords brought a removed tag straight back.
    """
    raw_meta = fields.record_keyword_fields(record.setdefault("raw_metadata", {}), flat, hierarchical)
    record["tags"] = list(tags)
    record["people"] = people_of(library, raw_meta, tags, path)
    return raw_meta


def page_copy(photo_path, max_size=None, upright=True):
    """(bytes, content type) of a photo for a page: the file itself, or with `max_size`
    a JPEG no larger than that on a side -- turned upright by its Orientation when
    `upright`. A copy that cannot be made falls back to the file.

    Refused for a file the servers do not send: the path is the page's to name.
    """
    if not images.is_servable(photo_path):
        raise Refused("Forbidden: Invalid file type requested")
    if not os.path.exists(photo_path):
        raise NotFound("Photo file not found: %s" % photo_path)
    if max_size:
        try:
            return images.smaller_copy(photo_path, max_size, upright), "image/jpeg"
        except Exception as e:
            logger.warning("Could not make a smaller copy of %s: %s", photo_path, e)
    with open(photo_path, "rb") as f:
        return f.read(), images.content_type(photo_path)


def face_crop(library, face_id):
    """A face's crop, as JPEG bytes: kept in its row, or cut from its photo and kept
    there the first time it is asked for. Both servers had a copy of this, and each
    wrote the crop back on a connection of its own, without the write lock.

    Raises ValueError for a box that cannot be read.
    """
    found = faces.crop_of(library.path, face_id)
    if not found:
        raise NotFound("Face not found")
    photo_path, box, crop = found
    if crop:
        return crop
    if not photo_path or not os.path.exists(photo_path):
        raise NotFound("Original photo not found")
    box = images.parse_box(box)
    if len(box) < 4:
        raise ValueError("Invalid bounding box")
    crop = images.face_crop(photo_path, box)
    try:
        faces.cache_crop(library.path, face_id, crop)
    except Exception as e:
        logger.warning("Could not cache face crop %s: %s", face_id, e)
    return crop


def smart_rename(library, photo_paths, grouping, rename_format, exiftool_path):
    """Number photos in the order given and name each for it: "<grouping> - <index> -
    <caption>" in `rename_format`, the caption being the one on the photo. Smart Rename.

    Each photo stays in its own folder. A file already holding one of the new names is
    moved aside to "<name>_conflict_<n>". The files are renamed all together or not at
    all (tagpup.files.names.rename_all), and then the index is told where they went --
    their rows carry their embeddings and faces, names included, and one rename that
    did not tell it stranded 78 rows holding 234 faces. A photo no longer on disk keeps
    its number, unused.

    details: `updated_paths`, old -> new for every photo, those already so named
    included; `renamed`, those whose name changed; `moved_aside`, the files moved out
    of the way; `index_rows_moved`; `index_skipped`, the (old, new) pairs whose new name
    already had rows in the index, left as they were.

    Refused, and nothing renamed, for a grouping that may not be used
    (tagpup.core.validation).
    """
    result = Result(attempted=len(photo_paths))
    problem = validation.problem("grouping", grouping)
    if problem:
        result.refuse(problem)
        return result
    width = len(str(len(photo_paths)))
    captions = names.read_for_renaming(exiftool_path, [p for p in photo_paths if os.path.exists(p)])
    renames = {}
    for index, old_path in enumerate(photo_paths, start=1):
        if old_path not in captions:
            continue
        base = renaming.file_base(rename_format, grouping, str(index).zfill(width), captions[old_path])
        renames[old_path] = os.path.join(os.path.dirname(old_path), base + os.path.splitext(old_path)[1])

    try:
        done, moved_aside = names.rename_all(renames)
    except names.RenameFailed as failure:
        result.fail("smart rename", failure.message())
        return result

    renamed = {old: new for old, new in done.items() if old != new}
    result.changed = len(renamed)
    result.details.update(updated_paths=done, renamed=renamed, moved_aside=moved_aside,
                          index_rows_moved=0, index_skipped=[])
    if renamed or moved_aside:
        try:
            # One call, so the files moved aside free their names for the photos
            # renamed into them within the same transaction.
            moved, skipped = photos.move_rows(library.path, {**moved_aside, **renamed})
            result.details.update(index_rows_moved=moved, index_skipped=skipped)
            logger.info("Renamed %d photo(s); moved %d index row(s).", len(renamed), moved)
        except Exception as e:
            # The files are renamed either way; a stranded row is recoverable with
            # scripts/relink_renamed_photos.py.
            logger.error("Renamed %d photo(s) but could not move their index rows: %s",
                         len(renamed), e)
    return result


def shift_date_taken(library, photo_paths, minutes, exiftool_path):
    """Move Date Taken in each photo by `minutes`, and tell the index. Time Shift.

    `changed` is ExifTool's own count of the files it wrote: a photo it could not
    write is not counted, where this used to answer with the number it had tried. The
    index rows get the new Date Taken, which orders photos and picks the era a face is
    compared against, and each file's new mtime and size, without which the next scan
    distrusts them.

    details: `records`, each photo as read back afterwards. Refused, and nothing
    written, for a shift that is not a whole number of minutes (tagpup.core.validation).
    """
    result = Result(attempted=len(photo_paths))
    problem = validation.problem("time shift", minutes)
    if problem:
        result.refuse(problem)
        return result
    before = {photo_path: embeddings.stamp_of(photo_path) for photo_path in photo_paths}
    try:
        result.changed = times.shift_date_taken(exiftool_path, photo_paths, minutes)
    except Exception as e:
        result.fail("time shift", e)
        return result
    # Only reading: minting a DocumentID here would write the files a second time.
    records = metadata.MetadataExtractor(exiftool_path=exiftool_path, mint_identities=False).batch_read(
        photo_paths, people=taxonomy.people_vocabulary(library.path))
    photos.record_reads(library.path, records, label="time shift", before=before)
    result.details["records"] = records
    return result


def delete(library, photo_path):
    """Send a photo to the Recycle Bin, and forget it: its row, faces and cached
    embedding. Clicking Delete.

    The file goes first. A photo that could not be moved stays in the library, and its
    rows with it.

    details: `removed`, the rows removed from each table.
    """
    result = Result(attempted=1)
    try:
        moved = recycle_bin.send_to_recycle_bin(photo_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    if not moved:
        result.fail(photo_path, "Failed to move file to Recycle Bin")
        return result
    result.changed = 1
    result.details["removed"] = photos.forget_photo(library.path, photo_path)
    return result


def rotate(library, photo_path, direction, exiftool_path):
    """Turn a photo a quarter left or right. Clicking Rotate Left or Rotate Right.

    Only the file's Orientation changes; its pixels do not (tagpup.files.metadata.
    rotate_image_file). Face boxes are in the coordinates Pillow shows the photo in:
    for most formats the stored pixels, which do not move, so the boxes stay where they
    are. Pillow applies a TIFF's Orientation as it loads it, so a TIFF's boxes turn with
    the photo. The index row gets the file's new mtime and size, or the folder scan
    would distrust it and read the photo again.

    details: `mtime` and `size` of the file now (the pages version image URLs by mtime,
    because thumbnails are cached for a day), its `orientation`, and `faces_turned`.
    """
    result = Result(attempted=1)
    try:
        width, height, oriented = images.shown_size(photo_path)
        orientation = metadata.rotate_image_file(photo_path, direction, exiftool_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    result.changed = 1
    result.details["orientation"] = orientation
    result.details["faces_turned"] = (
        faces.turn_boxes(library.path, photo_path, direction, width, height) if oriented else 0)
    # The embedder applies the Orientation, so the photo's vectors describe the turn
    # before; they go, and are computed again.
    photos.record_file_stat(library.path, photo_path, looks_different=True)
    stat = os.stat(photo_path)
    result.details.update(mtime=stat.st_mtime, size=stat.st_size)
    return result


def count_photos(folder, recursive=True):
    """(how many photos are under `folder`, has it subfolders): what the folder picker
    shows beside each folder. `recursive` counts every depth; else the folder's own
    files only. What counts as a photo is tagpup.files.images' (docs/findings.md, #72)."""
    count, has_subdirs = 0, False
    try:
        if recursive:
            for root, dirs, files in os.walk(folder):
                if root == folder and dirs:
                    has_subdirs = True
                count += sum(1 for f in files if images.is_photo(f))
        else:
            for entry in os.listdir(folder):
                if os.path.isdir(os.path.join(folder, entry)):
                    has_subdirs = True
                elif images.is_photo(entry):
                    count += 1
    except OSError:
        pass
    return count, has_subdirs


def indexed_by_folder(library):
    """{paths.key of a folder: photos the library holds directly in it}. Lets the folder
    picker show what is already in rather than offering it as if new; a library that
    cannot be read just now counts as holding nothing."""
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
    except Exception:
        return {}
    try:
        return photos.folder_counts(conn)
    except Exception:
        return {}
    finally:
        conn.close()


def is_photo(path):
    """Does `path` name a photo, by its extension (tagpup.files.images)? The web layer
    asks before opening one on the desktop."""
    return images.is_photo(path)

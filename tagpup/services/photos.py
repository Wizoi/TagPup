"""Actions on photo files."""
import logging
import os

from tagpup.core import renaming
from tagpup.core.result import NotFound, Refused, Result
from tagpup.files import images, metadata, names, recycle_bin, times
from tagpup.store import faces, photos, taxonomy

logger = logging.getLogger(__name__)


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
    """
    result = Result(attempted=len(photo_paths))
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

    details: `records`, each photo as read back afterwards.
    """
    result = Result(attempted=len(photo_paths))
    try:
        result.changed = times.shift_date_taken(exiftool_path, photo_paths, minutes)
    except Exception as e:
        result.fail("time shift", e)
        return result
    # Only reading: minting a DocumentID here would write the files a second time.
    records = metadata.MetadataExtractor(exiftool_path=exiftool_path, mint_identities=False).batch_read(
        photo_paths, people=taxonomy.people_vocabulary(library.path))
    photos.record_reads(library.path, records, label="time shift")
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
    photos.record_file_stat(library.path, photo_path)
    stat = os.stat(photo_path)
    result.details.update(mtime=stat.st_mtime, size=stat.st_size)
    return result

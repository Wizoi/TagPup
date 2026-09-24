"""Actions on one photo's file."""
import os

from tagpup.core.result import Result
from tagpup.files import images, metadata, recycle_bin
from tagpup.store import faces, photos


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

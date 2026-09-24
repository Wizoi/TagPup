"""Opening a photo's image.

For now, how Pillow shows a photo. Thumbnails and face crops move here with the
services that make them (ARCHITECTURE.md, phase 2).
"""
from PIL import Image


def shown_size(photo_path):
    """(width, height, oriented): the size Pillow shows a photo at, and whether it
    turned the picture by its Orientation as it loaded it.

    Face boxes are in the coordinates Pillow shows a photo in. For most formats that is
    the stored pixels, which an Orientation change leaves where they are; Pillow applies
    a TIFF's Orientation as it loads it, so a TIFF's boxes turn with the photo.
    """
    with Image.open(photo_path) as img:
        oriented = img.format == "TIFF"
        if oriented:
            img.load()
        width, height = img.size
    return width, height, oriented

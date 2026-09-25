"""Opening a photo's image: how Pillow shows it, a smaller copy of it for a page, and a
face cut out of it.

Thumbnails for the folder view move here with the services that make them
(docs/ARCHITECTURE.md, phase 2).
"""
import io
import json
import os

from PIL import Image, ImageOps

from tagpup.core import paths

#: Pillow refuses a picture over about 179 million pixels as a possible decompression
#: bomb; a stitched panorama is larger. The photos are the owner's own. Four modules
#: raised the limit each for itself (docs/findings.md, #74); every photo is opened here.
Image.MAX_IMAGE_PIXELS = 500_000_000

#: What counts as a photo, by its extension, and the Content-Type its own bytes go out
#: under: what the indexer scans, the folder views list, relink points a row at, and the
#: servers send. It was written eight times, with members of its own in two: relink
#: took .heic, which nothing scans, and the image server sent bmp, gif, heic and heif --
#: and all but png and webp as image/jpeg (docs/findings.md, #72).
PHOTO_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".webp": "image/webp",
}

#: The extensions of PHOTO_TYPES, lower case with the dot.
PHOTO_EXTENSIONS = frozenset(PHOTO_TYPES)

#: The largest side of a face crop, and the JPEG quality it is kept at. Face detection
#: cut and encoded its own crops beside face_crop's (docs/findings.md, #74).
CROP_SIZE = 256
CROP_QUALITY = 90


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


def is_photo(path):
    """Does `path` name a photo, by its extension (PHOTO_EXTENSIONS)?"""
    return os.path.splitext(str(path).lower())[1] in PHOTO_EXTENSIONS


#: The servers send a photo's bytes only for a photo: the path is the caller's.
is_servable = is_photo


def photos_under(folder):
    """The photos under `folder`, at any depth, in the form the index stores: walked from
    paths.stored(folder), as os.walk joins onto whatever it is given, and a folder typed
    D:/Photos gave D:/Photos\\a.jpg. Five walks each had a copy of this, two walking the
    folder as typed."""
    found = []
    for root, _dirs, files in os.walk(paths.stored(folder)):
        found += [os.path.join(root, name) for name in files if is_photo(name)]
    return found


def content_type(photo_path):
    """The Content-Type a photo's own bytes go out under."""
    return PHOTO_TYPES.get(os.path.splitext(photo_path)[1].lower(), "application/octet-stream")


def smaller_copy(photo_path, max_size, upright):
    """A JPEG of the photo no larger than `max_size` on a side.

    `upright` turns it by its Orientation first, as a person sees it. TagTuner draws
    face boxes over its photos, in the stored pixels' coordinates, and asks for them as
    stored (docs/findings.md, #1).
    """
    with Image.open(photo_path) as img:
        if upright:
            img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()


def opened(photo_path, upright):
    """The photo's picture as an RGB Pillow image, read in full and the file closed.

    `upright` turns it by its Orientation, as a person sees it -- what CLIP is shown.
    Without, it is the pixels as Pillow shows them, the coordinates face boxes are in
    (shown_size).
    """
    with Image.open(photo_path) as img:
        if upright:
            img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.load()
        return img


def parse_box(box):
    """A face box as stored -- JSON, or the bare "[x1, y1, x2, y2]" of older rows -- as a
    list of numbers, or [] if it cannot be read."""
    try:
        parsed = json.loads(box) if box else []
    except Exception:
        try:
            parsed = [int(x) for x in str(box).replace("[", "").replace("]", "").split(",")]
        except Exception:
            parsed = []
    return parsed if isinstance(parsed, list) else []


def face_crop(photo_path, box):
    """The face in `box` cut from its photo, as a JPEG no larger than CROP_SIZE on a
    side. A box that falls outside the picture gives a plain grey square."""
    with Image.open(photo_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        width, height = img.size
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(width, int(box[2])), min(height, int(box[3]))
        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            crop = Image.new("RGB", (100, 100), color=(50, 50, 50))
        else:
            crop = img.crop((x1, y1, x2, y2))
        return crop_jpeg(crop)


def crop_jpeg(crop):
    """A face already cut from its photo (a Pillow image), as the JPEG a face's crop is
    kept as: no larger than CROP_SIZE on a side, at CROP_QUALITY."""
    if max(crop.size) > CROP_SIZE:
        crop = crop.copy()
        crop.thumbnail((CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=CROP_QUALITY)
    return out.getvalue()

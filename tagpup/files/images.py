"""Opening a photo's image: how Pillow shows it, a smaller copy of it for a page, and a
face cut out of it.

Thumbnails for the folder view move here with the services that make them
(docs/ARCHITECTURE.md, phase 2).
"""
import io
import json
import os
import stat

from PIL import Image, ImageDraw, ImageOps

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

#: How a picture that does not decode is damaged, as the owner is told it.
DAMAGE = {
    "empty": "the file is empty",
    "all zeros": "the file holds nothing but zero bytes",
    "zero-filled": "the file ends in zero bytes where the picture should go on, as an interrupted copy leaves it",
    "truncated": "the file ends before the picture does",
    "not an image": "the file is not a picture that can be read",
    "damaged": "the picture's data is damaged",
}

#: A file that ends in this many zero bytes or more is possibly an incomplete copy: a copy
#: interrupted after the file was given its size leaves the rest of it zeros, and the
#: decoder may read them as picture data -- the photo loads, grey below a line. Copies
#: move data in blocks of 64 KiB and more (the owner's stopped at a MiB boundary). Of
#: the 68,661 photo files under photo_index's folders (2026-09-28), 18 end in zero bytes,
#: 16 at most -- padding some cameras write after the picture -- and one, damaged, in
#: over 1 MiB of them; nothing in between.
ZERO_TAIL = 64 * 1024

#: What `opened` marks a picture with in its `info`: how many zero bytes its file ends in.
ZERO_TAIL_INFO = "zero_tail"


class Unreadable(OSError):
    """A photo whose picture does not decode: the file is there and was read, and what
    it holds is not a whole picture. `kind` is one of DAMAGE, `detail` what the decoder
    said, without the path; `zero_tail`, the zero bytes the file ends in. Never a
    missing, locked or unreachable file: those raise the OSError the system raised (it
    has an errno; the decoder's own have none), since a network share gone for a moment
    is no reason to call a photo damaged."""

    def __init__(self, kind, detail, zero_tail=0):
        super().__init__("%s (%s)" % (DAMAGE.get(kind, kind), detail))
        self.kind, self.detail, self.zero_tail = kind, detail, zero_tail


def zero_tail(data):
    """How many zero bytes `data`, a file's bytes, ends in. Counted in full only when
    there are ZERO_TAIL of them: short padding is only looked at."""
    if not data or data[-1] != 0:
        return 0
    tail = data[-ZERO_TAIL:]
    run = len(tail) - len(tail.rstrip(bytes(1)))
    if run < len(tail):
        return run
    return len(data) - len(data.rstrip(bytes(1)))


def zero_tail_of(photo_path):
    """How many zero bytes the file at `photo_path` ends in, when ZERO_TAIL or more; else
    0. Reads the file's end only -- for a photo whose picture was not decoded now (its
    vector was kept from the file as it is)."""
    with open(photo_path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size < ZERO_TAIL:
            return 0
        handle.seek(size - ZERO_TAIL)
        if handle.read(ZERO_TAIL).count(0) != ZERO_TAIL:
            return 0
        end = size - ZERO_TAIL
        while end > 0:
            start = max(0, end - (1 << 20))
            handle.seek(start)
            kept = len(handle.read(end - start).rstrip(bytes(1)))
            if kept:
                return size - (start + kept)
            end = start
        return size


def damage(data, error):
    """The Unreadable that Pillow's `error` decoding a file of `data` means: how the file
    is damaged, by its bytes."""
    if isinstance(error, Image.UnidentifiedImageError):
        detail = "cannot identify image file"
    else:
        detail = str(error) or type(error).__name__
    zeros = zero_tail(data)
    if not data:
        return Unreadable("empty", detail)
    if zeros == len(data):
        return Unreadable("all zeros", detail, zeros)
    if zeros >= ZERO_TAIL:
        return Unreadable("zero-filled", detail, zeros)
    if isinstance(error, Image.UnidentifiedImageError):
        return Unreadable("not an image", detail, zeros)
    if "truncated" in detail:
        return Unreadable("truncated", detail, zeros)
    return Unreadable("damaged", detail, zeros)


def _decoder_failed(error):
    """Did decoding fail on what the file holds, rather than on reading it?"""
    if isinstance(error, OSError):
        return error.errno is None and not isinstance(error, Unreadable)
    return isinstance(error, (SyntaxError, ValueError, EOFError))


def is_damage(error):
    """Did decoding fail on what the file holds -- a truncated or not-a-picture file -- rather than on
    reading it (a missing file, a share gone away)? What `opened` raises Unreadable for; for a caller that
    decodes some other way (smaller_copy) and wants the same line drawn."""
    return _decoder_failed(error)


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


def _walk_into(entry):
    """Is a folder's entry a folder to walk into? Not a link to one, and not a junction:
    os.scandir's is_symlink() is False for a junction on Python 3.11, and a junction back
    up the tree was walked round and round, finding the same photos under ever longer
    names. Any reparse point is passed over."""
    try:
        if not entry.is_dir(follow_symlinks=False):
            return False
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


def photo_entries(folder):
    """Each photo under `folder`, at any depth, as its folder's listing entry, in the
    order os.walk gives: a folder's photos, then each subfolder in turn. Walked from
    paths.stored(folder); a folder that cannot be listed is passed over."""
    pending = [paths.stored(folder)]
    while pending:
        try:
            listing = os.scandir(pending.pop())
        except OSError:
            continue
        subfolders = []
        with listing:
            for entry in listing:
                if _walk_into(entry):
                    subfolders.append(entry.path)
                elif is_photo(entry.name):
                    try:
                        if not entry.is_dir():
                            yield entry
                    except OSError:
                        continue
        pending.extend(reversed(subfolders))


def photos_under(folder):
    """The photos under `folder`, at any depth, in the form the index stores: walked from
    paths.stored(folder), as os.walk joins onto whatever it is given, and a folder typed
    D:/Photos gave D:/Photos\\a.jpg. Five walks each had a copy of this, two walking the
    folder as typed. No link or junction to a folder is walked into (_walk_into)."""
    return [entry.path for entry in photo_entries(folder)]


def has_subfolders(folder):
    """Does `folder` hold a folder the walk would go into (_walk_into): not a link or a
    junction? False for one that cannot be listed."""
    try:
        with os.scandir(paths.stored(folder)) as listing:
            return any(_walk_into(entry) for entry in listing)
    except OSError:
        return False


def photos_in(folder):
    """The photos directly in `folder`, not in its subfolders, in the form the index stores:
    what indexing a folder alone reads (sync's new files in a folder the library holds)."""
    folder = paths.stored(folder)
    try:
        with os.scandir(folder) as listing:
            return [entry.path for entry in listing if is_photo(entry.name) and not _walk_into(entry)
                    and not entry.is_dir()]
    except OSError:
        return []


def stamps_under(folder):
    """{paths.key: (path as stored, mtime, size)} of the photos under `folder`, at any
    depth: the photos photos_under finds, each with the stamp its folder's listing gives.
    On Windows the listing carries both, so no file is opened or looked up on its own: a
    sync that finds nothing costs one walk (docs/ARCHITECTURE.md, phase 8)."""
    found = {}
    for entry in photo_entries(folder):
        try:
            stat_of = entry.stat()
        except OSError:
            continue
        found[paths.key(entry.path)] = (entry.path, stat_of.st_mtime, stat_of.st_size)
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


def placeholder_jpeg():
    """A small grey picture with a cross, as the JPEG a photo that cannot be shown is stood in by (the thumbnail
    cache): 200 by 150 pixels, the same bytes every time."""
    picture = Image.new("RGB", (200, 150), (64, 64, 64))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((1, 1, 198, 148), outline=(110, 110, 110))
    draw.line((60, 45, 140, 105), fill=(150, 150, 150), width=3)
    draw.line((60, 105, 140, 45), fill=(150, 150, 150), width=3)
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=70)
    return out.getvalue()


def opened(photo_path, upright):
    """The photo's picture as an RGB Pillow image, read in full and the file closed.

    `upright` turns it by its Orientation, as a person sees it -- what CLIP is shown.
    Without, it is the pixels as Pillow shows them, the coordinates face boxes are in
    (shown_size).

    The file is read once, and decoded from what was read. Its `info[ZERO_TAIL_INFO]`
    is how many zero bytes the file ends in (zero_tail): ZERO_TAIL or more, and the
    picture may be an incomplete copy's, grey below a line. Raises Unreadable when the
    picture does not decode (damage): a truncated file, one of zero bytes, one that is
    no picture. A file that cannot be opened or read at all raises what the system
    raised.
    """
    with open(photo_path, "rb") as handle:
        data = handle.read()
    try:
        with Image.open(io.BytesIO(data)) as img:
            if upright:
                img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.load()
    except Exception as error:
        if not _decoder_failed(error):
            raise
        raise damage(data, error) from error
    img.info[ZERO_TAIL_INFO] = zero_tail(data)
    return img


def pad_to_square(image, background_color=(0, 0, 0)):
    """The picture centred on a square of `background_color` (black), so that a model
    that crops to a square sees the whole frame."""
    width, height = image.size
    if width == height:
        return image
    elif width > height:
        result = Image.new(image.mode, (width, width), background_color)
        result.paste(image, (0, (width - height) // 2))
        return result
    else:
        result = Image.new(image.mode, (height, height), background_color)
        result.paste(image, ((height - width) // 2, 0))
        return result


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

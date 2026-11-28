"""Opening a photo's image: how Pillow shows it, a smaller copy of it for a page, and a
face cut out of it.

Thumbnails for the folder view move here with the services that make them
(docs/ARCHITECTURE.md, phase 2).
"""
import hashlib
import io
import json
import os
import stat

from PIL import Image, ImageDraw, ImageOps

from tagpup.core import orientation, paths
from tagpup.files import shares

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

#: The Content-Type a video's own bytes go out under (/api/video-file), by its extension. Not servable as a photo:
#: /api/photo-file never sends one. What the index keeps as a row of kind 'video' (kind_of): the MOV/MP4/M4V the
#: libraries' folders hold (2,184 files, tagpup.files.containers), and the older formats (.wmv, .mpg, .mpeg, .avi, .mod:
#: 629 files), listed with the play mark so the owner sees them (DECISIONS 2026-11-09). An older format is never written
#: (containers.read finds no MOV/MP4 boxes in it: images.CLIP_NOT_PATCHED) and no browser plays it: what it needs to play
#: is tagpup.files.convertible's to say.
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".avi": "video/x-msvideo",
               ".mod": "video/mpeg"}

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

#: What `opened` marks a picture with in its `info`: the Orientation its face boxes are turned by between the picture as
#: stored and the picture upright (orientation.boxes_turn), read from the file before it was turned.
BOXES_TURN_INFO = "boxes_turn"


def _boxes_turn(img):
    """orientation.boxes_turn of the Pillow image `img`, as the file opened: the Orientation its getexif declares (EXIF,
    else XMP tiff:Orientation), 1 when none or the EXIF does not parse."""
    try:
        declared = img.getexif().get(0x0112, 1)
    except Exception:
        declared = 1
    return orientation.boxes_turn(declared, img.format == "TIFF")


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


def tail_check(photo_path):
    """How many zero bytes the file at `photo_path` ends in (zero_tail_of), having read only the end of
    it: for the doctor's count of photos whose file fails to open (tagpup.services.inspect), without
    decoding them. Raises Unreadable for an empty file, and what the system raised for one that cannot
    be read. A zero-filled tail is `zero_tail_of`'s rule, at ZERO_TAIL, as the indexer's (#528), and
    nothing is judged of what follows the picture -- a Samsung trailer, an MP4 appended to a motion
    photo, 0xFF or zero padding below ZERO_TAIL, bytes after a PNG's IEND all decode. It cannot see
    damage in the middle of a picture, which the indexer's full decode (`opened`) does. The index's bake
    asks it of each photo it would write, and writes nothing into one ending in zeros (tagpup.services.
    bake_after_index._incomplete, #407); no other write asks it (DECISIONS 2026-10-15)."""
    if os.path.getsize(photo_path) == 0:   # unbounded: the doctor bounds the whole look (inspect, shares.bounded); the bake asks after the file's decode answered
        raise Unreadable("empty", "the file is empty")
    return zero_tail_of(photo_path)


def shown_shape(photo_path):
    """(width, height, oriented, turn): the size Pillow shows a photo at, whether it turned the
    picture by its Orientation as it loaded it, and the Orientation its face boxes are turned by
    (orientation.boxes_turn: the one the file declares, 1 when it declares none or is a TIFF) -- from
    one open of the file.

    Face boxes are in the coordinates Pillow shows a photo in. For most formats that is the stored
    pixels, which an Orientation change leaves where they are; Pillow applies a TIFF's Orientation as it
    loads it, so a TIFF's boxes turn with the photo. 2 to 8 turn or flip the picture as it is shown, and
    a face's box stays in the stored pixels: the boxes of such a photo do not sit on its faces when drawn
    over the picture as it is shown (the tabled orientation redesign). It was also `shown_size`, the
    same open less the Orientation."""
    with Image.open(photo_path) as img:
        oriented = img.format == "TIFF"
        if oriented:
            img.load()
        width, height = img.size
        turn = _boxes_turn(img)
    return width, height, oriented, turn


def picture_size(photo_path):
    """(width, height) of a photo's picture read from its header alone, nothing decoded: what the indexer reserves
    memory by before it decodes a photo ahead (tagpup.core.read_ahead). (0, 0) for a file whose header does not
    parse -- an empty file, one of zeros, one that is no picture --: its decode, next, says how it is damaged
    (opened). A file that cannot be opened raises what the system raised. Not shown_shape: that one decodes a
    TIFF in full to learn its turned size and raises for a damaged header, and the read-ahead needs neither."""
    try:
        with Image.open(photo_path) as img:
            return img.size
    except Exception as error:
        if not _decoder_failed(error):
            raise
        return 0, 0


def is_photo(path):
    """Does `path` name a photo, by its extension (PHOTO_EXTENSIONS)?"""
    return os.path.splitext(str(path).lower())[1] in PHOTO_EXTENSIONS


#: The servers send a photo's bytes only for a photo: the path is the caller's.
is_servable = is_photo


def kind_of(path):
    """'photo' or 'video', what `path` names by its extension (PHOTO_EXTENSIONS, VIDEO_TYPES): the kind of the row the
    index keeps for it (photos.kind); None for any other file."""
    extension = os.path.splitext(str(path).lower())[1]
    if extension in PHOTO_EXTENSIONS:
        return "photo"
    if extension in VIDEO_TYPES:
        return "video"
    return None


def is_clip(path):
    """Does `path` name a clip, a row of kind 'video' (kind_of)? Asked by extension, as the index gave the row its kind:
    nothing is read."""
    return kind_of(path) == "video"


#: Why a clip's file is not written: a turn is ExifTool's rewrite of the whole file -- gigabytes, over a share
#: (DECISIONS 2026-11-02, items 1 and 3) -- and a time shift moves dates a clip's XMP does not hold; its tags, title,
#: Date Taken and the name it had before its first rename are patched into its XMP only by a journaled write, in a
#: folder the library holds (field_values.writes; DECISIONS 2026-11-05, M7, and 2026-11-08, A5 and A5 (D) REVISED), so a
#: clip of another folder is not renamed or dated either. The one sentence of the refusal
#: (tagpup.services.file_changes.leave_out_clips, field_values.write).
CLIP_NOT_EDITED = ("TagPup changes only a video's tags, title, name and Date Taken, and only in a folder the library "
                   "holds; it does not turn a video or shift its time with a camera: this video's file was left as it "
                   "is.")

#: Why a clip whose tags or title are saved is not written (field_values.write):
#: its XMP cannot be patched where it is (a tail after its last box, two XMP boxes, no moov; in_place.patch_clip), and
#: ExifTool's rewrite of the whole file is not made (DECISIONS 2026-11-05, M7, Q2).
CLIP_NOT_PATCHED = ("TagPup cannot save into this video's file yet (its boxes are laid out in a way TagPup does not "
                    "patch): nothing was written.")

def is_media(path):
    """Does `path` name a file the index keeps, a photo or a clip (kind_of)? What the walks below find."""
    return kind_of(path) is not None


def walks_into(entry):
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


def photo_entries(folder, unlisted=None, listed=None):
    """Each photo and clip (is_media) under `folder`, at any depth, as its folder's listing entry, in the
    order os.walk gives: a folder's photos, then each subfolder in turn. Walked from
    paths.stored(folder), each folder listed through shares.listing; a folder that cannot be
    listed, whose listing fails part-way, or on a share that does not answer within its wait
    (a share dropping mid-walk, #2104), is passed over and, when `unlisted` (a list) is given,
    appended to it: what it holds was not looked at, which is not the same as not there (#1356). `listed(folder, photos)`
    is told each folder listed, once its photos are handed on: how far a long walk has got."""
    pending = [paths.stored(folder)]
    while pending:
        current = pending.pop()
        try:
            listing = shares.listing(current)
        except OSError:
            listing = shares.UNANSWERED
        if listing is shares.UNANSWERED:
            if unlisted is not None:
                unlisted.append(current)
            continue
        subfolders, photos = [], 0
        for entry in listing:
            if walks_into(entry):
                subfolders.append(entry.path)
            elif is_media(entry.name):
                try:
                    if not entry.is_dir():
                        photos += 1
                        yield entry
                except OSError:
                    continue
        if listed is not None:
            listed(current, photos)
        pending.extend(reversed(subfolders))


def _listing(folder):
    """shares.listing(folder), or [] when it cannot be listed or does not answer: what the one-folder looks below take
    a folder they cannot look at for."""
    try:
        found = shares.listing(folder)
    except OSError:
        return []
    return [] if found is shares.UNANSWERED else found


def photos_under(folder):
    """The photos under `folder`, at any depth, in the form the index stores: walked from
    paths.stored(folder), as os.walk joins onto whatever it is given, and a folder typed
    D:/Photos gave D:/Photos\\a.jpg. Five walks each had a copy of this, two walking the
    folder as typed. No link or junction to a folder is walked into (walks_into)."""
    return [entry.path for entry in photo_entries(folder)]


def has_subfolders(folder):
    """Does `folder` hold a folder the walk would go into (walks_into): not a link or a
    junction? False for one that cannot be listed, or on a share that does not answer."""
    return any(walks_into(entry) for entry in _listing(folder))


def photos_in(folder):
    """The photos and clips (is_media) directly in `folder`, not in its subfolders, in the form the index stores:
    what indexing a folder alone reads (sync's new files in a folder the library holds)."""
    found = []
    for entry in _listing(folder):
        try:
            if is_media(entry.name) and not walks_into(entry) and not entry.is_dir():
                found.append(entry.path)
        except OSError:
            continue
    return found


def stamps_in(folder):
    """{paths.key: (path as stored, mtime, size)} of the photos and clips directly in `folder`, not in its subfolders:
    photos_in, each with the stamp its listing gives, as stamps_under does for a folder and all below it."""
    found = {}
    for entry in _listing(folder):
        if is_media(entry.name) and not walks_into(entry):
            try:
                if entry.is_dir():
                    continue
                stat_of = entry.stat()
            except OSError:
                continue
            found[paths.key(entry.path)] = (entry.path, stat_of.st_mtime, stat_of.st_size)
    return found


def stamps_under(folder, unlisted=None, listed=None):
    """{paths.key: (path as stored, mtime, size)} of the photos and clips under `folder`, at any
    depth: the photos photos_under finds, each with the stamp its folder's listing gives.
    On Windows the listing carries both, so no file is opened or looked up on its own: a
    sync that finds nothing costs one walk (docs/ARCHITECTURE.md, phase 8). The folders
    that could not be listed go into `unlisted`, and each folder listed is told to `listed`, as photo_entries does."""
    found = {}
    for entry in photo_entries(folder, unlisted, listed):
        try:
            stat_of = entry.stat()
        except OSError:
            continue
        found[paths.key(entry.path)] = (entry.path, stat_of.st_mtime, stat_of.st_size)
    return found


def content_type(photo_path):
    """The Content-Type a photo's or a video's own bytes go out under (PHOTO_TYPES, then VIDEO_TYPES)."""
    extension = os.path.splitext(photo_path)[1].lower()
    return PHOTO_TYPES.get(extension) or VIDEO_TYPES.get(extension, "application/octet-stream")


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
    (shown_shape). Either way its `info[BOXES_TURN_INFO]` is the Orientation those boxes are
    turned by to the picture upright (orientation.boxes_turn), read before it was turned.

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
            turn = _boxes_turn(img)
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
    img.info[BOXES_TURN_INFO] = turn
    return img


def from_jpeg(data):
    """The RGB picture of JPEG bytes in memory: a clip's frame (tagpup.files.video_frames), upright already (ffmpeg
    applies a video's rotation), so its `info[BOXES_TURN_INFO]` is 1, no turn: a face found on it is in the frame as
    stored (the face model's picture of a frame, scrfd_adaface.ScrfdAdaFace.picture_of_frame). Raises Unreadable when
    the bytes do not decode."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            picture = img.convert("RGB")
            picture.load()
            picture.info[BOXES_TURN_INFO] = 1
            return picture
    except Exception as error:
        if not _decoder_failed(error):
            raise
        raise damage(data, error) from error


#: What a fingerprint is a hash of, as a bake and a library's fingerprint row name it (docs/PHASE6_DESIGN.md, section 7):
#: sha256 of the upright RGB decode, under the Pillow requirements.txt pins. A change of what is hashed, or a Pillow whose
#: decode differs, is a new key, so a fingerprint made the old way is never compared with one made the new way.
FINGERPRINT_KEY = "upright-sha256-1"


def fingerprint(img):
    """The sha256 digest (32 bytes) of the picture `img`, as `opened(path, upright=True)` gives it: its size, then its
    RGB pixels. Nothing of the file's but its picture goes in -- its metadata, its name, its ICC profile, an alpha channel
    (`opened` drops it) -- so a keyword write or a rename leaves it, and a rotation, which changes the upright picture,
    changes it. The size is hashed with the pixels: a picture of one colour turned a quarter has the same pixel bytes,
    and is a different picture (FINGERPRINT_KEY)."""
    digest = hashlib.sha256(b"%dx%d\n" % img.size)
    digest.update(img.tobytes())
    return digest.digest()


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
    """A face box as stored, the JSON list [x1, y1, x2, y2], as a list of numbers; [] for none. Every box is
    JSON (200,962 of 200,962 in the three libraries, 2026-10-15): the reading of a bare list for older rows had
    no row to read."""
    return json.loads(box) if box else []


#: The shorter side, in pixels of the picture as stored, under which a face is not made new by the detector's passes and
#: not baked into a file (owner, 2026-10-18 and 2026-10-25): smudges no one can recognise.
NEW_FACE_FLOOR = 40


def is_big_enough(box):
    """Whether a face in `box` ([x1, y1, x2, y2] in pixels of the picture as stored) is at least NEW_FACE_FLOOR on its
    shorter side: the one test of a face made new (tagpup.ml.scrfd_adaface.new_faces) and of a face baked
    (tagpup.services.baking.plan_photo)."""
    return min(box[2] - box[0], box[3] - box[1]) >= NEW_FACE_FLOOR


def face_crop(photo_path, box):
    """The face in `box` (in the picture as stored) cut from its photo as a person sees it, as a JPEG no larger than
    CROP_SIZE on a side (face_crop_of of the photo opened upright)."""
    return face_crop_of(opened(photo_path, upright=True), box)


def face_crop_of(img, box):
    """The face in `box`, a box of the picture as stored, cut from `img`, the photo's picture upright as
    `opened(path, upright=True)` gives it (its `info[BOXES_TURN_INFO]` the turn between the two): the box turned upright
    (orientation.to_upright) and clamped to the picture, the face as a person sees it (DECISIONS 2026-10-27), as the JPEG
    a face's crop is kept as (crop_jpeg): what the index, Suggest and a crop cut again keep. A box that
    falls outside the picture gives a plain grey square."""
    turn = img.info[BOXES_TURN_INFO]
    width, height = img.size
    # The stored picture's size is the upright one turned back: a swap is its own inverse.
    box = orientation.to_upright(box, *orientation.upright_size(width, height, turn), turn)
    x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
    x2, y2 = min(width, int(box[2])), min(height, int(box[3]))
    if (x2 - x1) <= 0 or (y2 - y1) <= 0:
        return crop_jpeg(Image.new("RGB", (100, 100), color=(50, 50, 50)))
    return crop_jpeg(img.crop((x1, y1, x2, y2)))


def crop_jpeg(crop):
    """A face already cut from its photo (a Pillow image), as the JPEG a face's crop is
    kept as: no larger than CROP_SIZE on a side, at CROP_QUALITY."""
    if max(crop.size) > CROP_SIZE:
        crop = crop.copy()
        crop.thumbnail((CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=CROP_QUALITY)
    return out.getvalue()

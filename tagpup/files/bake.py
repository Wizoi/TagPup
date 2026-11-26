"""A bake: what TagPup worked out of a photo, kept in the photo file, so that a library given the file reads it rather
than working it out again (docs/PHASE6_DESIGN.md, sections 3, 6 and 7).

A bake is the models it was made under, the fingerprint of the picture it describes (tagpup.files.images.fingerprint),
the photo's vector, and its faces: how many were found -- none is an answer, "looked, none found" -- and for each a region
and a vector. Never a name, a tag, an exclusion: those are a library's own.

Where it lives in the file. Five values in TagPup's own XMP namespace, `XMP-tagpup` (NAMESPACE, URI): PhotoModel,
FaceModel, Fingerprint (`<key>:<sha256 hex>`), PhotoVector, FacesFound, and FaceVectors, a Seq of one vector per face in
the order of the regions. The regions are MWG's (`XMP-mwg-rs:RegionInfo`), so that other programs show them: upright, the
picture as a person sees it, AppliedToDimensions in pixels and each Area a centre and a size as fractions of it
(tagpup.core.orientation.area_of / to_stored turn them at the file's edge). A vector is float16, little-endian, in base64:
1,152 values are 3,072 characters, 512 are 1,368, one XMP value each, never one blob of every face. All the values together are
another matter: about 35 faces fill a JPEG's 64 KB XMP block, and ExifTool then moves FaceVectors whole to Extended
XMP, the regions and the photo's values staying in the main block (probed 2026-10-25; its detail, DECISIONS 2026-10-25).

Reading it. ExifTool answers the namespace by name with no configuration of ours. The record is ExifTool's own, read with
`-struct` (READ_ARGS) and the bake's FIELDS: without it RegionInfo is answered as flattened lists, and a list of one face
as a bare value. Not through tagpup.files.metadata, whose structured reading makes a list of one its item. `parse` takes
that record and gives a Bake, or Rejected with the reason, and never raises: a bake that is not whole and consistent is
not used, and the photo is worked out as if it held none. None of these fields is a METADATA_FIELD (tagpup.core.fields):
the index's copy of a file's fields is not where a bake goes.

Writing it. Nothing here writes a file: `values` says what a baked file is to hold, as the journal keeps a field's value
(a list of texts, the regions ONE text: region_text), and tagpup.services.file_changes.write_fields writes it through
tagpup.files.field_values, journaled and undoable, in a session opened with config_path(): ExifTool writes the namespace
only under its configuration (NAMESPACE_CONFIG).
"""
import base64
import binascii
import dataclasses
import hashlib
import math
import os
import tempfile
import threading

import numpy as np

from tagpup.core import fields, orientation
from tagpup.files import exiftool_session, images

#: TagPup's XMP namespace: its group as ExifTool names it, and its URI.
NAMESPACE = "XMP-tagpup"
URI = "http://ns.tagpup.app/bake/1.0/"

#: The keys of a bake in a record read under READ_ARGS (`-G` names the family-0 group, XMP).
PHOTO_MODEL = "XMP:PhotoModel"
FACE_MODEL = "XMP:FaceModel"
FINGERPRINT = "XMP:Fingerprint"
PHOTO_VECTOR = "XMP:PhotoVector"
FACES_FOUND = "XMP:FacesFound"
FACE_VECTORS = "XMP:FaceVectors"
REGIONS = "XMP:RegionInfo"

#: The values every bake holds; TagPup's own values (FaceVectors only when it found a face); every field a bake is read from.
REQUIRED = (PHOTO_MODEL, FACE_MODEL, FINGERPRINT, PHOTO_VECTOR, FACES_FOUND)
OWN = REQUIRED + (FACE_VECTORS,)
FIELDS = OWN + (REGIONS,)

#: The field ExifTool writes each of FIELDS as: TagPup's in its own namespace, the regions MWG's.
WRITTEN = dict({key: "%s:%s" % (NAMESPACE, key.split(":", 1)[1]) for key in OWN}, **{REGIONS: fields.REGION_INFO})

#: TagPup's namespace for ExifTool to write it and read it back (config_path): every session opens with it.
NAMESPACE_CONFIG = r"""%Image::ExifTool::UserDefined = (
    'Image::ExifTool::XMP::Main' => {
        tagpup => { SubDirectory => { TagTable => 'Image::ExifTool::UserDefined::tagpup' } },
    },
);
%Image::ExifTool::UserDefined::tagpup = (
    GROUPS => { 0 => 'XMP', 1 => 'XMP-tagpup', 2 => 'Image' },
    NAMESPACE => { 'tagpup' => '""" + URI + r"""' },
    WRITABLE => 'string',
    PhotoModel => { }, FaceModel => { }, Fingerprint => { }, PhotoVector => { },
    FacesFound => { Writable => 'integer' },
    FaceVectors => { List => 'Seq' },
);
1;
"""

#: How a bake is read: a session's common arguments, with `-struct`.
READ_ARGS = tuple(exiftool_session.DEFAULT_COMMON_ARGS) + ("-struct",)

#: The length of a photo vector (SigLIP 2, the libraries' photo model, 2026-10-24) and of a face vector.
PHOTO_DIM = 1152
FACE_DIM = 512

#: The longest a value may be: every one of a bake is one XMP value, and a JPEG's XMP block holds 64 KB.
MAX_VALUE = 64 * 1024

#: The reason of a record holding none of a bake's own values: the common case, nothing baked.
NOT_BAKED = "the file holds no bake"

#: How a vector is kept: float16, little-endian.
_HALF = np.dtype("<f2")


@dataclasses.dataclass(frozen=True, eq=False)
class Face:
    """One face of a bake: its MWG Area (X, Y, W, H: the centre and the size, fractions of the upright picture) and its
    vector (float32)."""
    area: tuple
    vector: np.ndarray


@dataclasses.dataclass(frozen=True, eq=False)
class Bake:
    """A whole, consistent bake. `applied` is the (W, H) its regions were made against, None when it found no faces;
    `fingerprint` the 32-byte digest under `fingerprint_key`."""
    photo_model: str
    face_model: str
    fingerprint_key: str
    fingerprint: bytes
    photo_vector: np.ndarray
    applied: tuple
    faces: tuple


@dataclasses.dataclass(frozen=True)
class Rejected:
    """A record that holds no usable bake, and why."""
    reason: str


def encode_vector(vector):
    """The base64 text of `vector`, as float16 little-endian. Raises ValueError for a value float16 cannot hold."""
    with np.errstate(over="ignore"):
        half = np.asarray(vector, dtype=np.float32).astype(_HALF)
    if not np.all(np.isfinite(half)):
        raise ValueError("a vector holds a value float16 cannot keep")
    return base64.b64encode(half.tobytes()).decode("ascii")


def decode_vector(text, dim):
    """The float32 vector of `dim` values that `text` (encode_vector's) holds. Raises ValueError when it is not base64,
    not `dim` values long, or holds a value that is not a finite number."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("a vector is not base64: %s" % error) from None
    if len(raw) != dim * _HALF.itemsize:
        raise ValueError("a vector of %d bytes, not the %d of %d values" % (len(raw), dim * _HALF.itemsize, dim))
    vector = np.frombuffer(raw, dtype=_HALF).astype(np.float32)
    if not np.all(np.isfinite(vector)):
        raise ValueError("a vector holds a value that is not a finite number")
    return vector


def _text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("%s is not text" % key)
    if len(value) > MAX_VALUE:
        raise ValueError("%s is longer than %d characters" % (key, MAX_VALUE))
    return value


def _number(value, what):
    # ExifTool answers a number of 17 significant digits as a string ("0.20833333333333334", a region Area as
    # orientation.area_of gives it: tests/test_a_bake_is_used_only_whole.py), and a shorter one as a number.
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s is not a finite number" % what)
    return float(value)


def _holds_config(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read() == NAMESPACE_CONFIG
    except OSError:
        return False


def config_path():
    """The path of a file holding NAMESPACE_CONFIG, for the `config_file=` of a session that writes a bake: in the user's
    temp folder, named for its text, so every process and thread shares one file of one text and a changed text is a new
    file. Written whole under a name of its own and moved into place, so a process stopped part way leaves no half of it."""
    digest = hashlib.sha256(NAMESPACE_CONFIG.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), "tagpup-bake-%s.config" % digest)
    if _holds_config(path):
        return path
    part = "%s.%d.%d.part" % (path, os.getpid(), threading.get_ident())
    with open(part, "w", encoding="utf-8", newline="") as handle:
        handle.write(NAMESPACE_CONFIG)
    try:
        os.replace(part, path)
    except OSError:
        # The server and a CLI run writing it at once: Windows refuses the replace of a file the other's ExifTool has
        # open. The other's holds the same text, or this is an error.
        os.remove(part)
        if not _holds_config(path):
            raise
    return path


#: The characters of ExifTool's serialised structure, written in a value after a "|" (exiftool.org/struct.html).
_SPECIAL = frozenset("|,[]{}")


def _escaped(text):
    # Its special characters after a "|", and so each blank of a run at either edge: ExifTool trims those unless escaped,
    # and another program's name "  Lead Space  " was put back as "Lead Space" (#1880).
    start, end = len(text) - len(text.lstrip()), len(text.rstrip())
    return "".join("|" + char if char in _SPECIAL or not start <= n < end else char for n, char in enumerate(text))


def _number_text(value):
    # One text of one number: 4000 and 4000.0 are "4000"; a fraction is repr's, the shortest that reads back as the same
    # float (17 significant digits at most), and ExifTool keeps the text it is given, so it reads back as written.
    number = _number(value, "a value of the regions")
    return str(int(number)) if number.is_integer() and abs(number) < 2 ** 53 else repr(number)


def _serialised(value):
    if isinstance(value, dict):
        return "{%s}" % ",".join("%s=%s" % (key, _serialised(value[key])) for key in sorted(value))
    if isinstance(value, list):
        return "[%s]" % ",".join(_serialised(item) for item in value)
    if isinstance(value, str):
        return _escaped(value)
    return _number_text(value)


def region_text(info):
    """The one text of an MWG RegionInfo structure `info` -- as ExifTool answers it read with -struct, or as values()
    makes it -- in ExifTool's serialised form, which a write takes as it is (`-XMP-mwg-rs:RegionInfo=<text>`). Canonical,
    so the text of the structure a write left reads back as the text written: names sorted, a number by one formatting
    (_number_text: no rounding, the writer keeps a float exactly), a text as it is with its special characters, and the blanks at its edges, escaped.
    Another program's regions are kept so too, whatever they name. Raises ValueError for a value that is none of a
    structure, a list, a text or a finite number."""
    if not isinstance(info, dict):
        raise ValueError("the regions are not a structure")
    return _serialised(info)


def values(photo_vector, faces, applied, fingerprint, photo_model, face_model):
    """What a photo file holding this bake is to hold, {field ExifTool writes (WRITTEN): [texts]}, as
    tagpup.services.file_changes.write_fields takes a plan's after. `faces`, [(box, vector)] in the order the file is to
    list them, each box [x1, y1, x2, y2] in pixels of the upright picture, whose (W, H) is `applied`; `fingerprint`, its
    digest (images.fingerprint). No faces is "looked, none found": FacesFound "0", and no regions -- another program's
    are cleared, as they are replaced by a bake's (PHASE6_DESIGN, section 8). A face's prob is never baked."""
    width, height = applied
    held = {PHOTO_MODEL: [photo_model], FACE_MODEL: [face_model],
            FINGERPRINT: ["%s:%s" % (images.FINGERPRINT_KEY, bytes(fingerprint).hex())],
            PHOTO_VECTOR: [encode_vector(photo_vector)], FACES_FOUND: [str(len(faces))],
            FACE_VECTORS: [encode_vector(vector) for _box, vector in faces], REGIONS: []}
    if faces:
        listed = [{"Type": "Face", "Area": dict(zip("XYWH", orientation.area_of(box, width, height)), Unit="normalized")}
                  for box, _vector in faces]
        held[REGIONS] = [region_text({"AppliedToDimensions": {"W": width, "H": height, "Unit": "pixel"},
                                      "RegionList": listed})]
    return {WRITTEN[key]: texts for key, texts in held.items()}


def fingerprint_of(text):
    """(key, 32-byte digest) of a Fingerprint value (`<key>:<sha256 hex>`, values' spelling). Raises ValueError for
    a key other than images.FINGERPRINT_KEY or a digest that is not a sha256."""
    key, _, digest = text.partition(":")
    if key != images.FINGERPRINT_KEY:
        raise ValueError("a fingerprint under the key %r, not %r" % (key, images.FINGERPRINT_KEY))
    try:
        held = bytes.fromhex(digest)
    except ValueError:
        held = b""
    if len(held) != 32:
        raise ValueError("a fingerprint that is not a sha256")
    return key, held


def _regions(info):
    """((W, H), [(X, Y, W, H), ...]) of an MWG RegionInfo read with -struct."""
    if not isinstance(info, dict):
        raise ValueError("the regions are not a structure")
    dims, listed = info.get("AppliedToDimensions"), info.get("RegionList")
    if not isinstance(dims, dict) or dims.get("Unit") != "pixel":
        raise ValueError("the regions' AppliedToDimensions are not in pixels")
    applied = (_number(dims.get("W"), "AppliedToDimensions W"), _number(dims.get("H"), "AppliedToDimensions H"))
    if min(applied) <= 0:
        raise ValueError("the regions' AppliedToDimensions are empty")
    if not isinstance(listed, list):
        raise ValueError("the RegionList is not a list")
    areas = []
    for region in listed:
        area = region.get("Area") if isinstance(region, dict) else None
        if not isinstance(area, dict) or region.get("Type") != "Face" or area.get("Unit") != "normalized":
            raise ValueError("a region that is not a face with a normalized Area")
        x, y, w, h = (_number(area.get(name), "an Area's " + name) for name in ("X", "Y", "W", "H"))
        if w <= 0 or h <= 0 or w > 1 or h > 1:
            raise ValueError("a face region of no size, or larger than the picture")
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("a face region whose centre is outside the picture")
        areas.append((x, y, w, h))
    return applied, areas


def parse(record):
    """The Bake that ExifTool's `record` of a photo (read with READ_ARGS, asked for FIELDS) holds, or Rejected and why:
    NOT_BAKED for a record holding none of the bake's own values. Never raises."""
    if not any(key in record for key in OWN):
        return Rejected(NOT_BAKED)
    missing = [key for key in REQUIRED if record.get(key) in (None, "")]
    if missing:
        return Rejected("the bake is missing " + ", ".join(missing))
    try:
        photo_model, face_model = _text(record, PHOTO_MODEL), _text(record, FACE_MODEL)
        key, digest = fingerprint_of(_text(record, FINGERPRINT))
        photo_vector = decode_vector(_text(record, PHOTO_VECTOR), PHOTO_DIM)
        found = record[FACES_FOUND]
        if isinstance(found, bool) or not isinstance(found, int) or found < 0:
            raise ValueError("FacesFound is not a count")
        vectors = record.get(FACE_VECTORS, [])
        if not isinstance(vectors, list):
            raise ValueError("FaceVectors is not a list")
        if found == 0:
            if REGIONS in record or vectors:
                raise ValueError("FacesFound is 0 and the file holds regions or face vectors")
            return Bake(photo_model, face_model, key, digest, photo_vector, None, ())
        if REGIONS not in record:
            raise ValueError("FacesFound is %d and the file holds no regions" % found)
        applied, areas = _regions(record[REGIONS])
        if not found == len(areas) == len(vectors):
            raise ValueError("FacesFound is %d, with %d regions and %d face vectors" % (found, len(areas), len(vectors)))
        faces = []
        for area, text in zip(areas, vectors):
            if not isinstance(text, str) or len(text) > MAX_VALUE:
                raise ValueError("a face vector is not text under %d characters" % MAX_VALUE)
            faces.append(Face(area, decode_vector(text, FACE_DIM)))
    except ValueError as error:
        return Rejected(str(error))
    return Bake(photo_model, face_model, key, digest, photo_vector, applied, tuple(faces))

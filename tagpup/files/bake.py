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
another matter: about 35 faces fill a JPEG's 64 KB XMP block, and ExifTool then writes Extended XMP (cut 3 decides).

Reading it. ExifTool answers the namespace by name with no configuration of ours. The record is ExifTool's own, read with
`-struct` (READ_ARGS) and the bake's FIELDS: without it RegionInfo is answered as flattened lists, and a list of one face
as a bare value. Not through tagpup.files.metadata, whose structured reading makes a list of one its item. `parse` takes
that record and gives a Bake, or Rejected with the reason, and never raises: a bake that is not whole and consistent is
not used, and the photo is worked out as if it held none. Nothing here writes a bake, and none of these fields is a
METADATA_FIELD (tagpup.core.fields): the index's copy of a file's fields is not where a bake goes.
"""
import base64
import binascii
import dataclasses
import math

import numpy as np

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


def _fingerprint(text):
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
        key, digest = _fingerprint(_text(record, FINGERPRINT))
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

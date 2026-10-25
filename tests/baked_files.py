"""Photo files holding a bake (tagpup.files.bake), written by the one writer (tagpup.files.field_values.write), and the
records ExifTool answers of them: what the bake's tests and the index's read of a bake share.

    held = baked_files.record(faces=2)          # a record as ExifTool answers a baked photo
    baked_files.write(path, held)               # that record written into the photo at `path`
"""
import numpy as np

from tagpup.files import bake, field_values, images

import own_home

EXIFTOOL = own_home.installed_exiftool()
PHOTO_MODEL = "siglip2|webli|full-frame|2.0|native"
FACE_MODEL = "scrfd-adaface"
DIGEST = bytes(range(32))


def unit(dim, seed):
    """A unit vector `dim` long, the same for the same `seed`."""
    vector = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return vector / np.linalg.norm(vector)


def area(n):
    return (0.2 + 0.1 * n, 0.4, 0.125, 0.25)


def baked(photo_vector, faces, applied=(4000, 3000), *, photo_model=PHOTO_MODEL, face_model=FACE_MODEL,
          fingerprint=DIGEST):
    """A record as ExifTool answers a baked photo read with bake.READ_ARGS: `faces` [(Area, vector)] of a picture
    `applied` (W, H), `fingerprint` the digest (images.FINGERPRINT_KEY's)."""
    held = {"SourceFile": "C:/Pictures/Harbour.jpg", bake.PHOTO_MODEL: photo_model, bake.FACE_MODEL: face_model,
            bake.FINGERPRINT: "%s:%s" % (images.FINGERPRINT_KEY, fingerprint.hex()),
            bake.PHOTO_VECTOR: bake.encode_vector(photo_vector), bake.FACES_FOUND: len(faces)}
    if faces:
        held[bake.FACE_VECTORS] = [bake.encode_vector(vector) for _area, vector in faces]
        held[bake.REGIONS] = {"AppliedToDimensions": {"W": applied[0], "H": applied[1], "Unit": "pixel"},
                              "RegionList": [{"Type": "Face", "Area": dict(zip("XYWH", one), Unit="normalized")}
                                             for one, _vector in faces]}
    return held


def record(faces=2, **changed):
    """A record of a baked photo with `faces` faces (area(n), unit vectors of seeds 10 + n), each key of `changed` in
    place of the bake's (None: left out)."""
    held = baked(unit(bake.PHOTO_DIM, 1), [(area(n), unit(bake.FACE_DIM, 10 + n)) for n in range(faces)])
    held.update(changed)
    return {key: value for key, value in held.items() if value is not None}


def write(path, held):
    """Write the bake `held` (a record, as `baked` makes one: any of its values changed) into the photo at `path`, through
    the one writer, in the session a journaled write opens."""
    values = {bake.WRITTEN[key]: [str(held[key])] for key in bake.REQUIRED if key in held}
    if bake.REGIONS in held:
        values[bake.WRITTEN[bake.REGIONS]] = [bake.region_text(held[bake.REGIONS])]
    if bake.FACE_VECTORS in held:
        values[bake.WRITTEN[bake.FACE_VECTORS]] = list(held[bake.FACE_VECTORS])
    with field_values.session(EXIFTOOL) as et:
        field_values.write(et, path, values)
    return path

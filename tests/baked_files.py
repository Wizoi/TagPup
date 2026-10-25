"""Photo files holding a bake (tagpup.files.bake), made by ExifTool under a configuration of the tests' own -- the writer is
a later cut -- and the records ExifTool answers of them: what the bake's tests and the index's read of a bake share.

    held = baked_files.record(faces=2)                       # a record as ExifTool answers a baked photo
    baked_files.write(path, baked_files.config_in(folder), held)   # that record written into the photo at `path`
"""
import os

import numpy as np

from tagpup.files import bake, images
from tagpup.files.exiftool_session import ExifToolSession

import own_home

EXIFTOOL = own_home.installed_exiftool()
PHOTO_MODEL = "siglip2|webli|full-frame|2.0|native"
FACE_MODEL = "scrfd-adaface"
DIGEST = bytes(range(32))

#: TagPup's namespace for ExifTool to write it; reading needs none (bake's docstring).
CONFIG = r"""%Image::ExifTool::UserDefined = (
    'Image::ExifTool::XMP::Main' => {
        tagpup => { SubDirectory => { TagTable => 'Image::ExifTool::UserDefined::tagpup' } },
    },
);
%Image::ExifTool::UserDefined::tagpup = (
    GROUPS => { 0 => 'XMP', 1 => 'XMP-tagpup', 2 => 'Image' },
    NAMESPACE => { 'tagpup' => 'http://ns.tagpup.app/bake/1.0/' },
    WRITABLE => 'string',
    PhotoModel => { }, FaceModel => { }, Fingerprint => { }, PhotoVector => { },
    FacesFound => { Writable => 'integer' },
    FaceVectors => { List => 'Seq' },
);
1;
"""


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


def config_in(folder):
    """The path of CONFIG, written into `folder`."""
    path = os.path.join(folder, "bake.config")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(CONFIG)
    return path


def write(path, config, held, also=()):
    """Write the bake `held` (a record, as `baked` makes one) into the photo at `path` with ExifTool, and the arguments
    `also` (another field) in the same command."""
    args = ["-XMP-tagpup:%s=%s" % (key.split(":")[1], held[key]) for key in bake.REQUIRED if key in held]
    if bake.REGIONS in held:
        dims, listed = held[bake.REGIONS]["AppliedToDimensions"], held[bake.REGIONS]["RegionList"]
        regions = ",".join("{Type=Face,Area={X=%r,Y=%r,W=%r,H=%r,Unit=normalized}}" % tuple(
            region["Area"][name] for name in "XYWH") for region in listed)
        args.append("-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=%s,H=%s,Unit=pixel},RegionList=[%s]}"
                    % (dims["W"], dims["H"], regions))
    args += ["-XMP-tagpup:FaceVectors=" + text for text in held.get(bake.FACE_VECTORS, [])]
    with ExifToolSession(executable=EXIFTOOL, config_file=config) as et:
        et.execute(*args, *also, "-overwrite_original", path)
    return path

"""Damaged photo files for tests, made as the owner's were damaged (docs/findings.md, #407):
a JPEG cut short, a file of nothing but zero bytes, and a JPEG whose second half an
interrupted copy left as zero bytes -- which still decodes, grey below a line. Each is
made from a whole JPEG, so everything but the damage is what a camera's file holds.
"""
import os
import random

from PIL import Image

#: How many bytes the truncated JPEG is cut short by.
CUT = 700


def whole_jpeg(path, seed=7, size=(160, 120)):
    """A whole JPEG at `path`, of noise, so its picture data is most of the file. Returns
    its bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    noise = random.Random(seed).randbytes(size[0] * size[1] * 3)
    Image.frombytes("RGB", size, noise).save(path, "JPEG", quality=90)
    with open(path, "rb") as handle:
        return handle.read()


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(body)
    return path


def truncated(path, seed=11):
    """A JPEG cut CUT bytes short, as a copy that stopped leaves it."""
    body = whole_jpeg(path, seed)
    return _write(path, body[:-CUT])


def second_half_zeros(path, seed=13):
    """A JPEG of its full size whose second half is zero bytes -- well over
    images.ZERO_TAIL of them: what an interrupted copy into a file already given its
    size leaves. It decodes."""
    body = whole_jpeg(path, seed, size=(480, 360))
    half = len(body) // 2
    return _write(path, body[:half] + bytes(len(body) - half))


def all_zeros(path, size=9000):
    """A file named as a photo holding nothing but zero bytes."""
    return _write(path, bytes(size))


def unreadable(folder):
    """{kind: path} of one photo that does not decode of each kind made here, in
    `folder`, as images.Unreadable names them."""
    return {"truncated": truncated(os.path.join(folder, "cut short.jpg")),
            "all zeros": all_zeros(os.path.join(folder, "all zeros.jpg"))}

"""The bytes of an in-place patch of a file's metadata: the record the journal saves before the first byte is written,
and what a file holds against it (DECISIONS 2026-11-02, "VIDEO: THE JPEG PATCH IS BUILT WITH THE VIDEOS'").

ExifTool rewrites a whole file beside it and renames it over the original, so the file holds what it held or what it
was to hold, never half of each. A patch overwrites a few ranges of the file where they are, and a crash part-way leaves
some ranges written and some not, or one cut in the middle. So the patcher (field_values.write's in-place path) builds
its record here first (`record`), gives it to the journal (`saving`, tagpup.store.file_journal.save_patch), and only
when that returns writes a byte; whoever meets the file next settles it by its bytes (`held`), putting back what this
process tore (`put_back`), never what another program wrote.

A record is JSON, as `change_files.patch` holds it: {"v": 1, "size": the file's size, "ranges": [{"at", "len", "after":
the new bytes, base64}], "outside": null or {"sha256": of every byte outside the ranges, in order}}; the bytes the
ranges held before, concatenated in the ranges' order, are kept beside it (`change_files.patch_before`). The new bytes
are kept too, so a range this process tore is told from one another program wrote. Reads are of the open file, not
looks at its path: the caller has the file's state already (tagpup.services.file_changes reads it from the journal).
"""
import base64
import hashlib
import os

VERSION = 1

#: Whether a clip's record carries the hash of every byte outside its ranges, as a photo's always does. A photo's is two
#: full reads of a few megabytes, and finds a byte changed outside the patch (#2377); a clip's would be two full reads of
#: up to 10 GB, about what the rewrite the patch avoids costs. The owner has not decided (2026-11-02): until then, none.
CLIPS_HASHED = False

#: What a file holds against its record (held).
NEW, OLD, TORN, NEITHER, RESIZED, OUTSIDE = "new", "old", "torn", "neither", "resized", "outside"

#: The bytes read at a time for the hash of what lies outside the ranges.
BLOCK = 1 << 20


def _spans(record):
    return [(each["at"], each["len"], base64.b64decode(each["after"])) for each in record["ranges"]]


def _outside(handle, size, spans):
    """The sha256 of every byte of the open file outside `spans`, in order."""
    digest, at = hashlib.sha256(), 0
    for start, length, _after in spans + [(size, 0, b"")]:
        handle.seek(at)
        left = start - at
        while left > 0:
            block = handle.read(min(BLOCK, left))
            if not block:
                raise OSError("the file ended before its byte %d" % start)
            digest.update(block)
            left -= len(block)
        at = start + length
    return digest.hexdigest()


def _read_at(handle, at, length):
    handle.seek(at)
    return handle.read(length)


def record(handle, changes, clip=False):
    """(record, before) of a patch writing `changes`, [(at, new bytes)], into the file open in `handle` (binary, read):
    the record as the journal saves it, and the bytes the ranges hold now. Ranges are sorted and may not overlap, be
    empty or run past the end. The hash of what lies outside them is taken for a photo, and for a clip only when
    CLIPS_HASHED. Writes nothing."""
    size = handle.seek(0, os.SEEK_END)
    changes = sorted((int(at), bytes(new)) for at, new in changes)
    end = 0
    for at, new in changes:
        if not new or at < end or at + len(new) > size:
            raise ValueError("a patch's ranges are each some bytes inside the file, none overlapping another")
        end = at + len(new)
    spans = [(at, len(new), new) for at, new in changes]
    before = b"".join(_read_at(handle, at, length) for at, length, _new in spans)
    hashed = not clip or CLIPS_HASHED
    return {"v": VERSION, "size": size,
            "ranges": [{"at": at, "len": length, "after": base64.b64encode(new).decode("ascii")} for at, length, new in spans],
            "outside": {"sha256": _outside(handle, size, spans)} if hashed else None}, before


def held(path, record, before):
    """What the file at `path` holds against its patch's `record` and the bytes its ranges held `before`: NEW, every
    range as the patch wrote it; OLD, every range as it was; TORN, each byte one or the other but not all of one (a
    write this process cut short); NEITHER, a byte that is neither (another program wrote it); RESIZED, the file is not
    the size it was; OUTSIDE, the ranges are this process's but a byte outside them is not what it was. Raises OSError
    when the file cannot be read."""
    spans = _spans(record)
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size != record["size"]:
            return RESIZED
        now = b"".join(_read_at(handle, at, length) for at, length, _new in spans)
        new = b"".join(after for _at, _length, after in spans)
        if now == new:
            found = NEW
        elif now == before:
            found = OLD
        elif len(now) == len(new) == len(before) and all(n in (o, a) for n, o, a in zip(now, before, new)):
            found = TORN
        else:
            return NEITHER
        outside = record.get("outside")
        if outside and _outside(handle, size, spans) != outside["sha256"]:
            return OUTSIDE
    return found


def put_back(path, record, before):
    """Write the bytes the ranges held `before` back into the file at `path`, flushed to the disk, and read them back:
    raises OSError when the file cannot be written or does not then hold them."""
    spans, at_old = _spans(record), 0
    with open(path, "r+b") as handle:
        for at, length, _new in spans:
            handle.seek(at)
            handle.write(before[at_old:at_old + length])
            at_old += length
        handle.flush()
        os.fsync(handle.fileno())
    if held(path, dict(record, outside=None), before) != OLD:
        raise OSError("the bytes put back did not read back")

"""The bytes of an in-place patch of a file's metadata: the record the journal saves before the first byte is written,
and what a file holds against it (DECISIONS 2026-11-02, "VIDEO: THE JPEG PATCH IS BUILT WITH THE VIDEOS'").

ExifTool rewrites a whole file beside it and renames it over the original, so the file holds what it held or what it
was to hold, never half of each. A patch overwrites a few ranges of the file where they are, and a crash part-way leaves
some ranges written and some not, or one cut in the middle. So the patcher (field_values.write's in-place path) builds
its record here first (`record`), gives it to the journal (`saving`, tagpup.store.file_journal.save_patch), and only
when that returns writes a byte; whoever meets the file next settles it by its bytes (`held`), putting back what this
process tore (`put_back`), never what another program wrote.

A record is JSON, as `change_files.patch` holds it: {"v": 1, "size": the file's size, "ranges": [{"at", "len", "after":
the new bytes, base64}]}; the bytes the ranges held before, concatenated in the ranges' order, are kept beside it
(`change_files.patch_before`). The new bytes are kept too, so a range this process tore is told from one another program
wrote. A patch checks only the bytes it changed, against those two (DECISIONS 2026-11-03, "AN IN-PLACE PATCH CHECKS ONLY
THE BYTES IT CHANGED"): no other byte of the file is read, nor any hash taken of it, for a photo or a clip. Reads are of
the open file, not looks at its path: the caller has the file's state already (tagpup.services.file_changes reads it
from the journal).

**A JPEG's patch** (`patch_jpeg`). A photo's EXIF, IPTC (Photoshop APP13) and XMP segments are rewritten at their old
length, and only the bytes of them that differ are written. TagPup writes no serialiser of its own: ExifTool writes the
fields, as it does for a rewrite, into a copy of the photo's header alone (its segments up to and including the first
scan's header, then an end of image), on this PC's disk, and the segments it wrote are taken from that copy and fitted
to the old lengths (`jpeg_ranges`): XMP by the whitespace before its packet's end, EXIF by zero bytes after its last
data, IPTC by a dummy 8BIM resource (containers.PAD_RESOURCE; zero bytes there break ExifTool's next write and Windows'
reader). It is not a patch's, and the caller rewrites the whole file, when any of these holds: the file is no JPEG or
holds two segments of one kind, an extended XMP, or bytes after its last 8BIM resource; ExifTool would make, drop or
change any other segment (a photo with no XMP yet); a new segment is longer than the old; an EXIF segment would move
(a maker note may point at its own place in the file). The photo is opened once, with no sharing on Windows, from the
read of its header to the last byte written, so no other program writes it, nor reads it half written, meanwhile; one
that holds it open already makes it the rewrite's. Every read and write of it is bounded (shares.bounded, SECONDS).
"""
import base64
import io
import os
import tempfile
import threading

from tagpup.files import containers, shares, xmp_packet

VERSION = 1

#: Seconds a read or write of the photo may take (shares.bounded): a share that does not answer in it is away (Away).
SECONDS = 30.0

#: The segments a patch may rewrite, by containers.Segment.kind.
PATCHED_KINDS = ("exif", "xmp", "iptc")

_EOI = b"\xff\xd9"


class Away(OSError):
    """The share of the photo did not answer a read or write of it in SECONDS. What a write left is settled by its bytes
    against the record the journal saved (held), the next time."""

#: What a file holds against its record (held).
NEW, OLD, TORN, NEITHER, RESIZED = "new", "old", "torn", "neither", "resized"


def _spans(record):
    return [(each["at"], each["len"], base64.b64decode(each["after"])) for each in record["ranges"]]


def _read_at(handle, at, length):
    handle.seek(at)
    return handle.read(length)


def record(handle, changes):
    """(record, before) of a patch writing `changes`, [(at, new bytes)], into the file open in `handle` (binary, read):
    the record as the journal saves it, and the bytes the ranges hold now, the only bytes of the file read. Ranges are
    sorted and may not overlap, be empty or run past the end. Writes nothing."""
    size = handle.seek(0, os.SEEK_END)
    changes = sorted((int(at), bytes(new)) for at, new in changes)
    end = 0
    for at, new in changes:
        if not new or at < end or at + len(new) > size:
            raise ValueError("a patch's ranges are each some bytes inside the file, none overlapping another")
        end = at + len(new)
    spans = [(at, len(new), new) for at, new in changes]
    before = b"".join(_read_at(handle, at, length) for at, length, _new in spans)
    ranges = [{"at": at, "len": length, "after": base64.b64encode(new).decode("ascii")} for at, length, new in spans]
    return {"v": VERSION, "size": size, "ranges": ranges}, before


def _bounded(path, call):
    """What `call()` returned, on a thread bounded by SECONDS (shares.bounded); raises the OSError it raised, or Away."""
    answer, value = shares.bounded(path, call, SECONDS)
    if answer == "away":
        raise Away("the photo's folder did not answer in %d s" % SECONDS)
    if answer == "error":
        raise value
    return value


def held(path, record, before):
    """What the file at `path` holds against its patch's `record` and the bytes its ranges held `before`: NEW, every
    range as the patch wrote it; OLD, every range as it was; TORN, each byte one or the other but not all of one (a
    write this process cut short); NEITHER, a byte that is neither (another program wrote it); RESIZED, the file is not
    the size it was. Reads the file's size and its ranges, no other byte. Raises OSError when the file cannot be read
    (Away, when its share does not answer)."""
    return _bounded(path, lambda: _held(path, record, before))


def _held(path, record, before):
    spans = _spans(record)
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size != record["size"]:
            return RESIZED
        now = b"".join(_read_at(handle, at, length) for at, length, _new in spans)
        new = b"".join(after for _at, _length, after in spans)
    if now == new:
        return NEW
    if now == before:
        return OLD
    if len(now) == len(new) == len(before) and all(n in (o, a) for n, o, a in zip(now, before, new)):
        return TORN
    return NEITHER


def put_back(path, record, before):
    """Write the bytes the ranges held `before` back into the file at `path`, flushed to the disk, and read them back:
    raises OSError when the file cannot be written or does not then hold them (Away, when its share does not answer)."""
    spans, at_old, changes = _spans(record), 0, []
    for at, length, _new in spans:
        changes.append((at, before[at_old:at_old + length]))
        at_old += length

    def put():
        with _alone(path) as handle:
            _write(handle, changes)

    _bounded(path, put)
    if held(path, record, before) != OLD:
        raise OSError("the bytes put back did not read back")


def _alone(path):
    """The file at `path` open to read and write (binary), on Windows with no sharing: no other program may open it
    while this is open, and one that has it open already makes this fail (a sharing violation, winerror 32)."""
    if os.name != "nt":
        return open(path, "r+b")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE)
    read_write, no_sharing, existing, normal = 0xC0000000, 0, 3, 0x80
    handle = create(path, read_write, no_sharing, None, existing, normal, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY), "r+b")


def _write(handle, changes):
    """Write each (at, bytes) of `changes` into the open file, flushed to the disk."""
    for at, new in changes:
        handle.seek(at)
        handle.write(new)
    handle.flush()
    os.fsync(handle.fileno())


# --- a JPEG's patch -------------------------------------------------------------------------------------------------


def _header_of(handle):
    """(the JPEG's layout, its bytes up to its first scan's header's end) of the open file, or None when it is not a
    patch's: no JPEG, no scan found, two segments of one patched kind, an extended XMP, or bytes after the last 8BIM."""
    layout = containers.read(handle)
    if not isinstance(layout, containers.Jpeg) or layout.image_offset is None:
        return None
    if any(len(layout.of_kind(kind)) > 1 for kind in PATCHED_KINDS) or layout.of_kind("xmp-extension"):
        return None
    if any(segment.stray for segment in layout.of_kind("iptc")):
        return None
    end = layout.segments[-1].end
    handle.seek(0)
    header = handle.read(end)
    return (layout, header) if len(header) == end else None


def _fitted(old, new, written):
    """The segment `new` of the header ExifTool `written`, made the size of the old segment `old` (both containers.
    Segment of one kind); None when it does not fit."""
    least = new.size - new.slack
    if least > old.size:
        return None
    segment = written[new.offset:new.end]
    if new.kind == "xmp":
        packet_end = segment.rfind(b"<?xpacket end=")
        meta_end = segment.rfind(b"</x:xmpmeta>", 0, packet_end) + len(b"</x:xmpmeta>")
        room = packet_end - meta_end - (new.size - old.size)
        if packet_end < 0 or meta_end < len(b"</x:xmpmeta>") or room < containers.XMP_PADDING_LEAST:
            return None
        segment = segment[:meta_end] + xmp_packet.padding(room) + segment[packet_end:]
    elif new.kind == "exif":
        if new.offset != old.offset:
            return None
        segment = segment[:least] + bytes(old.size - least)
    else:
        if new.stray:
            return None
        pad = old.size - least
        if pad and (pad < containers.LEAST_RESOURCE or pad % 2):
            return None
        segment = segment[:least] + (containers.pad_resource(pad) if pad else b"")
    return segment[:2] + (old.size - 2).to_bytes(2, "big") + segment[4:]


def jpeg_ranges(header, written):
    """[(at, new bytes)] that make the JPEG whose bytes up to its first scan's header's end are `header` hold the
    segments ExifTool `written` (the same header, rewritten, then an end of image) at their old lengths: the bytes of
    each patched segment that differ, from the first to the last. None when they do not fit (the module says when); []
    when every segment, fitted, is what the photo holds. A segment ExifTool wrote otherwise can fit to the photo's own:
    an EXIF a patch made up to its old length with zero bytes, written again with the values it holds, comes without the
    zeros, and the fit puts them back (a second save of a photo whose first patch shortened its EXIF)."""
    old = containers.read(io.BytesIO(header + _EOI))
    new = containers.read(io.BytesIO(written))
    if not isinstance(new, containers.Jpeg) or new.image_offset is None:
        return None
    if [(each.marker, each.kind) for each in old.segments] != [(each.marker, each.kind) for each in new.segments]:
        return None
    changes = []
    for was, now in zip(old.segments, new.segments):
        before = header[was.offset:was.end]
        if written[now.offset:now.end] == before:
            continue
        if was.kind not in PATCHED_KINDS:
            return None
        after = _fitted(was, now, written)
        if after is None:
            return None
        if after == before:
            continue        # both old.size bytes: when they differ, a byte differs, and the two next() below find it
        first = next(at for at in range(was.size) if after[at] != before[at])
        last = next(at for at in range(was.size - 1, -1, -1) if after[at] != before[at])
        changes.append((was.offset + first, after[first:last + 1]))
    return changes


def patch_jpeg(path, serialise, saving):
    """Write the fields ExifTool would write into the JPEG at `path` by a patch of its metadata segments (the module):
    True once the file holds them -- a patch saved by `saving(record, before)` (the journal's, which commits the record
    before this writes a byte, and raises when it will not, nothing then written) and written; or nothing to write, the
    segments already holding them. False when it is not a patch's, nothing written: the caller rewrites the file.

    `serialise(source, target)` is ExifTool's write of the fields from the file at `source` into a new file at `target`
    (field_values.write's command): True when it wrote it, False when ExifTool refused (the rewrite then says why).
    Raises Away when the photo's share does not answer, the file as it was or as far as the patch wrote it (held settles
    it); an OSError that is no sharing violation or refusal to open; whatever `saving` raises."""
    try:
        handle = _bounded(path, lambda: _alone(path))
    except Away:
        raise
    except OSError:
        return False        # held open by another program, read-only, gone: the rewrite's, which says why
    lent = _Lent(handle)
    try:
        found = lent.bounded(path, lambda: _header_of(handle))
        if found is None:
            return False
        layout, header = found
        with tempfile.TemporaryDirectory(prefix="tagpup-patch-") as folder:
            source, target = os.path.join(folder, "header.jpg"), os.path.join(folder, "written.jpg")
            with open(source, "wb") as copy:
                copy.write(header + _EOI)
            if not serialise(source, target):
                return False
            with open(target, "rb") as copy:
                written = copy.read()
        changes = jpeg_ranges(header, written)
        if changes is None:
            return False
        if not changes:
            return True
        saved, before = lent.bounded(path, lambda: record(handle, changes))
        saving(saved, before)
        lent.bounded(path, lambda: _write(handle, changes))
        return True
    finally:
        lent.close()


class _Lent:
    """The photo's open handle across the bounded calls of a patch (_bounded): closed by `close` at the patch's end,
    unless a call that did not answer (Away) may still be using it on its thread; then by that call's end, or at once
    when the call never started. Not left to the garbage collector: the Away's traceback holds the patch's frame, and
    with it the handle, which no other program, nor a settle of this process, could open meanwhile."""

    def __init__(self, handle):
        self.handle, self.gate = handle, threading.Lock()
        self.lent = self.started = self.ended = False

    def bounded(self, path, work):
        def call():
            with self.gate:
                if self.lent:
                    return None         # began after its wait ran out: the handle is closed already
                self.started, self.ended = True, False
            try:
                return work()
            finally:
                with self.gate:
                    self.ended = True
                    if self.lent:
                        self.handle.close()
        try:
            return _bounded(path, call)
        except Away:
            with self.gate:
                self.lent = True
                if not self.started or self.ended:
                    self.handle.close()
            raise

    def close(self):
        with self.gate:
            if not self.lent:
                self.handle.close()

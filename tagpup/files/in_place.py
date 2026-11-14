"""The bytes of an in-place patch of a file's metadata: the record the journal saves before the first byte is written,
and what a file holds against it (DECISIONS 2026-11-02, "VIDEO: THE JPEG PATCH IS BUILT WITH THE VIDEOS'").

ExifTool rewrites a whole file beside it and renames it over the original, so the file holds what it held or what it
was to hold, never half of each. A patch overwrites a few ranges of the file where they are, and a crash part-way leaves
some ranges written and some not, or one cut in the middle. So the patcher (field_values.write's in-place path) builds
its record here first (`record`), gives it to the journal (`saving`, tagpup.store.file_journal.save_patch), and only
when that returns writes a byte; whoever meets the file next settles it by its bytes (`held`), putting back what this
process tore (`put_back`), never what another program wrote.

A record is JSON, as `change_files.patch` holds it: {"v": 2, "size": the file's size before, "size_after": its size
once written, "ranges": [{"at", "len", "after": the new bytes, base64}], "stale": [the fields the patch leaves holding
what they held]}; the bytes the ranges held before, those inside the old size, concatenated in the ranges' order, are
kept beside it (`change_files.patch_before`). A patch grows the file only by its last range (a clip's XMP box appended
at its end, or grown where it ends; DECISIONS 2026-11-05), so every byte past the old size is one the patch writes. `stale` (a keyword or
caption save that writes the XMP copy alone, fields.XMP_ONLY) is what a file holding every range as written holds of
the write: fields.as_patched, read back by `left_stale` when a crash leaves the file to be settled by its bytes. The new bytes are kept too, so a range this process tore is told from one another program
wrote. A patch checks only the bytes it changed, against those two (DECISIONS 2026-11-03, "AN IN-PLACE PATCH CHECKS ONLY
THE BYTES IT CHANGED"): no other byte of the file is read, nor any hash taken of it, for a photo or a clip. Reads are of
the open file, not looks at its path: the caller has the file's state already (tagpup.services.file_changes reads it
from the journal).

**A JPEG's patch** (`patch_jpeg`). A photo's EXIF, IPTC (Photoshop APP13) and XMP segments are rewritten at their old
length, and only the bytes of them that differ are written. The new header -- the photo's segments up to and including
the first scan's header, its EXIF, IPTC and XMP bodies changed and every other byte its own -- is the caller's
(`new_header`, field_values.write): TagPup's own writer (tagpup.files.segment_edits, field_values.OWN_WRITER), or,
until that is turned on, ExifTool's write of the fields into a copy of the header alone, on this PC's disk, whose
segments are fitted to the old lengths (`fitted`): XMP by the whitespace before its packet's end, EXIF by zero bytes
after its last data, IPTC by a dummy 8BIM resource (containers.PAD_RESOURCE; zero bytes there break ExifTool's next
write and Windows' reader). It is not a patch's, and the caller rewrites the whole file, when the file is no JPEG or
holds two segments of one kind, an extended XMP, or bytes after its last 8BIM resource (`_header_of`, whichever writer),
or the writer answers no header: ExifTool's when it would make, drop or change any other segment (a photo with no XMP
yet), a new segment is longer than the old, or an EXIF segment would move (a maker note may point at its own place in
the file); the own writer's by segment_edits' rules. The photo is opened once, with no sharing on Windows, from the
read of its header to the last byte written, so no other program writes it, nor reads it half written, meanwhile; one
that holds it open already makes it the rewrite's. Every read and write of it is bounded (shares.bounded, SECONDS).

**A clip's patch** (`patch_clip`). A MOV/MP4/M4V's keyword and caption save is its XMP alone (fields.CLIP_FIELDS; every
copy outside XMP is left stale, segment_edits.clip_stale_of). The clip's boxes are walked (containers.read: their
headers only) and its one XMP box's packet read whole, of any size (owner, 2026-11-12: the one bound is the journal's,
file_journal.PATCH_CAP, its record and the old bytes, whose `saving` refuses a larger patch before a byte is written;
a box over it is read before it is refused, none live, the largest 206,986 bytes); the new packet is the caller's
(`new_packet`, segment_edits.new_packet, a blank one for a clip with none), placed by clip_patch: over the old box and
the free boxes after it, grown where it ends the file, or appended in a new box at the end (the file grows by its last
range, the record saying its size after). A cleared title also frees the clip's QuickTime title item, four bytes, and
an undo gives it back the same way (clip_patch, DECISIONS 2026-11-08): the free boxes of its item list are read for
that, at most clip_patch.FREED_MOST each. The move of a clip's Windows Tags into its XMP frees its Xtra box the same way,
and an undo gives it back (DECISIONS 2026-11-09): the Xtra, or the free boxes of udta, are read whole for that, at most
clip_patch.FREED_MOST each. No other byte is read, nor any hash taken, of a clip of any size (DECISIONS
2026-11-03). It is not a patch's, nothing written, for no MOV/MP4/M4V, two XMP boxes, a packet the
writer cannot make, or a clip clip_patch will not place (a tail, no moov, fragments): such a clip is not written until
the whole-file rewrite (owner, 2026-11-05, Q2). It is opened once, alone, as a photo is; one another program holds open
(a player, an editor) is refused at once with a sentence (tagpup.core.result.Refused, OPEN_ELSEWHERE; owner,
2026-11-05, Q7), never waited for, the sentence naming who holds it when this PC can tell (lock_owners.explain, #2969). A crash after its record is saved, part-way or after the last byte is settled by its bytes like a photo's
(held: OLD, TORN, NEW); a torn grown file is put back to its old bytes and size (put_back).

**A clip's packet read** (`packet_held`): the packet a patch would read, read alone and bounded, for a conversion that
carries it whole into its copy (field_values.packet_held and packet_given, the pair tagpup.services.convert calls; owner
2026-11-13, #2938). Nothing is written.
"""
import base64
import io
import logging
import os
import threading

from tagpup.core.result import Refused
from tagpup.files import clip_patch, containers, lock_owners, shares, xmp_packet

logger = logging.getLogger(__name__)

VERSION = 2

#: Seconds a read or write of the photo may take (shares.bounded): a share that does not answer in it is away (Away).
SECONDS = 30.0

#: The segments a patch may rewrite, by containers.Segment.kind.
PATCHED_KINDS = ("exif", "xmp", "iptc")

#: The end of image marker: a header's copy ends with it, so a program reads it as a JPEG.
EOI = b"\xff\xd9"


class Away(OSError):
    """The share of the photo did not answer a read or write of it in SECONDS. What a write left is settled by its bytes
    against the record the journal saved (held), the next time."""


#: Why a clip open in another program when it is saved is refused at once, never waited for (owner, 2026-11-05).
OPEN_ELSEWHERE = ("it is open in another program (a player, an editor): close it there and save again; nothing was "
                  "written")

#: What a file holds against its record (held).
NEW, OLD, TORN, NEITHER = "new", "old", "torn", "neither"

#: What packet_held answers for a clip holding XMP no patch reads: two XMP boxes, or an item list's XMP_ item that is not
#: one data atom (clip_patch.xmp_box, clip_patch.packet_of).
UNREAD = "unread"


def _spans(record):
    return [(each["at"], each["len"], base64.b64decode(each["after"])) for each in record["ranges"]]


def _read_at(handle, at, length):
    handle.seek(at)
    return handle.read(length)


def record(handle, changes, stale=(), grow=False):
    """(record, before) of a patch writing `changes`, [(at, new bytes)], into the file open in `handle` (binary, read),
    leaving the fields `stale` as they were: the record as the journal saves it, and the bytes the ranges hold now inside
    the file, the only bytes of it read. Ranges are sorted and may not overlap or be empty; each lies inside the file,
    but the last of a patch that may `grow` the file, which starts inside it or at its end and may run past the end
    (the file is then `size_after` long). Writes nothing."""
    size = handle.seek(0, os.SEEK_END)
    changes = sorted((int(at), bytes(new)) for at, new in changes)
    end = 0
    for index, (at, new) in enumerate(changes):
        past = at + len(new) > size and not (grow and index == len(changes) - 1)
        if not new or at < end or at > size or past:
            raise ValueError("a patch's ranges are each some bytes inside the file, none overlapping another; only the"
                             " last of a patch that grows the file runs past its end")
        end = at + len(new)
    spans = [(at, len(new), new) for at, new in changes]
    before = b"".join(_read_at(handle, at, min(length, size - at)) for at, length, _new in spans)
    ranges = [{"at": at, "len": length, "after": base64.b64encode(new).decode("ascii")} for at, length, new in spans]
    return {"v": VERSION, "size": size, "size_after": max(size, end), "ranges": ranges, "stale": list(stale)}, before


def left_stale(record):
    """The fields the patch of `record` leaves holding what they held (record's `stale`). A record saved by the version
    installed before the key (its patches wrote every field) has none: ()."""
    return tuple(record.get("stale", ()))


def _bounded(path, call):
    """What `call()` returned, on a thread bounded by SECONDS (shares.bounded); raises the OSError it raised, or Away."""
    answer, value = shares.bounded(path, call, SECONDS)
    if answer == "away":
        raise Away("the photo's folder did not answer in %d s" % SECONDS)
    if answer == "error":
        raise value
    return value


def held(path, record, before):
    """What the file at `path` holds against its patch's `record` and the bytes its ranges held `before`: NEW, the size
    after and every range as the patch wrote it; OLD, the size before and every range as it was; TORN, a size from the
    one to the other and each byte of a range there one or the other, but not all of one (a write or a put back this
    process cut short); NEITHER, another size or a byte that is neither (another program wrote it, or the disk lost what
    this one wrote). A record of another version (v1, saved by the TagPup installed before record v2, which says no
    size after) is NEITHER without a look at the file: a conflict, never written over. Reads the file's size and its
    ranges, no other byte. Raises OSError when the file cannot be read (Away, when its share does not answer)."""
    return _bounded(path, lambda: _held(path, record, before))


def _held(path, record, before):
    if record.get("v") != VERSION:
        return NEITHER
    spans, size_before, size_after = _spans(record), record["size"], record["size_after"]
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        if not size_before <= size <= size_after:
            return NEITHER
        now = [_read_at(handle, at, min(length, size - at)) for at, length, _new in spans]
    if size == size_after and now == [after for _at, _length, after in spans]:
        return NEW
    if size == size_before and b"".join(now) == before:
        return OLD
    olds = _old_parts(spans, size_before, before)
    if all(_old_or_new(found, old, after) for found, old, (_at, _length, after) in zip(now, olds, spans)):
        return TORN
    return NEITHER


def _old_or_new(found, old, new):
    """Whether each byte of `found`, a range as the file holds it, is the byte `old` or `new` held at its place: `old` is
    shorter where the range runs past the file's old size, a place that held no byte before."""
    return all(byte == new[k] or (k < len(old) and byte == old[k]) for k, byte in enumerate(found))


def _old_parts(spans, size_before, before):
    """The bytes each range of `spans` held `before` (its part inside the file's old size, `size_before`)."""
    parts, at_old = [], 0
    for at, length, _new in spans:
        inside = max(0, min(length, size_before - at))
        parts.append(before[at_old:at_old + inside])
        at_old += inside
    return parts


def put_back(path, record, before):
    """Write the bytes the ranges held `before` back into the file at `path`, then cut it to the size it was, flushed to
    the disk, and read them back: raises OSError when the file cannot be written or does not then hold them (Away, when
    its share does not answer). The cut is last, so a put back cut short leaves the file TORN or OLD, never NEITHER:
    settled by its bytes as the patch's own crash is."""
    spans = _spans(record)
    changes = [(at, old) for (at, _length, _new), old in zip(spans, _old_parts(spans, record["size"], before)) if old]

    def put():
        with _alone(path) as handle:
            _write(handle, changes)
            _truncate(handle, record["size"])

    _bounded(path, put)
    if held(path, record, before) != OLD:
        raise OSError("the bytes put back did not read back")


def _alone(path):
    """The file at `path` open to read and write (binary), on Windows with no sharing: no other program may open it
    while this is open, and one that has it open already makes this fail (a sharing violation, winerror 32), a piece
    of a video being read (shares.read_span) too."""
    return lock_owners.opened(path, lock_owners.NO_SHARING, write=True)


def _write(handle, changes):
    """Write each (at, bytes) of `changes` into the open file, flushed to the disk."""
    for at, new in changes:
        handle.seek(at)
        handle.write(new)
    handle.flush()
    os.fsync(handle.fileno())


def _truncate(handle, size):
    """Cut the open file to `size` bytes, flushed to the disk."""
    handle.truncate(size)
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


def fitted(header, written):
    """The JPEG header `header` (its bytes up to its first scan's header's end) holding the segments ExifTool `written`
    (the same header, rewritten, then an end of image), each at its old length; None when they do not fit (the module
    says when). A segment ExifTool wrote otherwise can fit to the photo's own: an EXIF a patch made up to its old length
    with zero bytes, written again with the values it holds, comes without the zeros, and the fit puts them back (a
    second save of a photo whose first patch shortened its EXIF)."""
    old = containers.read(io.BytesIO(header + EOI))
    new = containers.read(io.BytesIO(written))
    if not isinstance(new, containers.Jpeg) or new.image_offset is None:
        return None
    if [(each.marker, each.kind) for each in old.segments] != [(each.marker, each.kind) for each in new.segments]:
        return None
    result = bytearray(header)
    for was, now in zip(old.segments, new.segments):
        if written[now.offset:now.end] == header[was.offset:was.end]:
            continue
        if was.kind not in PATCHED_KINDS:
            return None
        after = _fitted(was, now, written)
        if after is None:
            return None
        result[was.offset:was.end] = after
    return bytes(result)


def _ranges(layout, header, new):
    """[(at, new bytes)] that make `header` (of `layout`) hold `new`, a header of its length: the bytes of each patched
    segment that differ, from the first to the last; [] when they are the same. A byte outside the patched segments that
    differs is a writer's fault, never written: ValueError."""
    if len(new) != len(header) or new[:layout.segments[0].offset] != header[:layout.segments[0].offset]:
        raise ValueError("a new header is not the photo's header with its metadata segments changed")
    changes = []
    for segment in layout.segments:
        before, after = header[segment.offset:segment.end], new[segment.offset:segment.end]
        if after == before:
            continue
        if segment.kind not in PATCHED_KINDS:
            raise ValueError("a new header changed a %s segment, which no patch writes" % segment.kind)
        first = next(at for at in range(segment.size) if after[at] != before[at])
        last = next(at for at in range(segment.size - 1, -1, -1) if after[at] != before[at])
        changes.append((segment.offset + first, after[first:last + 1]))
    return changes


def patch_jpeg(path, new_header, saving):
    """Write a journaled write's fields into the JPEG at `path` by a patch of its metadata segments (the module): the
    fields it left holding what they held (a tuple, () when it wrote every field) once the file holds them -- a patch
    saved by `saving(record, before)` (the journal's, which commits the record before this writes a byte, and raises
    when it will not, nothing then written) and written; or nothing to write, the segments already holding them, the
    fields left all the same. None when it is not a patch's, nothing written: the caller rewrites the file.

    `new_header(layout, header)` is the writer's: given the photo's layout (containers.Jpeg) and its header's bytes, it
    answers (the header with the fields written, the same length; the fields left stale), or None, no patch.
    Raises Away when the photo's share does not answer, the file as it was or as far as the patch wrote it (held settles
    it); an OSError that is no sharing violation or refusal to open; whatever `new_header` or `saving` raises."""
    try:
        lent = _opened(path)
    except Away:
        raise
    except OSError:
        return None         # held open by another program, read-only, gone: the rewrite's, which says why
    with lent:
        found = lent.bounded(path, lambda: _header_of(lent.handle))
        if found is None:
            return None
        layout, header = found
        made = new_header(layout, header)
        if made is None:
            return None
        new, stale = made
        changes = _ranges(layout, header, new)
        if changes:
            _written(lent, path, changes, stale, saving)
        return tuple(stale)


# --- a clip's patch ------------------------------------------------------------------------------------------------


def patch_clip(path, new_packet, saving, title=None, category=None, dates=None):
    """Write a journaled write's fields into the XMP of the clip (MOV/MP4/M4V) at `path` by a patch (the module): the
    fields it left holding what they held (a tuple) once the file holds them -- a patch saved by `saving(record, before)`
    (the journal's, which commits the record before this writes a byte, and raises when it will not, nothing then
    written) and written; or nothing to write, the packet already holding them, the fields left all the same. None when
    it is not a patch's (clip_patch says when, and new_packet), nothing written: the clip is not written.

    `new_packet(old)` is the writer's (segment_edits.new_packet, its values given): given the packet of the clip's one
    XMP box (bytes; None for a clip with none), it answers (the new packet, the fields left stale) or None for no patch.
    `title`, the write's QuickTime:Title (clip_patch.patch): None not written, "" freed, a text given back by an undo.
    `category`, the write's Windows Tags (fields.CLIP_CATEGORY; clip_patch.patch): None not written, () its Xtra freed,
    texts given back by an undo.
    `dates`, the write's QuickTime header dates ({field of fields.CLIP_HEADER_DATES: texts}; clip_patch.patch): None or
    {} not written; a box holding its date already is not written, so a save whose XMP holds the date and whose headers
    do not writes the headers (#3002).
    Raises Refused(OPEN_ELSEWHERE) at once when another program holds the
    clip open; Away when its share does not answer, the file as it was or as far as the patch wrote it (held settles
    it); any other OSError the open or a write raised; whatever `new_packet` or `saving` raises."""
    with _opened_to_patch(path) as lent:
        found = lent.bounded(path, lambda: _packet_of(lent.handle, title, category))
        if found is None:
            return None
        layout, old, freed, xtra_read = found
        made = new_packet(old)
        if made is None:
            return None
        packet, stale = made
        if packet == old and title is None and category is None and not dates:
            return tuple(stale)
        placed = clip_patch.patch(layout, None if packet == old else packet, title, freed, category, xtra_read, dates)
        if placed is None:
            return None
        if placed.ranges:
            _written(lent, path, placed.ranges, stale, saving, grow=True, size=placed.size)
        return tuple(stale)


def packet_held(path):
    """The XMP packet the clip at `path` holds, as a patch reads it (_packet_in), for a conversion to carry whole into
    its copy (field_values.packet_held, its one caller): its bytes; None when it holds no XMP box, or is no MOV/MP4/M4V the walk reads
    (an old format, .wmv, .mpg or .avi, is containers.Unreadable: taken as holding no packet to carry, so its Convert
    goes on as before; decided by the main session 2026-11-13, #2938); UNREAD when it holds XMP no patch reads. Read
    with a plain open, bounded by SECONDS: the boxes' headers and the packet, no other byte; nothing written. Raises
    Away when its share does not answer, or the OSError of the open or a read."""
    def read():
        with open(path, "rb") as handle:
            layout = _video_of(handle)
            return None if layout is None else _packet_in(handle, layout)
    return _bounded(path, read)


def headers_held(path):
    """The QuickTime header dates the clip at `path` holds (clip_patch.header_dates: {field of fields.CLIP_HEADER_DATES:
    the texts of its boxes}), as a patch reads its boxes, for field_values.read, its one caller; None when it is no
    MOV/MP4/M4V the walk reads. Read with a plain open, bounded by SECONDS: the boxes' headers, no other byte; nothing
    written. Raises Away when its share does not answer, the OSError of the open or a read, ValueError for a header whose
    time it does not read."""
    def read():
        with open(path, "rb") as handle:
            return _video_of(handle)
    layout = _bounded(path, read)
    return None if layout is None else clip_patch.header_dates(layout)


def _video_of(handle):
    """The layout of the clip open in `handle` (containers.Video), or None when it is no MOV/MP4/M4V. Raises the OSError
    of a read that failed on the way (a share gone part-way through the walk), which is "could not be read", never "not
    a patch's" (#2540)."""
    layout = containers.read(handle)
    if isinstance(layout, containers.Unreadable) and layout.error is not None:
        raise layout.error
    return layout if isinstance(layout, containers.Video) else None


def _packet_in(handle, layout):
    """The packet the one XMP box of the clip open in `handle`, laid out as `layout`, holds: its bytes, None for no XMP
    box, UNREAD for XMP no patch reads (clip_patch.xmp_box, clip_patch.packet_of)."""
    if not layout.xmp:
        return None
    box = clip_patch.xmp_box(layout)
    if box is None:
        return UNREAD
    packet = clip_patch.packet_of(layout, _read_at(handle, box.payload, box.end - box.payload))
    return UNREAD if packet is None else packet


def _packet_of(handle, title=None, category=None):
    """(the clip's layout, the packet its one XMP box holds, None for none, the freed title boxes an undo giving `title`
    back reads: ((box, its bytes after its header), ...), clip_patch.freed_titles, the boxes a write of `category`
    reads: ((box, its bytes, header and all), ...), clip_patch.xtra_boxes) of the open file, or None when it is
    not a patch's: no MOV/MP4/M4V, or no XMP box a patch reads (_packet_in). Raises the OSError of a read that
    failed on the way (_video_of). Reads the boxes' headers, the packet and those boxes, no other byte."""
    layout = _video_of(handle)
    if layout is None:
        return None
    packet = _packet_in(handle, layout)
    if packet is UNREAD:
        return None
    freed = tuple((box, _read_at(handle, box.payload, box.end - box.payload))
                  for box in (clip_patch.freed_titles(layout) if title else ()))
    xtra_read = tuple((box, _read_at(handle, box.offset, box.size)) for box in clip_patch.xtra_boxes(layout, category))
    return layout, packet, freed, xtra_read


# --- the open file, shared by both patches ----------------------------------------------------------------------------


def _opened(path):
    """The file at `path` opened alone (_alone) on a bounded call, lent to the patch's later calls (_Lent, a context
    manager that closes it). Raises Away, or the OSError the open raised."""
    return _Lent(_bounded(path, lambda: _alone(path)))


def _opened_to_patch(path):
    """_opened, but a file another program holds open (lock_owners.looks_held: a sharing or lock violation; an open's
    access denied never is, #2970) is Refused(OPEN_ELSEWHERE and who holds it: lock_owners.explain) at once, never
    retried. Raises Away, or any other OSError the open raised."""
    try:
        return _opened(path)
    except Away:
        raise
    except OSError as error:
        if not lock_owners.looks_held(error):
            raise
        raise Refused(lock_owners.explain(error, path, OPEN_ELSEWHERE)) from None


def _written(lent, path, changes, stale, saving, grow=False, size=None):
    """Write `changes`, [(at, new bytes)], into the file `lent` holds: its record made (record), saved by `saving` (the
    journal's, which commits it before a byte is written), then the bytes written and flushed. ValueError, nothing saved
    nor written, when the record's size after is not `size`, the size its writer worked out (None: not checked)."""
    saved, before = lent.bounded(path, lambda: record(lent.handle, changes, stale, grow))
    if size is not None and saved["size_after"] != size:
        raise ValueError("a patch's record says the file is %d bytes once written, its writer %d"
                         % (saved["size_after"], size))
    saving(saved, before)
    lent.bounded(path, lambda: _write(lent.handle, changes))


class _Raised:
    """What a bounded call raised that is no OSError (a writer's fault, a test's crash), carried out of shares.bounded,
    which would make it an OSError of its text, to be raised as itself."""

    def __init__(self, error):
        self.error = error


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
            except OSError:
                raise
            except Exception as error:
                return _Raised(error)
            finally:
                with self.gate:
                    self.ended = True
                    if self.lent:
                        self.handle.close()
        try:
            value = _bounded(path, call)
        except Away:
            with self.gate:
                self.lent = True
                if not self.started or self.ended:
                    self.handle.close()
            raise
        if isinstance(value, _Raised):
            raise value.error
        return value

    def __enter__(self):
        return self

    def __exit__(self, *_raised):
        self.close()
        return False

    def close(self):
        with self.gate:
            if not self.lent:
                self.handle.close()

"""The fields TagPup owns in a JPEG's EXIF block, read and edited in place (docs/PLAN_OWN_METADATA.md, M3).

Pure: the EXIF segment's body (the bytes after its length field: "Exif\\0\\0" and the TIFF block) in, the same out; no
file. The layout of the TIFF block is `containers.exif_tiff`'s, its MakerNote's classes `containers.maker_note`'s.

`read(body)` is {name: value} of the owned fields the block holds (FIELDS: text, Orientation an int). `edit(body,
changes)` sets each named field (None or '' clears it) and answers `Edited`; every other byte stays where it was, and
the values of the other tags, the other IFDs, the MakerNote, IFD1 and its thumbnail, GPS and Interoperability, the
byte order, all keep their bytes and offsets. A value of the same length or shorter is written where it was (the rest
zeroed); a longer one goes after the last byte any IFD points at, into the slack, and its old bytes are zeroed. A new
tag takes a new entry, 12 bytes, in tag order: only where the 12 bytes after the IFD's table are bytes nothing points
at (the slack, or a gap); an IFD followed by data is refused, since moving that data would move what points at it.
The block keeps its length; a change that needs more than its slack answers `DoesNotFit` with the bytes it needs, and
only `grow=True` lets the block grow, at its end alone, after everything (never before the MakerNote's data or the
thumbnail). Bytes no IFD points at (the slack, a gap) are taken only beside a MakerNote whose every pointer lands
inside it as written, or as its Canon footer shifts them (or none): a note that may point outside itself might
point there, and a note moved by an unknown shift points outside itself as written.

Either answers `Refusal`, a sentence, for a block it does not wholly understand: it never repairs one and never answers
bytes on doubt (an edit is walked again and read back, and every byte it changed is checked to be an owned one, before
it is answered). The rules and the counts behind them: docs/domains/files.md, "The EXIF block editor".
"""
import re
import struct
from dataclasses import dataclass

from tagpup.files import containers

#: name: (IFD, tag, kind). "text": TIFF ASCII (type 2), NUL-ended, written as UTF-8 as ExifTool writes it; "date" and
#: "digits" text of that shape; "ucs2": an XP tag, UCS-2 little-endian whatever the block's byte order, NUL-ended,
#: written as BYTE (type 1) as ExifTool writes it; "short": Orientation, SHORT (type 3).
FIELDS = {
    "ImageDescription": ("ifd0", 0x010E, "text"),
    "Orientation": ("ifd0", 0x0112, "short"),
    "XPComment": ("ifd0", 0x9C9C, "ucs2"),
    "XPKeywords": ("ifd0", 0x9C9E, "ucs2"),
    "DateTimeOriginal": ("exif", 0x9003, "date"),
    "CreateDate": ("exif", 0x9004, "date"),
    "SubSecTime": ("exif", 0x9290, "digits"),
    "SubSecTimeOriginal": ("exif", 0x9291, "digits"),
    "SubSecTimeDigitized": ("exif", 0x9292, "digits"),
}
#: The types an owned tag is read in (each seen in the libraries' files), and the type it is written in.
_READ_TYPES = {"text": (2,), "date": (2,), "digits": (2,), "ucs2": (1, 2), "short": (3, 4)}
_WRITE_TYPE = {"text": 2, "date": 2, "digits": 2, "ucs2": 1, "short": 3}
_IFD_NAMES = {"ifd0": "IFD0", "exif": "Exif"}
LIMIT = containers.MOST_BODY
#: The MakerNote classes (containers.maker_note) whose every pointer lands inside the note, as written or shifted by
#: the shift its Canon footer records: beside such a note, a byte no IFD points at is free. Not "moved, shift
#: unknown" (main session, 2026-11-03): its pointers as written land outside it (before it, in 138 of 138 such
#: Sony notes of photo_index's sample) and no record says where it was, so no byte beside it is taken on a guess.
_CONTAINED = frozenset({"tiff-relative", "note-relative", "either", "own header", "no pointers",
                        "moved, the Canon footer says by how much"})
_SHAPES = {"date": re.compile(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d\Z", re.ASCII),
           "digits": re.compile(r"\d+\Z", re.ASCII)}


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Edited:
    body: bytes
    #: The block is longer than it was (only with grow=True): its segment must be rewritten, not patched.
    length_changed: bool


@dataclass(frozen=True)
class DoesNotFit:
    """The change is understood but needs `needed` bytes more than the block's slack: `edit(..., grow=True)` makes it."""
    needed: int

    @property
    def reason(self):
        return "The change needs %d bytes more than the EXIF block's slack holds." % self.needed


class _Refused(Exception):
    pass


def _overlaps(a, b, spans):
    return any(start < b and a < end for start, end, _owner in spans)


def _decoded(name, kind, entry, raw, order):
    if entry[1] not in _READ_TYPES[kind] or (kind == "short" and entry[2] != 1):
        raise _Refused("%s is stored as TIFF type %d, count %d: not a form TagPup reads." % (name, entry[1], entry[2]))
    if kind == "short":
        return struct.unpack(order + ("H" if entry[1] == 3 else "I"), raw[:2 if entry[1] == 3 else 4])[0]
    if kind == "ucs2":
        # ExifTool's reading: UCS-2 little-endian, an odd last byte dropped, up to the first NUL character.
        return raw[:len(raw) // 2 * 2].decode("utf-16-le", "surrogatepass").split("\0")[0]
    try:
        return raw.split(b"\0")[0].decode("utf-8")
    except UnicodeDecodeError:
        raise _Refused("%s holds bytes that are not UTF-8." % name) from None


class _Block:
    """An EXIF body read: its TIFF bytes and layout, the owned fields' entries and values, and whether bytes no IFD
    points at are free (`free`, a sentence saying why not when they are not)."""

    def __init__(self, body, limit):
        if len(body) > limit:
            raise _Refused("The EXIF segment is %d bytes, over the %d a segment holds." % (len(body), limit))
        if not body.startswith(containers.EXIF_HEAD):
            raise _Refused("The segment is not an EXIF segment.")
        self.tiff = tiff = body[len(containers.EXIF_HEAD):]
        self.layout = layout = containers.exif_tiff(tiff)
        if layout is None or "ifd0" not in layout.named:
            raise _Refused("The EXIF block's structure cannot be followed: an IFD or a value lies outside it, or an "
                           "entry is of no TIFF type.")
        if layout.unfollowed:
            raise _Refused("The EXIF block points at strips, tiles or SubIFDs, which its walk does not follow.")
        order = layout.order
        self.entries, self.values = {}, {}
        for name, (where, tag, kind) in FIELDS.items():
            ifd = layout.named.get(where)
            found = [entry for entry in ifd.entries if entry[0] == tag] if ifd else []
            if len(found) > 1:
                raise _Refused("%s is written twice." % name)
            if found:
                self.entries[name] = found[0]
                self.values[name] = _decoded(name, kind, found[0], containers.value_bytes(tiff, found[0]), order)
        owned = {("ifd", layout.named[where].offset) for where in _IFD_NAMES if where in layout.named}
        owned |= {(layout.named[FIELDS[name][0]].offset, FIELDS[name][1]) for name in self.entries}
        for start, end, owner in layout.spans:
            if owner in owned and (start < 8 or _overlaps(start, end, [s for s in layout.spans if s[2] != owner])):
                raise _Refused("An IFD TagPup edits, or an owned value, shares its bytes with another.")
        self.free = None
        exif = layout.named.get("exif")
        note = next((entry for entry in exif.entries if entry[0] == 0x927C), None) if exif else None
        if note is not None and containers.TIFF_TYPE_SIZES[note[1]] * note[2] > 4:
            canon = containers.make_word(tiff, next((e for e in layout.named["ifd0"].entries if e[0] == 0x010F), None))
            kind = containers.maker_note(tiff, note[4], containers.TIFF_TYPE_SIZES[note[1]] * note[2], order,
                                         canon=canon == "CANON")[0]
            if kind not in _CONTAINED:
                self.free = "The MakerNote may point outside itself (%s): no byte beside it is taken." % kind

    def held(self, name):
        value = self.values.get(name)
        return None if value == "" else value


def read(body, limit=LIMIT):
    """{name: value} of the owned fields the EXIF segment's `body` holds, or a Refusal."""
    try:
        return dict(_Block(body, limit).values)
    except _Refused as why:
        return Refusal(str(why))


def _wanted(name, value, order):
    """(the value `name` is to hold, normalised, None for cleared; its (type, count, bytes), None for cleared)."""
    kind = FIELDS[name][2]
    if kind == "short":
        if value is None:
            return None, None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
            raise _Refused("Orientation takes a whole number from 1 to 8.")
        return value, (3, 1, struct.pack(order + "H", value))
    if value is not None and not isinstance(value, str):
        raise _Refused("%s takes text." % name)
    if not value:
        return None, None
    if "\0" in value:
        raise _Refused("A value of %s holds a NUL, which ends a value in EXIF." % name)
    if kind in _SHAPES and not _SHAPES[kind].match(value):
        raise _Refused("%s takes %s." % (name, "YYYY:MM:DD HH:MM:SS" if kind == "date" else "digits"))
    try:
        data = value.encode("utf-16-le") + b"\0\0" if kind == "ucs2" else value.encode("utf-8") + b"\0"
    except UnicodeEncodeError:
        raise _Refused("A value of %s holds a lone surrogate, which no encoding carries." % name) from None
    return value, (_WRITE_TYPE[kind], len(data), data)


class _Room:
    """Where new bytes go: after the last byte any IFD points at, from an even offset, when the slack is zeros to the
    block's end; at the block's end when other bytes lie after it. `end` is how far the block must reach."""

    def __init__(self, block):
        self.block = block
        layout = block.layout
        self.next = layout.used if layout.slack or layout.used == layout.size else layout.size
        self.next += self.next & 1
        self.end = layout.size

    def taken(self, start, end):
        if self.block.free:
            raise _Refused(self.block.free)
        self.next = max(self.next, end + (end & 1))
        self.end = max(self.end, end)

    def take(self, size):
        at = self.next
        self.taken(at, at + size)
        return at


def _table(block, room, ifd, names, changes, writes, zeroed):
    """Lay out the IFD `ifd` holding `changes` ({name: (type, count, bytes) or None}): the table's bytes go into
    `writes`, an owned value's old bytes it no longer uses into `zeroed`. Values come second (`_values`)."""
    order, layout = block.layout.order, block.layout
    entries = list(ifd.entries)
    for name in names:
        tag = FIELDS[name][1]
        at = next((index for index, entry in enumerate(entries) if entry[0] == tag), None)
        if changes[name] is None:
            entries.pop(at)
        elif at is None:
            tags = [entry[0] for entry in ifd.entries]
            if tags != sorted(tags):
                raise _Refused("The %s IFD's entries are not in tag order: a new one has no place." % _IFD_NAMES[
                    FIELDS[name][0]])
            entries.insert(sum(1 for entry in entries if entry[0] < tag), (tag, None, None, None, None))
    end = ifd.offset + 2 + 12 * len(entries) + 4
    if end > ifd.end:
        if _overlaps(ifd.end, end, [span for span in layout.spans if span[2] != ("ifd", ifd.offset)]):
            raise _Refused("A new tag needs a new entry in the %s IFD, and data follows its table." % _IFD_NAMES[
                FIELDS[names[0]][0]])
        room.taken(ifd.end, end)
    elif end < ifd.end:
        zeroed.append((end, ifd.end))
    return entries, end


def _values(block, room, ifd, entries, names, changes, writes, zeroed):
    order, tiff = block.layout.order, block.tiff
    spans = {owner: (start, stop) for start, stop, owner in block.layout.spans}
    placed = {}
    for name in names:
        old = block.entries.get(name)
        span = spans.get((ifd.offset, FIELDS[name][1])) if old else None
        new = changes[name]
        if new is None:
            if span:
                zeroed.append(span)
            continue
        kind, count, data = new
        if len(data) <= 4:
            raw = data + bytes(4 - len(data))
            if span:
                zeroed.append(span)
        elif span and span[1] - span[0] >= len(data):
            raw = struct.pack(order + "I", span[0])
            writes.append((span[0], data + bytes(span[1] - span[0] - len(data))))
        else:
            if span:
                zeroed.append(span)
            at = room.take(len(data))
            writes.append((at, data))
            raw = struct.pack(order + "I", at)
        placed[FIELDS[name][1]] = struct.pack(order + "HHI", FIELDS[name][1], kind, count) + raw
    table = struct.pack(order + "H", len(entries))
    for entry in entries:
        table += placed.get(entry[0]) or tiff[_entry_at(ifd, entry):_entry_at(ifd, entry) + 12]
    writes.append((ifd.offset, table + struct.pack(order + "I", ifd.next)))


def _entry_at(ifd, entry):
    return ifd.offset + 2 + 12 * ifd.entries.index(entry)


def _changed_at(old, new):
    """The offsets at which `new` differs from `old` (zero bytes past its end)."""
    old = old + bytes(max(0, len(new) - len(old)))
    out, step = [], 256
    for start in range(0, len(new), step):
        if old[start:start + step] != new[start:start + step]:
            out += [at for at in range(start, min(start + step, len(new))) if old[at] != new[at]]
    return out


def _kept(before, after, names):
    """Raise unless `after` (a _Block) keeps every IFD, every entry and every value of `before` but the owned ones
    `names`, at their offsets, and every byte it changed is an edited table's, an owned value's, or zero."""
    old, new = before.layout, after.layout
    if [ifd.offset for ifd in old.ifds] != [ifd.offset for ifd in new.ifds] or set(old.named) != set(new.named):
        raise _Refused("The edited block's IFDs are not where they were.")
    edited = {old.named[FIELDS[name][0]].offset for name in names}
    tags = {FIELDS[name][1] for name in FIELDS}
    for was, now in zip(old.ifds, new.ifds):
        keep = (lambda entries: [e for e in entries if e[0] not in tags]) if was.offset in edited else list
        if keep(was.entries) != keep(now.entries) or was.next != now.next:
            raise _Refused("The edited block's entries are not the block's.")
    mine = {("ifd", at) for at in edited} | {(at, tag) for at in edited for tag in tags}
    for start, end, owner in old.spans:
        if owner not in mine and before.tiff[start:end] != after.tiff[start:end]:
            raise _Refused("The edited block changed bytes it does not own.")
    ours = [(start, end) for start, end, owner in new.spans if owner in mine]
    for at in _changed_at(before.tiff, after.tiff):
        if after.tiff[at] and not any(start <= at < end for start, end in ours):
            raise _Refused("The edited block changed bytes it does not own.")


def edit(body, changes, grow=False, limit=LIMIT):
    """`body` (an EXIF segment's, bytes) with `changes` ({name: value}) made: Edited, DoesNotFit or a Refusal."""
    try:
        unknown = sorted(set(changes) - set(FIELDS))
        if unknown:
            raise _Refused("%s is not an EXIF field TagPup owns." % unknown[0])
        before = _Block(body, limit)
        order = before.layout.order
        # A value the block holds already is not looked at: an Orientation of 0 (1 of 3,000 photo_index files),
        # given back as it was read, changes no byte.
        wanted = {name: _wanted(name, changes[name], order) for name in FIELDS
                  if name in changes and before.held(name) != (None if changes[name] == "" else changes[name])}
        moved = {name: new for name, (value, new) in wanted.items() if before.held(name) != value}
        if not moved:
            return Edited(body, False)
        room, writes, zeroed, tables = _Room(before), [], [], []
        for where in _IFD_NAMES:
            names = [name for name in moved if FIELDS[name][0] == where]
            if names:
                ifd = before.layout.named.get(where)
                if ifd is None:
                    raise _Refused("The EXIF block has no %s IFD to hold %s." % (_IFD_NAMES[where], names[0]))
                tables.append((ifd, names) + _table(before, room, ifd, names, moved, writes, zeroed))
        for ifd, names, entries, _end in tables:
            _values(before, room, ifd, entries, names, moved, writes, zeroed)
        if room.end > before.layout.size and not grow:
            return DoesNotFit(room.end - before.layout.size)
        tiff = bytearray(before.tiff + bytes(max(0, room.end - before.layout.size)))
        for start, end in zeroed:
            tiff[start:end] = bytes(end - start)
        for at, data in writes:
            tiff[at:at + len(data)] = data
        result = containers.EXIF_HEAD + bytes(tiff)
        if len(result) > limit:
            raise _Refused("The edited EXIF segment would be %d bytes, over %d." % (len(result), limit))
        after = _Block(result, limit)
        expected = {name: before.held(name) for name in FIELDS}
        expected.update({name: value for name, (value, _new) in wanted.items()})
        if {name: after.held(name) for name in FIELDS} != expected:
            raise _Refused("The edited EXIF block does not read back as written.")
        _kept(before, after, list(moved))
        return Edited(result, len(result) != len(body))
    except _Refused as why:
        return Refusal(str(why))

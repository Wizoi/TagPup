"""The layout of a video's boxes and a JPEG's metadata segments, read from a file already open: where each piece sits and
how long it is, so that an in-place write of the file's metadata knows where the XMP lives and how much room is beside it.

Reads only, and only headers: a MOV/MP4/M4V is walked by seeking from one box header to the next (a 10 GB clip costs a
few dozen small reads, never its media data); a JPEG is read up to its first scan, the picture itself never. It takes an
open binary file rather than a path, so it makes no look of its own: the caller opens the file and bounds the reading on a
share (`shares.bounded`). Whatever the bytes, `read` answers `Video`, `Jpeg` or `Unreadable`, never an exception: a file
cut short, a box that claims more than the file holds, or bytes that are no box at all end the walk as the layout's
`tail`, and a read that fails part-way is `Unreadable` with its reason.

    Video   the top-level boxes in order (moov and udta with their children), the XMP box wherever it is (a `uuid` box
            BE7ACFCB-97A9-42E8-9C71-999491E3AFAC at the top level, as ExifTool writes into an MP4 and as an append at
            the end leaves it; or `XMP_` inside moov's udta, as ExifTool writes into a .mov), the free/skip/wide boxes
            beside a box (room a write may take), moov before or after mdat, and moof (a fragmented file).
    Jpeg    the segments up to the first scan, each APP1 EXIF, APP1 XMP and APP13 Photoshop (IPTC) segment with its
            slack: the bytes a rewrite of the segment at its old length may use, as the in-place study defined them
            (DECISIONS 2026-11-02, "THE JPEG PATCH IS BUILT WITH THE VIDEOS"): XMP's whitespace between </x:xmpmeta>
            and the packet's end (one newline kept), EXIF's zero bytes after the last data its IFDs point at, IPTC's
            dummy 8BIM resource 0x2710 at the end; and bytes after the last 8BIM resource that are no resource
            (`stray`), which break ExifTool's next write and Windows' reader.
"""
import struct
from dataclasses import dataclass

#: The usertype of the `uuid` box that holds a video's XMP.
XMP_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")
#: Boxes that hold nothing and may be taken by a write beside them.
FREE_TYPES = frozenset({"free", "skip", "wide"})
#: Boxes whose children are walked, and which of those children are walked in turn: where ExifTool puts a .mov's XMP
#: (moov/udta/XMP_). No deeper: a box of garbage nested in itself is not followed down.
CONTAINERS = {"moov": frozenset({"udta"}), "udta": frozenset()}
#: What a MOV/MP4/M4V starts with: an ftyp box, or a skip box before it (2 of the 2,184 live .mov/.mp4/.m4v files,
#: counted 2026-11-02; no other first box among them).
FIRST_TYPES = frozenset({"ftyp", "skip"})
#: The 8BIM resource the in-place writer pads an IPTC segment with (no meaning to any reader).
PAD_RESOURCE = 0x2710

_XMP_HEAD = b"http://ns.adobe.com/xap/1.0/\0"
_XMP_EXTENSION_HEAD = b"http://ns.adobe.com/xmp/extension/\0"
_EXIF_HEAD = b"Exif\0\0"
_IPTC_HEAD = b"Photoshop 3.0\0"
_PACKET_END = b"<?xpacket end="
_XMPMETA_END = b"</x:xmpmeta>"
#: Bytes of each TIFF field type (EXIF): a type not here makes the EXIF slack unknown, taken as none.
_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
#: TIFF tags that point at another IFD: Exif, GPS, Interoperability.
_SUB_IFDS = (0x8769, 0x8825, 0xA005)


@dataclass(frozen=True)
class Unreadable:
    """Not a layout this reads, or a read that failed: `reason` says which, in a sentence."""
    reason: str


@dataclass(frozen=True)
class Tail:
    """Where the walk stopped before the end of the file: `reason` "truncated" (a box or segment claims more bytes than
    the file holds; `claimed` is how many) or "garbage" (bytes that are no box or segment header)."""
    offset: int
    reason: str
    claimed: int = 0
    type: str = ""


@dataclass(frozen=True)
class Box:
    """One ISO-BMFF box: `size` is the whole box, its header included; `header` is 8, 16 for a 64-bit size, and 16 more
    for a `uuid` box's usertype. `to_end` is a box stored with size 0 (it runs to the end of the file). `children` are
    walked only as CONTAINERS says; `broken` is a container whose children do not fit in it."""
    type: str
    offset: int
    size: int
    header: int
    uuid: bytes = None
    to_end: bool = False
    children: tuple = ()
    broken: bool = False

    @property
    def end(self):
        return self.offset + self.size

    @property
    def payload(self):
        """Where the box's content starts (after the usertype of a `uuid` box)."""
        return self.offset + self.header

    @property
    def is_xmp(self):
        return (self.type == "uuid" and self.uuid == XMP_UUID) or self.type == "XMP_"


@dataclass(frozen=True)
class Video:
    """A MOV/MP4/M4V's top-level boxes, in file order, and where their walk stopped short (`tail`, None at the end)."""
    file_size: int
    boxes: tuple
    tail: Tail = None

    def of_type(self, kind):
        return tuple(box for box in self.boxes if box.type == kind)

    @property
    def moov(self):
        """The first moov box, None for none."""
        found = self.of_type("moov")
        return found[0] if found else None

    @property
    def first_mdat(self):
        found = self.of_type("mdat")
        return found[0] if found else None

    @property
    def last_mdat(self):
        found = self.of_type("mdat")
        return found[-1] if found else None

    @property
    def moov_first(self):
        """True when moov comes before the first mdat, False after; None when either is missing."""
        if self.moov is None or self.first_mdat is None:
            return None
        return self.moov.offset < self.first_mdat.offset

    @property
    def fragmented(self):
        return bool(self.of_type("moof"))

    @property
    def xmp(self):
        """((path of box types, box), ...): every XMP box, top-level `uuid` and moov/udta/XMP_ alike, in file order."""
        return tuple(_xmp_boxes(self.boxes, ()))

    def slack_beside(self, box):
        """(free boxes just before `box`, free boxes just after it): the runs of free/skip/wide boxes that touch it among
        its siblings (the top level, or the container that holds it)."""
        siblings = _siblings_of(self.boxes, box)
        if siblings is None:
            return (), ()
        at = siblings.index(box)
        before, after = [], []
        for other in reversed(siblings[:at]):
            if other.type not in FREE_TYPES:
                break
            before.insert(0, other)
        for other in siblings[at + 1:]:
            if other.type not in FREE_TYPES:
                break
            after.append(other)
        return tuple(before), tuple(after)


@dataclass(frozen=True)
class Resource:
    """One 8BIM resource of a Photoshop (APP13) segment: `size` is the whole resource, header and padding included."""
    id: int
    offset: int
    size: int


@dataclass(frozen=True)
class Segment:
    """One JPEG marker segment: `offset` of its 0xFF, `size` from the marker to its end. `kind` is "exif", "xmp",
    "xmp-extension", "iptc", "sos" or "other". `slack` and `stray` as the module says; `resources` of an IPTC segment."""
    marker: int
    offset: int
    size: int
    kind: str
    slack: int = 0
    stray: int = 0
    resources: tuple = ()

    @property
    def end(self):
        return self.offset + self.size


@dataclass(frozen=True)
class Jpeg:
    """A JPEG's segments from its start to its first scan (`image_offset`, None when the file stops before one)."""
    file_size: int
    segments: tuple
    image_offset: int = None
    tail: Tail = None

    def of_kind(self, kind):
        return tuple(segment for segment in self.segments if segment.kind == kind)

    def _only(self, kind):
        found = self.of_kind(kind)
        return found[0] if len(found) == 1 else None

    @property
    def exif(self):
        """The EXIF segment; None for none, or for more than one (which one a reader takes is not this module's to say)."""
        return self._only("exif")

    @property
    def xmp(self):
        """The XMP segment; None for none, or for more than one."""
        return self._only("xmp")

    @property
    def iptc(self):
        """The Photoshop (IPTC) segment; None for none, or for more than one."""
        return self._only("iptc")


def read(handle):
    """The layout of the file open in `handle` (binary, seekable): `Video`, `Jpeg` or `Unreadable`. Never raises for
    what the bytes hold or for a read that fails."""
    try:
        file_size = handle.seek(0, 2)
        start = _read_at(handle, 0, 16)
        if start[:3] == b"\xff\xd8\xff":
            return _read_jpeg(handle, file_size)
        if len(start) >= 8 and start[4:8].decode("latin-1") in FIRST_TYPES:
            return _read_video(handle, file_size)
        return Unreadable("neither a JPEG nor a MOV/MP4/M4V: it starts with no JPEG marker and no ftyp or skip box")
    except OSError as problem:
        return Unreadable("the file could not be read: %s" % problem)


def _read_at(handle, offset, count):
    handle.seek(offset)
    return handle.read(count)


# --- video --------------------------------------------------------------------------------------------------------


def _box_type(raw):
    """The four characters of a box type, None when they are not printable (garbage, not a box). QuickTime's own types
    start with the copyright sign (0xA9)."""
    if len(raw) != 4 or not all(32 <= b < 127 or b == 0xA9 for b in raw):
        return None
    return raw.decode("latin-1")


def _header(handle, offset, end):
    """(type, size, header length, uuid, to_end) of the box at `offset`, `end` being where its parent ends; a str reason
    ("garbage" or "truncated") when there is no whole box header there, or (type, size...) with a size past `end` for
    the caller to call truncated."""
    want = min(32, end - offset)
    head = _read_at(handle, offset, want)
    if len(head) < want:
        return "truncated"      # the file holds fewer bytes than its size said: it is being cut or written
    if want < 8:
        return "garbage"
    size = int.from_bytes(head[:4], "big")
    kind = _box_type(head[4:8])
    if kind is None:
        return "garbage"
    header, to_end = 8, False
    if size == 1:
        if len(head) < 16:
            return "truncated"
        size, header = int.from_bytes(head[8:16], "big"), 16
    elif size == 0:
        size, to_end = end - offset, True
    uuid = None
    if kind == "uuid":
        if len(head) < header + 16:
            return "truncated"
        uuid = bytes(head[header:header + 16])
        header += 16
    if size < header:
        return "garbage"
    return kind, size, header, uuid, to_end


def _read_video(handle, file_size):
    boxes, tail, offset = [], None, 0
    while offset < file_size:
        found = _header(handle, offset, file_size)
        if isinstance(found, str):
            tail = Tail(offset, found)
            break
        kind, size, header, uuid, to_end = found
        if offset + size > file_size:
            tail = Tail(offset, "truncated", claimed=size, type=kind)
            break
        children, broken = (), False
        if kind in CONTAINERS:
            children, broken = _children(handle, offset + header, offset + size, CONTAINERS[kind])
        boxes.append(Box(kind, offset, size, header, uuid, to_end, children, broken))
        offset += size
    if not boxes:
        return Unreadable("a MOV/MP4/M4V whose first box is not whole")
    return Video(file_size, tuple(boxes), tail)


def _children(handle, start, end, walked):
    """(the boxes from `start` to `end`, the children of those of `walked` among them; True when one does not fit). Fewer than 8 bytes left, or a size of 0, is the end
    of the list (QuickTime ends a udta with four zero bytes)."""
    found, offset = [], start
    while end - offset >= 8:
        head = _read_at(handle, offset, 4)
        if head == b"\0\0\0\0":
            break
        header = _header(handle, offset, end)
        if isinstance(header, str):
            return tuple(found), True
        kind, size, length, uuid, to_end = header
        if offset + size > end:
            return tuple(found), True
        children, broken = (), False
        if kind in walked:
            children, broken = _children(handle, offset + length, offset + size, CONTAINERS[kind])
        found.append(Box(kind, offset, size, length, uuid, to_end, children, broken))
        offset += size
    return tuple(found), False


def _xmp_boxes(boxes, path):
    for box in boxes:
        here = path + (box.type,)
        if box.is_xmp:
            yield here, box
        yield from _xmp_boxes(box.children, here)


def _siblings_of(boxes, wanted):
    if wanted in boxes:
        return list(boxes)
    for box in boxes:
        found = _siblings_of(box.children, wanted)
        if found is not None:
            return found
    return None


# --- JPEG ---------------------------------------------------------------------------------------------------------


def _read_jpeg(handle, file_size):
    segments, offset = [], 2
    while offset < file_size:
        head = _read_at(handle, offset, 4)
        if len(head) < 2 or head[0] != 0xFF:
            return Jpeg(file_size, tuple(segments), None, Tail(offset, "garbage"))
        marker = head[1]
        if marker == 0xFF:          # fill byte before a marker
            offset += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:      # a marker with no length
            offset += 2
            continue
        if marker == 0xD9:          # the end of the image before any scan
            return Jpeg(file_size, tuple(segments), None, None)
        if len(head) < 4:
            return Jpeg(file_size, tuple(segments), None, Tail(offset, "truncated"))
        length = int.from_bytes(head[2:4], "big")
        if length < 2:
            return Jpeg(file_size, tuple(segments), None, Tail(offset, "garbage"))
        if offset + 2 + length > file_size:
            return Jpeg(file_size, tuple(segments), None, Tail(offset, "truncated", claimed=2 + length))
        if marker == 0xDA:
            segments.append(Segment(marker, offset, 2 + length, "sos"))
            return Jpeg(file_size, tuple(segments), offset, None)
        body = _read_at(handle, offset + 4, length - 2) if marker in (0xE1, 0xED) else b""
        if len(body) != (length - 2 if marker in (0xE1, 0xED) else 0):
            return Jpeg(file_size, tuple(segments), None, Tail(offset, "truncated", claimed=2 + length))
        segments.append(_segment(marker, offset, 2 + length, body))
        offset += 2 + length
    return Jpeg(file_size, tuple(segments), None, None)


def _segment(marker, offset, size, body):
    if marker == 0xE1 and body.startswith(_XMP_HEAD):
        return Segment(marker, offset, size, "xmp", slack=_xmp_slack(body[len(_XMP_HEAD):]))
    if marker == 0xE1 and body.startswith(_XMP_EXTENSION_HEAD):
        return Segment(marker, offset, size, "xmp-extension")
    if marker == 0xE1 and body.startswith(_EXIF_HEAD):
        return Segment(marker, offset, size, "exif", slack=_exif_slack(body[len(_EXIF_HEAD):]))
    if marker == 0xED and body.startswith(_IPTC_HEAD):
        start = offset + 4 + len(_IPTC_HEAD)
        resources, stray = _resources(body[len(_IPTC_HEAD):], start)
        slack = resources[-1].size if resources and resources[-1].id == PAD_RESOURCE else 0
        return Segment(marker, offset, size, "iptc", slack=slack, stray=stray, resources=resources)
    return Segment(marker, offset, size, "other")


def _xmp_slack(packet):
    """The whitespace between </x:xmpmeta> and <?xpacket end=, less the one newline a rewrite keeps; 0 when the packet
    has no such ending or something other than whitespace sits there."""
    end = packet.rfind(_PACKET_END)
    meta = packet.rfind(_XMPMETA_END, 0, end) if end >= 0 else -1
    if meta < 0:
        return 0
    between = packet[meta + len(_XMPMETA_END):end]
    if between.strip(b" \t\r\n"):
        return 0
    return max(len(between) - 1, 0)


def _exif_slack(tiff):
    """The zero bytes after the last byte EXIF's IFDs (IFD0, IFD1 and its thumbnail, Exif, GPS, Interoperability) point
    at; 0 when the structure cannot be followed or something not zero sits after it."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return 0
    order = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(order + "H", tiff[2:4])[0] != 42:
        return 0
    used = 8
    queue, seen = [struct.unpack(order + "I", tiff[4:8])[0]], set()
    while queue:
        at = queue.pop()
        if at == 0 or at in seen:
            continue
        seen.add(at)
        if at + 2 > len(tiff):
            return 0
        count = struct.unpack(order + "H", tiff[at:at + 2])[0]
        ifd_end = at + 2 + 12 * count + 4
        if ifd_end > len(tiff):
            return 0
        used = max(used, ifd_end)
        thumbnail = {}
        for entry in range(at + 2, at + 2 + 12 * count, 12):
            tag, kind, n = struct.unpack(order + "HHI", tiff[entry:entry + 8])
            if kind not in _TIFF_TYPE_SIZES:
                return 0
            value = struct.unpack(order + "I", tiff[entry + 8:entry + 12])[0]
            length = _TIFF_TYPE_SIZES[kind] * n
            if length > 4:
                if value + length > len(tiff):
                    return 0
                used = max(used, value + length)
            if tag in _SUB_IFDS:
                queue.append(value)
            elif tag in (0x0201, 0x0202):
                thumbnail[tag] = value
        if len(thumbnail) == 2:
            end = thumbnail[0x0201] + thumbnail[0x0202]
            if end > len(tiff):
                return 0
            used = max(used, end)
        queue.append(struct.unpack(order + "I", tiff[ifd_end - 4:ifd_end])[0])
    rest = tiff[used:]
    return len(rest) if not rest.strip(b"\0") else 0


def _resources(data, start):
    """(the 8BIM resources of a Photoshop segment's `data`, which starts at file offset `start`; how many bytes after
    the last whole one are not a resource)."""
    found, at = [], 0
    while len(data) - at >= 12 and data[at:at + 4] == b"8BIM":
        resource_id = int.from_bytes(data[at + 4:at + 6], "big")
        name = 1 + data[at + 6]
        name += name & 1
        size_at = at + 6 + name
        if size_at + 4 > len(data):
            break
        length = int.from_bytes(data[size_at:size_at + 4], "big")
        whole = 6 + name + 4 + length + (length & 1)
        if at + whole > len(data):
            break
        found.append(Resource(resource_id, start + at, whole))
        at += whole
    return tuple(found), len(data) - at

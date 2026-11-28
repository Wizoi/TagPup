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
            beside a box (room a write may take), moov before or after mdat, moof (a fragmented file), the clip's
            QuickTime title item (moov/udta/meta/ilst/\xa9nam, `title_item`) and its Microsoft Xtra box
            (moov/udta/Xtra, `xtra`), where Windows keeps the clip's Tags; and the creation time each of its
            movie, track and media headers holds (mvhd, moov/trak/tkhd, moov/trak/mdia/mdhd: `headers`).
    Jpeg    the segments up to the first scan, each APP1 EXIF, APP1 XMP and APP13 Photoshop (IPTC) segment with its
            slack: the bytes a rewrite of the segment at its old length may use, as the in-place study defined them
            (DECISIONS 2026-11-02, "THE JPEG PATCH IS BUILT WITH THE VIDEOS"): XMP's whitespace between </x:xmpmeta>
            and the packet's end (one newline kept), EXIF's zero bytes after the last data its IFDs point at, IPTC's
            dummy 8BIM resource (PAD_RESOURCE) at the end; and bytes after the last 8BIM resource that are no resource
            (`stray`), which break ExifTool's next write and Windows' reader.
"""
import struct
from dataclasses import dataclass, field

#: The usertype of the `uuid` box that holds a video's XMP.
XMP_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")
#: Boxes that hold nothing and may be taken by a write beside them.
FREE_TYPES = frozenset({"free", "skip", "wide"})
#: Boxes whose children are walked, and which of those children are walked in turn: where ExifTool puts a .mov's XMP
#: (moov/udta/XMP_), and where a clip's QuickTime title is (moov/udta/meta/ilst/\xa9nam: every one of the 2,018
#: photo_index clips holding one, counted 2026-11-08), and where a clip's track and media headers are (moov/trak/tkhd,
#: moov/trak/mdia/mdhd). No deeper: a box of garbage nested in itself is not followed down.
CONTAINERS = {"moov": frozenset({"udta", "trak"}), "udta": frozenset({"meta"}), "meta": frozenset({"ilst"}),
              "ilst": frozenset(), "trak": frozenset({"mdia"}), "mdia": frozenset()}
#: The boxes holding a clip's creation time (the movie's header, each track's and each media's): a full box, its one
#: version byte and three flag bytes, then its creation time in seconds since 1904-01-01, 4 bytes in version 0 and 8 in
#: version 1 (ISO/IEC 14496-12).
HEADER_TYPES = ("mvhd", "tkhd", "mdhd")
#: The bytes of a header's creation time by its version byte: the one place the rule is spelled (Box.width).
HEADER_WIDTH = {0: 4, 1: 8}
#: The box of a clip's QuickTime title (ExifTool's QuickTime:Title), an item of an ilst; QuickTime's older user data
#: keeps one directly in udta.
TITLE_ITEM = "\xa9nam"
#: The box Windows keeps a clip's Tags in (its WM/Category entry) and the other properties it writes: every one of the
#: 2,103 photo_index clips holding one holds one, directly in moov/udta (counted 2026-11-09); none holds two, none one
#: elsewhere.
XTRA = "Xtra"
#: The handler (its hdlr's) of a meta box whose ilst holds items by their four-letter names, TITLE_ITEM among them; a
#: 'mdta' meta's ilst holds numbered items its keys box names.
ITEM_HANDLER = "mdir"
#: The boxes a meta box written without a full box's version and flags (QuickTime's form) starts with; an MP4's meta
#: has the 4 bytes first. ExifTool tells the two forms so.
_META_CHILDREN = frozenset({"hdlr", "keys", "ilst", "mhdr"})
#: What a MOV/MP4/M4V starts with: an ftyp box, or a skip box before it (2 of the 2,184 live .mov/.mp4/.m4v files,
#: counted 2026-11-02; no other first box among them).
FIRST_TYPES = frozenset({"ftyp", "skip"})
#: The id of the 8BIM resource the in-place writer pads an IPTC segment with, and the only id read as that padding:
#: 0x0DAC (3500), in the gap Adobe's Photoshop File Formats Specification leaves undefined between Origin Path Info
#: (0x0BB8) and the Plug-In resources (0x0FA0), so no program writes it with a meaning. It was 0x2710 before it was
#: merged, which is Photoshop's Print Flags Information: 7 of 300 photo_index JPEGs hold a real one (a reviewer's count,
#: 2026-11-03), which the patch then took as its padding and wrote zeros over.
PAD_RESOURCE = 0x0DAC
#: The fewest bytes an 8BIM resource takes: its signature, id, an empty name and its length, no data.
LEAST_RESOURCE = 12
#: The 8BIM resource of a Photoshop segment that holds its IPTC-IIM records.
IPTC_RESOURCE = 0x0404
#: The longest body a JPEG segment holds: its length field, at most 65,535, counts its own two bytes.
MOST_BODY = 0xFFFF - 2
#: The fewest bytes of whitespace an XMP packet keeps between </x:xmpmeta> and <?xpacket end=: the one line break
#: ExifTool ends its padding with. The slack a packet gives, the padding an edit leaves and the patch's fit all count
#: from it (`xmp_packet.padding` lays the bytes).
XMP_PADDING_LEAST = 1

XMP_HEAD = b"http://ns.adobe.com/xap/1.0/\0"
_XMP_EXTENSION_HEAD = b"http://ns.adobe.com/xmp/extension/\0"
#: What an EXIF segment's body starts with, before its TIFF block.
EXIF_HEAD = b"Exif\0\0"
_IPTC_HEAD = b"Photoshop 3.0\0"
_PACKET_END = b"<?xpacket end="
_XMPMETA_END = b"</x:xmpmeta>"
#: Bytes of each TIFF field type (EXIF): a type not here makes the EXIF slack unknown, taken as none.
TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
#: TIFF tags that point at another IFD, by the name of the IFD each points at: Exif and GPS (from IFD0),
#: Interoperability (from Exif).
_SUB_IFDS = {0x8769: "exif", 0x8825: "gps", 0xA005: "interop"}
#: Tags that point at bytes the walk of an EXIF block does not follow: strips, tiles, SubIFDs.
_UNFOLLOWED = frozenset({0x0111, 0x0144, 0x014A})
#: Lengths of the vendor headers before a MakerNote's IFD (none: Canon; 6 "AOC"; 8 "OLYMP"; 12 "SONY DSC", "Panasonic",
#: "FUJIFILM", "OLYMPUS II"; 14 "Apple iOS"; 18 Nikon's type 3, whose TIFF header is found first).
NOTE_HEADERS = (0, 6, 8, 10, 12, 14, 18)


@dataclass(frozen=True)
class Refusal:
    """What the three block editors (`xmp_packet`, `exif_block`, `iptc_block`) answer for input they do not wholly
    understand or a change they will not make: a sentence, nothing changed."""
    reason: str


@dataclass(frozen=True)
class Edited:
    """What an editor answers for a change made: the new bytes of the packet, EXIF body or APP13 body, and whether the
    length is not what it was (the change did not fit, or `grow` was given): the segment must then be rewritten, not
    patched."""
    body: bytes
    length_changed: bool


@dataclass(frozen=True)
class Unreadable:
    """Not a layout this reads, or a read that failed: `reason` says which, in a sentence; `error`, the OSError of a read
    that failed (a share gone part-way), None for bytes that are no layout this reads."""
    reason: str
    error: object = field(default=None, compare=False)


@dataclass(frozen=True)
class Tail:
    """Where the walk stopped before the end of the file: `reason` "truncated" (a box or segment claims more bytes than
    the file holds; `claimed` is how many) or "garbage" (bytes that are no box or segment header). `blank`: a garbage
    tail of nothing but zero bytes, BLANK_MOST or fewer of them (the padding a Canon camera leaves after mdat; 9,556 in the
    owner's 2014 Leavenworth clip): no reader finds a box there, so a box written over it is found."""
    offset: int
    reason: str
    claimed: int = 0
    type: str = ""
    blank: bool = False

#: The most zero bytes after a clip's last box that are read to know they are all zero (a tail of more is garbage).
BLANK_MOST = 1 << 20


@dataclass(frozen=True)
class Box:
    """One ISO-BMFF box: `size` is the whole box, its header included; `header` is 8, 16 for a 64-bit size, and 16 more
    for a `uuid` box's usertype. `to_end` is a box stored with size 0 (it runs to the end of the file). `children` are
    walked only as CONTAINERS says; `broken` is a container whose children do not fit in it. `handler`, a meta box's
    handler type (ITEM_HANDLER), None for another box or a meta with no one hdlr. `version` and `created`, a header's
    (HEADER_TYPES) version byte and the creation time it stores, read under moov; None for another box, and `created`
    None for a header of another version or too short to hold one."""
    type: str
    offset: int
    size: int
    header: int
    uuid: bytes = None
    to_end: bool = False
    children: tuple = ()
    broken: bool = False
    handler: str = None
    version: int = None
    created: int = None

    @property
    def end(self):
        return self.offset + self.size

    @property
    def width(self):
        """The bytes of a header's creation time (HEADER_WIDTH); None for a box that is no header or of a version other
        than 0 and 1."""
        return HEADER_WIDTH.get(self.version)

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
    def fragmented(self):
        return bool(self.of_type("moof"))

    @property
    def xmp(self):
        """((path of box types, box), ...): every XMP box, top-level `uuid` and moov/udta/XMP_ alike, in file order."""
        return tuple(_xmp_boxes(self.boxes, ()))

    def in_udta(self, kind):
        """Every box of type `kind` directly in the first moov's udta, in file order."""
        moov = self.moov
        return tuple(child for udta in (moov.children if moov else ()) if udta.type == "udta"
                     for child in udta.children if child.type == kind)

    @property
    def titles(self):
        """Every QuickTime title item (TITLE_ITEM) under the first moov's udta, in file order: directly in it, or in the
        ilst of a meta in it, whatever the meta's handler."""
        listed = tuple(item for meta in self.in_udta("meta") for ilst in meta.children if ilst.type == "ilst"
                       for item in ilst.children if item.type == TITLE_ITEM)
        return tuple(sorted(self.in_udta(TITLE_ITEM) + listed, key=lambda box: box.offset))

    @property
    def item_list(self):
        """The one ilst of a moov/udta/meta whose handler is ITEM_HANDLER, it and its meta walked whole; None for none,
        for two, or for one whose boxes do not fit."""
        found = [(meta, ilst) for meta in self.in_udta("meta") if meta.handler == ITEM_HANDLER
                 for ilst in meta.children if ilst.type == "ilst"]
        if len(found) != 1 or found[0][0].broken or found[0][1].broken:
            return None
        return found[0][1]

    @property
    def title_item(self):
        """The clip's one QuickTime title item, in its item list (item_list); None for none, for two anywhere under
        moov/udta (one clip of photo_index holds two, 2026-11-08), or for one elsewhere (directly in udta)."""
        ilst, found = self.item_list, self.titles
        if ilst is None or len(found) != 1 or found[0] not in ilst.children:
            return None
        return found[0]

    @property
    def xtra(self):
        """The clip's one Microsoft Xtra box (XTRA), directly in moov/udta; None for none, or for two."""
        found = self.in_udta(XTRA)
        return found[0] if len(found) == 1 else None

    @property
    def headers(self):
        """Every header box (HEADER_TYPES) under the first moov, its tracks' and their media's, in file order."""
        moov = self.moov
        return tuple(_headers_in(moov.children if moov else ()))

    @property
    def headers_whole(self):
        """False when a track or its media box holds children that do not fit (`broken`): a header in it is missing from
        `headers`, and a write of every header would leave that one as it was."""
        return not any(track.broken or any(media.broken for media in track.children if media.type == "mdia")
                       for track in (self.moov.children if self.moov else ()) if track.type == "trak")

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
    """One 8BIM resource of a Photoshop (APP13) segment: `size` is the whole resource, header and padding included;
    its data is `length` bytes at file offset `data`."""
    id: int
    offset: int
    size: int
    data: int
    length: int


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
        return Unreadable("the file could not be read: %s" % problem, problem)


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


def _blank(handle, offset, file_size):
    """Is everything from `offset` to the end of the file zero bytes, a box header's worth (8: fewer is a file cut in a
    header) to BLANK_MOST of them?"""
    return 8 <= file_size - offset <= BLANK_MOST and not _read_at(handle, offset, file_size - offset).strip(b"\0")


def _read_video(handle, file_size):
    boxes, tail, offset = [], None, 0
    while offset < file_size:
        found = _header(handle, offset, file_size)
        if isinstance(found, str):
            tail = Tail(offset, found, blank=found == "garbage" and _blank(handle, offset, file_size))
            break
        kind, size, header, uuid, to_end = found
        if offset + size > file_size:
            tail = Tail(offset, "truncated", claimed=size, type=kind)
            break
        children, broken, handler = (), False, None
        if kind in CONTAINERS:
            children, broken, handler = _walked(handle, kind, offset + header, offset + size)
        boxes.append(Box(kind, offset, size, header, uuid, to_end, children, broken, handler))
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
        children, broken, handler = (), False, None
        if kind in walked:
            children, broken, handler = _walked(handle, kind, offset + length, offset + size)
        version, created = _created(handle, kind, offset + length, offset + size)
        found.append(Box(kind, offset, size, length, uuid, to_end, children, broken, handler, version, created))
        offset += size
    return tuple(found), False


def _created(handle, kind, start, end):
    """(the version byte, the creation time) of a header box (HEADER_TYPES) whose content runs from `start` to `end`;
    (None, None) for another box, the time None for a version other than 0 or 1 or a box too short to hold it."""
    if kind not in HEADER_TYPES:
        return None, None
    raw = _read_at(handle, start, min(12, end - start))
    if not raw:
        return None, None
    width = HEADER_WIDTH.get(raw[0])
    if width is None or len(raw) < 4 + width:
        return raw[0], None
    return raw[0], int.from_bytes(raw[4:4 + width], "big")


def _walked(handle, kind, start, end):
    """(the children of the container `kind` whose content runs from `start` to `end`, True when one does not fit, a
    meta's handler or None). A meta's children follow the 4 bytes of a full box's version and flags in an MP4, and its
    header at once in QuickTime's form: told by whether the bytes there begin a box a meta holds (_META_CHILDREN)."""
    if kind == "meta" and end - start >= 8 and _read_at(handle, start + 4, 4).decode("latin-1") not in _META_CHILDREN:
        start += 4
    children, broken = _children(handle, start, end, CONTAINERS[kind])
    handler = None
    hdlr = [child for child in children if child.type == "hdlr"] if kind == "meta" else []
    if len(hdlr) == 1 and hdlr[0].end - hdlr[0].payload >= 12:
        handler = _box_type(_read_at(handle, hdlr[0].payload + 8, 4))
    return children, broken, handler


def _headers_in(boxes):
    for box in boxes:
        if box.type in HEADER_TYPES:
            yield box
        yield from _headers_in(box.children)


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
    if marker == 0xE1 and body.startswith(XMP_HEAD):
        return Segment(marker, offset, size, "xmp", slack=_xmp_slack(body[len(XMP_HEAD):]))
    if marker == 0xE1 and body.startswith(_XMP_EXTENSION_HEAD):
        return Segment(marker, offset, size, "xmp-extension")
    if marker == 0xE1 and body.startswith(EXIF_HEAD):
        return Segment(marker, offset, size, "exif", slack=_exif_slack(body[len(EXIF_HEAD):]))
    if marker == 0xED and body.startswith(_IPTC_HEAD):
        start = offset + 4 + len(_IPTC_HEAD)
        resources, stray = _resources(body[len(_IPTC_HEAD):], start)
        slack = resources[-1].size if resources and resources[-1].id == PAD_RESOURCE else 0
        return Segment(marker, offset, size, "iptc", slack=slack, stray=stray, resources=resources)
    return Segment(marker, offset, size, "other")


def _xmp_slack(packet):
    """The whitespace between </x:xmpmeta> and <?xpacket end=, less XMP_PADDING_LEAST; 0 when the packet
    has no such ending or something other than whitespace sits there."""
    end = packet.rfind(_PACKET_END)
    meta = packet.rfind(_XMPMETA_END, 0, end) if end >= 0 else -1
    if meta < 0:
        return 0
    between = packet[meta + len(_XMPMETA_END):end]
    if between.strip(b" \t\r\n"):
        return 0
    return max(len(between) - XMP_PADDING_LEAST, 0)


def _exif_slack(tiff):
    """The zero bytes after the last byte EXIF's IFDs point at (`exif_tiff`); 0 when the structure cannot be followed."""
    found = exif_tiff(tiff)
    return found.slack if found else 0


@dataclass(frozen=True)
class Ifd:
    """One IFD of an EXIF block: its offset in the TIFF block, its entries as `ifd` gives them, the next IFD's offset."""
    offset: int
    entries: tuple
    next: int

    @property
    def end(self):
        return self.offset + 2 + 12 * len(self.entries) + 4


@dataclass(frozen=True)
class Tiff:
    """An EXIF block's TIFF structure (the bytes after "Exif\\0\\0"), walked. `order` is struct's "<" or ">"; `ifds` every
    IFD reached (IFD0, IFD1 and its thumbnail, Exif, GPS, Interoperability, and any next IFD of theirs); `named` those
    reached by their usual pointer, by "ifd0", "ifd1", "exif", "gps", "interop"; `spans` ((start, end, owner), ...) every
    byte range an IFD points at, its owner ("ifd", offset) for an IFD's own table, (IFD offset, tag) for a value of more
    than four bytes, ("thumbnail", IFD offset) for IFD1's JPEG; `used` the end of the last. `slack` the zero bytes after
    it (0 when anything else lies there). `unfollowed`: an entry points at bytes the walk does not follow (strips,
    tiles, SubIFDs, an entry of type IFD)."""
    order: str
    size: int
    ifds: tuple
    named: dict
    spans: tuple
    used: int
    slack: int
    unfollowed: bool


def tiff_head(tiff):
    """(struct's byte order, "<" or ">"; the offset of IFD0) of an EXIF block's TIFF bytes, or None when they start with
    no TIFF header. The one reading of that header."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    order = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(order + "H", tiff[2:4])[0] != 42:
        return None
    return order, struct.unpack(order + "I", tiff[4:8])[0]


def exif_tiff(tiff):
    """The `Tiff` of an EXIF block's TIFF bytes, or None when its structure cannot be followed: no TIFF header, an IFD
    or a value that does not fit in the block, an entry of no TIFF type. The one walk of an EXIF block's IFDs."""
    head = tiff_head(tiff)
    if head is None:
        return None
    order, first = head
    ifds, named, spans, unfollowed = [], {}, [], False
    queue, seen = [(first, "ifd0")], set()
    while queue:
        at, name = queue.pop()
        if at == 0 or at in seen:
            continue
        seen.add(at)
        found = ifd(tiff, at, order)
        if found is None or found[1] is None:
            return None
        here = Ifd(at, tuple(found[0]), found[1])
        ifds.append(here)
        if name:
            named.setdefault(name, here)
        spans.append((at, here.end, ("ifd", at)))
        thumbnail = {}
        for tag, kind, n, _raw, value in here.entries:
            if kind not in TIFF_TYPE_SIZES:
                return None
            unfollowed = unfollowed or kind == 13 or tag in _UNFOLLOWED
            length = TIFF_TYPE_SIZES[kind] * n
            if length > 4:
                if value + length > len(tiff):
                    return None
                spans.append((value, value + length, (at, tag)))
            if tag in _SUB_IFDS:
                queue.append((value, _SUB_IFDS[tag] if name == ("exif" if tag == 0xA005 else "ifd0") else None))
            elif tag in (0x0201, 0x0202):
                thumbnail[tag] = value
        if len(thumbnail) == 2:
            end = thumbnail[0x0201] + thumbnail[0x0202]
            if end > len(tiff):
                return None
            spans.append((thumbnail[0x0201], end, ("thumbnail", at)))
        queue.append((here.next, "ifd1" if name == "ifd0" else None))
    used = max([8] + [end for _start, end, _owner in spans])
    rest = tiff[used:]
    return Tiff(order, len(tiff), tuple(ifds), named, tuple(spans), used, 0 if rest.strip(b"\0") else len(rest),
                unfollowed)


def value_bytes(tiff, entry):
    """The bytes of the value of an IFD `entry` (as `ifd` gives it) of `tiff`: in the entry, or where it points."""
    _tag, kind, n, raw, at = entry
    length = TIFF_TYPE_SIZES[kind] * n
    return raw[:length] if length <= 4 else tiff[at:at + length]


def make_word(tiff, entry):
    """The first word of EXIF Make, upper case, of its IFD0 `entry` (None for none): "" when it is no text."""
    if entry is None or entry[1] != 2:
        return ""
    words = value_bytes(tiff, entry).split(b"\0")[0].decode("latin-1").strip().upper().split()
    return words[0] if words else ""


def _note_ifd(note, at, order):
    """(the (size, offset) of each entry of the IFD at `at` of a MakerNote whose value is outside its entry, how many
    entries were skipped as of no TIFF type or a count no note holds), or None when the bytes there are no plausible IFD."""
    found = ifd(note, at, order)
    if found is None or not 1 <= len(found[0]) <= 512:
        return None
    pointers, bad = [], 0
    for _tag, kind, n, _raw, value in found[0]:
        if kind not in TIFF_TYPE_SIZES or n > 1 << 24:
            bad += 1
        elif TIFF_TYPE_SIZES[kind] * n > 4:
            pointers.append((TIFF_TYPE_SIZES[kind] * n, value))
    return None if 2 * bad >= len(found[0]) else (pointers, bad)


def canon_footer(note, start, order):
    """How far a Canon MakerNote at `start` was moved from where its footer says it was written, or None when its last
    8 bytes are no footer in the note's byte `order`."""
    if note[-8:-4] != (b"II*\0" if order == "<" else b"MM\0*"):
        return None
    return start - struct.unpack(order + "I", note[-4:])[0]


def maker_note(tiff, start, length, order, canon=False):
    """(how a MakerNote at `start` of `tiff`, `length` bytes, points at its values; how many of its entries were skipped;
    the shift its Canon footer records, None without one). `canon`: the EXIF's Make is Canon's.

    Told by its bytes, not its maker. Its first IFD is found after a vendor header of a length in NOTE_HEADERS, and each
    entry whose value is larger than four bytes points somewhere: "tiff-relative" when every pointer lands inside the
    note counted from the TIFF header (the note breaks if it is moved; Canon, Sony, Panasonic), "note-relative" when
    every pointer lands inside it counted from its own start (Apple, Fujifilm, Olympus' newer form), "own header" for a
    note carrying a TIFF header of its own (Nikon's type 3), "either" when both fit, "no pointers" when every value fits
    in its entry. An entry of no TIFF type, or of a count no note holds, is skipped and counted (Sony notes rewritten in
    the other byte order carry four; ExifTool reads past them too), and the note is told by its good entries; a note
    with no more good entries than bad is no IFD. A note moved by a program that did not fix its pointers is told only by
    what records its old place, a Canon note's footer (its byte order and its old offset from the TIFF header, the last
    8 bytes, as Canon cameras write it and ExifTool reads it): "moved, the Canon footer says by how much" (or "... wrong"
    when that shift does not land every pointer inside it). A note with no footer whose pointers fit inside it only once
    shifted by one amount is "moved, shift unknown". The Exif IFD's OffsetSchema (0xEA1D) is not trusted for the shift
    (it differed from the footer's in 1,224 of 1,239 photo_index notes). "outside" when no shift fits, "not an IFD" when
    no header length gives one, "cut short" when the note does not fit in the block."""
    note = tiff[start:start + length]
    if len(note) < length or length < 8:
        return "cut short", 0, None
    if note[10:12] in (b"II", b"MM") and note[12:14] in (b"*\0", b"\0*"):
        return "own header", 0, None
    for skip in NOTE_HEADERS:
        for each in (order, "<" if order == ">" else ">"):
            found = _note_ifd(note, skip, each)
            if found is None:
                continue
            pointers, bad = found
            footer = canon_footer(note, start, each) if canon else None
            if not pointers:
                return "no pointers", bad, footer
            if footer:
                return ("moved, the Canon footer says by how much" if all(
                    start <= at + footer and at + footer + size <= start + length for size, at in pointers)
                    else "moved, the Canon footer wrong"), bad, footer
            tiff_relative = all(start <= at and at + size <= start + length for size, at in pointers)
            note_relative = all(at + size <= length for size, at in pointers)
            if tiff_relative or note_relative:
                return ("either" if tiff_relative and note_relative else "tiff-relative" if tiff_relative
                        else "note-relative"), bad, footer
            if max(at + size for size, at in pointers) - min(at for _size, at in pointers) > length:
                return "outside", bad, footer
            return "moved, shift unknown", bad, footer
    return "not an IFD", 0, None


def ifd(tiff, at, order):
    """(the entries of the TIFF IFD at offset `at` of `tiff`, each (tag, type, count, its four value bytes, those read as
    an offset); the offset of the next IFD, None when the bytes stop before it), or None when its entries do not fit in
    `tiff`. `order` is struct's "<" or ">". The one reading of an IFD's layout; what a type means is the caller's."""
    if at + 2 > len(tiff):
        return None
    count = struct.unpack(order + "H", tiff[at:at + 2])[0]
    end = at + 2 + 12 * count
    if end > len(tiff):
        return None
    entries = []
    for entry in range(at + 2, end, 12):
        tag, kind, n = struct.unpack(order + "HHI", tiff[entry:entry + 8])
        raw = tiff[entry + 8:entry + 12]
        entries.append((tag, kind, n, raw, struct.unpack(order + "I", raw)[0]))
    next_ifd = struct.unpack(order + "I", tiff[end:end + 4])[0] if end + 4 <= len(tiff) else None
    return entries, next_ifd


def photoshop_resources(body):
    """(the 8BIM resources of an APP13 segment's `body` -- what follows its marker and length -- each at its offset in
    `body`; how many bytes after the last whole one are no resource), or None for a body that is not Photoshop 3.0's."""
    if not body.startswith(_IPTC_HEAD):
        return None
    return _resources(body[len(_IPTC_HEAD):], len(_IPTC_HEAD))


def resource(resource_id, data, name=b"\0\0"):
    """The bytes of an 8BIM resource: `name` its Pascal string as held (even in length; empty by default), `data` padded
    to an even length."""
    return (b"8BIM" + resource_id.to_bytes(2, "big") + name + len(data).to_bytes(4, "big") + data
            + bytes(len(data) & 1))


def pad_resource(size):
    """The dummy resource (PAD_RESOURCE) of `size` bytes, its data zeros: `size` is even and LEAST_RESOURCE or more."""
    return resource(PAD_RESOURCE, bytes(size - LEAST_RESOURCE))


@dataclass(frozen=True)
class IptcRecord:
    """One IPTC-IIM dataset of an 0x0404 resource's data: its record and dataset numbers, where its tag starts and where
    its value starts and ends. `extended` is a value whose length is held in an extended (long) length field."""
    record: int
    dataset: int
    offset: int
    start: int
    end: int
    extended: bool = False


def iptc_records(block):
    """(the IPTC-IIM datasets of `block`, an 0x0404 resource's data, in order; the offset where their walk stopped: the
    block's length, or the first byte that does not begin a whole dataset)."""
    found, at = [], 0
    while at + 5 <= len(block) and block[at] == 0x1C:
        start, length = at + 5, int.from_bytes(block[at + 3:at + 5], "big")
        extended = bool(length & 0x8000)
        if extended:
            width = length & 0x7FFF
            start, length = start + width, int.from_bytes(block[at + 5:at + 5 + width], "big")
        if start + length > len(block):
            break
        found.append(IptcRecord(block[at + 1], block[at + 2], at, start, start + length, extended))
        at = start + length
    return found, at


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
        found.append(Resource(resource_id, start + at, whole, start + size_at + 4, length))
        at += whole
    return tuple(found), len(data) - at

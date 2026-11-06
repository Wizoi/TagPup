"""Where a clip's new XMP packet goes in the file as it is: the byte ranges that patch a MOV/MP4/M4V in place, or None
when the clip needs the rewrite (docs/PLAN_OWN_METADATA.md, M7; DECISIONS 2026-11-02, "VIDEO IS MEDIA TAGPUP MANAGES" 3:
overwrite the XMP slot, use an adjacent free box, or append at the end).

Pure: the clip's layout (`containers.Video`, read by in_place.patch_clip from the file it holds open) and the new
packet's bytes in, a `Patch` or None out; no file is opened here and nothing is written. The packet is made by
segment_edits.new_packet (the XMP editor's one caller); this module only places it. "The rewrite's", below, is a clip
TagPup does not write until the whole-file clip rewrite, a later merge (owner, 2026-11-05: Q2, Q4): no ExifTool
rewrite of a clip is made meanwhile.

Where the packet goes, in this order (live counts of the 2,184 MOV/MP4/M4V in the folders the three libraries hold,
counted read-only 2026-11-05):
  1. Into the clip's one XMP box -- a top-level `uuid` box (80 live, each after moov; none is an MP4 as ExifTool
     writes it: 41 are Adobe Premiere/Media Encoder wrapped, 39 a bare 368-byte self-closing Description, and
     their properties are attributes, which xmp_packet.edit reads and edits where they stand (79 of the 80; the 80th is
     over the 65,536-byte read cap and is refused); a box this module appends is the other source) or moov/udta/XMP_ (a .mov as ExifTool writes it; none live, made by a rewrite that falls
     to ExifTool) -- with the free/skip/wide boxes straight after it among its siblings ("the run"; 31 of the 80 have
     one, none has one before it): the new box is written at the old one's place and what is left of the run becomes
     one `free` box after it (only its header is written: a free box's content is never read). Fewer than 8 bytes left
     over cannot make a box, so the packet takes them as whitespace before its `<?xpacket end`. The run covers the same
     bytes before and after, so no other box moves: moov keeps its size, mdat its offset, and the chunk offsets in
     stco/co64 stay true.
  2. A top-level XMP box whose run reaches the end of the file (39 live) grows there: the file is longer, nothing moves.
  3. A top-level XMP box that does not fit and cannot grow is made a `free` box (its four type bytes, and its old packet
     blanked with spaces: a free box's content is never read, but it would stay readable) and the packet is appended at
     the end of the file in a new uuid box. A .mov's moov/udta/XMP_ that does not fit is the rewrite's: to
     grow it is to grow udta and moov, which moves mdat when moov comes first; to move it out to a top-level box is to
     take it from the place Adobe's specification gives a QuickTime file.
  4. A clip with no XMP (2,104 live) gets a uuid box appended at the end of the file: what ExifTool writes into an MP4,
     and what ExifTool 13.59 and ffmpeg read back from an MP4 with moov first or last and from a .mov
     (tests/test_a_clip_patched_in_place.py).

None, the rewrite's, for: no moov, two moov, a moov or udta whose children do not fit in it, a fragmented clip (moof),
two XMP boxes (which one a reader takes is not this module's to guess), any XMP box whose run would leave a free box of
4 GB or more (a 32-bit size cannot say it; 0 of the 2,184 clips: 3 are over 4 GB, none holds XMP), an XMP box
holding more than 64 KB (1 live; the old bytes are blanked, and the journal keeps what they were, so they are bounded),
a packet that needs an append where the last box has size 0 ("to the end of the file": it would
take the appended box into itself; none live), and a clip whose walk stops before its end (`Video.tail`): a box that
claims more than the file holds (it may still be being copied) or bytes that are no box, where an appended box would
lie past what a reader walks and an XMP box before them is in a file no two readers need agree on. 2 live clips have a
tail, one of each, and neither holds XMP.

A `Patch`'s last range may start at the file's end (an append) or run past it (a box that grows there): `size` is the
file's size once written. Its ranges are in file order and do not overlap.
"""
from dataclasses import dataclass

from tagpup.files import containers

#: How a patch places the packet (the module's 1 to 4).
IN_PLACE, GROWN, APPENDED = "in place", "grown at the end", "appended"
#: The header of a box with a 32-bit size; the largest size it can say.
_HEADER = 8
_MOST = 2 ** 32 - 1
#: The most an old packet may hold for its bytes to be blanked (an XMP packet is at most 64 KB): what a patch keeps of
#: the old bytes is bounded by this and a few headers, far under the journal's cap (file_journal.PATCH_CAP, 1 MB).
OLD_MOST = 1 << 16
_PACKET_END = b"<?xpacket end="


@dataclass(frozen=True)
class Patch:
    """The writes that put a new XMP packet into a clip: `ranges` ((offset, bytes), ...), `size` the file's size after
    them, `how` IN_PLACE, GROWN or APPENDED."""
    ranges: tuple
    size: int
    how: str


def patch(layout, packet):
    """The `Patch` that puts `packet` (bytes, the whole new XMP packet) into the clip laid out as `layout`
    (containers.Video), or None when the clip needs the rewrite (the module says when)."""
    if not _understood(layout):
        return None
    found = layout.xmp
    if len(found) > 1:
        return None
    if not found:
        return _appended(layout, packet, ())
    (where, box), = found
    after = layout.slack_beside(box)[1]
    end = after[-1].end if after else box.end
    written = _box(box, packet)
    if box.end - box.payload > OLD_MOST or end - box.offset - len(written) > _MOST:
        return None
    placed = _placed(box, packet, end)
    if placed is not None:
        return Patch(placed, layout.file_size, IN_PLACE)
    if len(where) > 1:
        return None         # moov/udta/XMP_: the rewrite's (the module, 3)
    if end == layout.file_size and box.offset + len(written) > end:
        return Patch(((box.offset, written),), box.offset + len(written), GROWN)
    return _appended(layout, packet, ((box.offset + 4, b"free"), (box.payload, b" " * (box.end - box.payload))))


def _understood(layout):
    """Is `layout` a clip this module may patch: walked whole to its end, one moov with its children whole, no
    fragments?"""
    if not isinstance(layout, containers.Video) or layout.fragmented or layout.tail is not None:
        return False
    return len(layout.of_type("moov")) == 1 and not layout.moov.broken and not any(
        child.broken for child in layout.moov.children)


def _box(box, packet):
    """The bytes of an XMP box of `box`'s kind (a uuid box, or a .mov's XMP_) holding `packet`, with a 32-bit size."""
    usertype = b"uuid" + containers.XMP_UUID if box is None or box.type == "uuid" else b"XMP_"
    return (_HEADER + len(usertype) - 4 + len(packet)).to_bytes(4, "big") + usertype + packet


def _placed(box, packet, end):
    """The ranges that write the XMP box holding `packet` over `box`, the bytes up to `end` being it and the free boxes
    after it (the run), and the rest of the run made one free box; None when it does not fit there."""
    at, written = box.offset, _box(box, packet)
    left = end - at - len(written)
    if left < 0:
        return None
    if 0 < left < _HEADER:
        widened = _widened(packet, left)
        return None if widened is None else ((at, _box(box, widened)),)
    if left == 0:
        return ((at, written),)
    # The free box's header, and the old packet's bytes still in its content (a shorter packet leaves its tail there)
    # blanked: the old packet is at most OLD_MOST, so this is at most that and a header.
    free_at = at + len(written)
    return (at, written), (free_at, left.to_bytes(4, "big") + b"free" + b" " * max(0, box.end - free_at - _HEADER))


def _widened(packet, extra):
    """`packet` with `extra` spaces more of whitespace before its `<?xpacket end`, or None for a packet with no line
    break there (no wrapper: it has no padding to widen)."""
    end = packet.rfind(_PACKET_END)
    if end < 1 or packet[end - 1:end] != b"\n":
        return None
    return packet[:end - 1] + b" " * extra + packet[end - 1:]


def _appended(layout, packet, retired):
    """The Patch that appends a uuid box holding `packet` at the end of the file, after the `retired` ranges (an old XMP
    box made free); None when the last box runs to the end of the file (size 0)."""
    if layout.boxes[-1].to_end:
        return None
    written = _box(None, packet)
    return Patch(retired + ((layout.file_size, written),), layout.file_size + len(written), APPENDED)

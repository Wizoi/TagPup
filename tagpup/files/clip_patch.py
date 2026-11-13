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
     their properties are attributes, which xmp_packet.edit reads and edits where they stand; a box this module appends
     is the other source. Of any size: 1 live box is over 64 KB, a Premiere packet of 206,986 bytes, none over 256 KB,
     counted 2026-11-12) or moov/udta/XMP_ (a .mov as ExifTool writes it; none live, made by a rewrite that falls
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
4 GB or more (a 32-bit size cannot say it; 0 of the 2,184 clips: 3 are over 4 GB, none holds XMP), a packet that needs an append where the last box has size 0 ("to the end of the file": it would
take the appended box into itself; none live), and a clip whose walk stops before its end (`Video.tail`): a box that
claims more than the file holds (it may still be being copied) or bytes that are no box, where an appended box would
lie past what a reader walks and an XMP box before them is in a file no two readers need agree on. 2 live clips have a
tail, one of each, and neither holds XMP.

No size of an XMP box is refused here (owner, 2026-11-12): the one bound is the journal's, which keeps a patch's record
and the bytes its ranges held (file_journal.PATCH_CAP, 1 MiB) and refuses a larger patch before a byte is written.

A clip's QuickTime title (DECISIONS 2026-11-08): a save that leaves a clip with no XMP:Title frees its title item too
(containers.Video.title_item, moov/udta/meta/ilst/\xa9nam: every QuickTime Title of the 2,018 photo_index clips holding
one, counted 2026-11-08), its four type bytes made `free` (`title=""`); no size or offset moves, and ExifTool, ffprobe
and Windows read no title. An undo gives it back (`title`, its text) by the same four bytes, only to the one `free` box
of the item list holding exactly one UTF-8 `data` atom of that text (`freed_titles`, `holds_title`); a file rewritten
meanwhile has none, and is the rewrite's. None, the rewrite's, for a clear of a clip holding two title items (1 live)
or one outside the item list (directly in udta, QuickTime's older form), and a give-back to a clip holding a title item
already. A clear is asked only of a clip whose QuickTime Title ExifTool read (tagpup.services.tagging._title_written),
so a clear finding no title item is the rewrite's too: the title is in a form this does not walk (an 'mdta' keys meta,
no live clip, 2026-11-08), refused before a byte of the XMP is written rather than read back still titled after it.
A title typed over is not written here: the QuickTime copy stays as it was.

A clip's Windows Tags (DECISIONS 2026-11-09): the one-time move of a clip's Windows Tags into its XMP frees its Xtra
box (containers.Video.xtra, moov/udta/Xtra: each of the 2,103 photo_index clips holding one, 2,093 of them holding a
WM/Category, counted 2026-11-09) in the same Patch that writes the XMP, its four type bytes made `free`
(`category=()`); the Xtra's other Windows properties go with it, and nothing moves. An undo gives it back (`category`,
the texts it held) by the same four bytes, only to the one `free` box directly in udta whose bytes are an Xtra's
entries holding those texts as a read gives them (fields.same_read: trimmed, in any order, a number or a boolean as
ExifTool answers it; the journal keeps what ExifTool read; `xtra_boxes`, `windows_tags`), while the clip holds no Xtra. Each box is read
whole, its header with it, so the four bytes the patch writes over are the ones read as `Xtra` (or `free`) on the open
file. None, the rewrite's, for a clear of a clip holding no Xtra, two, or one holding no WM/Category text (10 live
clips hold an empty Xtra; the move asks only of a clip whose Category ExifTool read), and a give-back to a clip
holding an Xtra again (Windows wrote its Tags since).

A `Patch`'s last range may start at the file's end (an append) or run past it (a box that grows there): `size` is the
file's size once written. Its ranges are in file order and do not overlap.
"""
import struct
from dataclasses import dataclass

from tagpup.core import fields
from tagpup.files import containers

#: How a patch places the packet (the module's 1 to 4).
IN_PLACE, GROWN, APPENDED = "in place", "grown at the end", "appended"
#: The header of a box with a 32-bit size; the largest size it can say.
_HEADER = 8
_MOST = 2 ** 32 - 1
_PACKET_END = b"<?xpacket end="
#: The most a patch reads of a box it frees or gives back, to know what it holds: a free box of the item list (a title)
#: or of udta (an Xtra), and the Xtra a clear frees (the largest live Xtra is 1,417 bytes, 2026-11-09).
FREED_MOST = 1 << 16
#: A title item's data atom, before its text: its type, UTF-8 (1), as every live title's is (2026-11-08).
_UTF8 = b"\0\0\0\1"
#: The Xtra entry of a clip's Windows Tags; each of its values is UTF-16LE text ended by a NUL (type 8: all 7,293 values
#: of the 2,093 clips holding one, counted 2026-11-09).
CATEGORY = b"WM/Category"


@dataclass(frozen=True)
class Patch:
    """The writes that put a new XMP packet into a clip: `ranges` ((offset, bytes), ...), `size` the file's size after
    them, `how` IN_PLACE, GROWN or APPENDED."""
    ranges: tuple
    size: int
    how: str


def patch(layout, packet, title=None, freed=(), category=None, xtra_read=()):
    """The `Patch` that puts `packet` (bytes, the whole new XMP packet; None, the XMP as it is) into the clip laid out as
    `layout` (containers.Video), and, with `title` given, frees its QuickTime title ("") or gives it back (its text) to
    the one of the `freed` ((box, its bytes after its header), ...: freed_titles' boxes, read) that holds it; with
    `category` given, frees its Xtra (()) or gives it back (its Windows Tags, texts) to the one of `xtra_read` ((box,
    its bytes, header and all), ...: xtra_boxes(layout, category), read) that holds them; None when the clip needs the
    rewrite (the module says when). A Patch with no ranges is nothing to write."""
    if not _understood(layout):
        return None
    titled = () if title is None else _title_ranges(layout, title, freed)
    tagged = () if category is None else _xtra_ranges(category, xtra_read)
    placed = Patch((), layout.file_size, IN_PLACE) if packet is None else _xmp_patch(layout, packet)
    if titled is None or tagged is None or placed is None:
        return None
    return Patch(tuple(sorted(placed.ranges + titled + tagged)), placed.size, placed.how)


def freed_titles(layout):
    """The boxes an undo may give the clip's QuickTime title back to: each `free` box of its item list
    (containers.Video.item_list) of at most FREED_MOST, while it holds no title item; () otherwise."""
    ilst = layout.item_list
    if ilst is None or layout.titles:
        return ()
    return tuple(box for box in ilst.children if box.type == "free" and box.end - box.payload <= FREED_MOST)


def xtra_boxes(layout, category):
    """The boxes in_place reads whole for a write of the clip's Windows Tags (patch's `category`), each of at most
    FREED_MOST: for a clear (()), its one Xtra (containers.Video.xtra); for an undo giving texts back, each `free` box
    directly in its udta, while it holds no Xtra; () for None."""
    if category is None or (category and layout.in_udta(containers.XTRA)):
        return ()
    found = layout.in_udta("free") if category else (layout.xtra,) if layout.xtra is not None else ()
    return tuple(box for box in found if box.size <= FREED_MOST)


def windows_tags(raw, kind, header):
    """The Windows Tags `raw` holds, a box's bytes from its header on (`header` long), when its type is `kind` (b"Xtra",
    b"free") and its content is an Xtra's entries, whole: the texts of its WM/Category values, as the journal keeps a
    field's (fields.field_values), [] for none; None for a box of another type or bytes that are no Xtra's."""
    if raw[4:8] != kind:
        return None
    content, at, texts = raw[header:], 0, []
    try:
        while at < len(content):
            size, named = struct.unpack_from(">II", content, at)
            end, (count,) = at + size, struct.unpack_from(">I", content, at + 8 + named)
            value = at + 12 + named
            for _ in range(count):
                length = struct.unpack_from(">I", content, value)[0]
                if content[at + 8:at + 8 + named] == CATEGORY:
                    texts.append(content[value + 6:value + length].decode("utf-16-le", "replace").rstrip("\0"))
                value += max(length, 6)
            if value != end or end > len(content):
                return None
            at = end
    except struct.error:
        return None
    return fields.field_values(texts)


def holds_title(content, text):
    """Does `content`, a free box's bytes after its header, hold exactly one data atom of UTF-8 text `text`: the title
    item a clear freed, as it left it?"""
    value = text.encode("utf-8")
    return (len(content) == 16 + len(value) and content[:4] == len(content).to_bytes(4, "big")
            and content[4:8] == b"data" and content[8:12] == _UTF8 and content[16:] == value)


def _title_ranges(layout, title, freed):
    """The ranges that free the clip's title item (`title` "", containers.Video.title_item) or give `title` back to the
    one of `freed` holding it; None, the rewrite's, otherwise (the module)."""
    if not title:
        box = layout.title_item
        return None if box is None else ((box.offset + 4, b"free"),)
    return _retyped(freed, lambda box, content: holds_title(content, title), containers.TITLE_ITEM.encode("latin-1"))


def _xtra_ranges(category, read):
    """The range that frees the clip's Xtra (`category` (); `read`, its one Xtra, read) when it holds Windows Tags, or
    gives `category`'s texts back to the one of `read` (free boxes of udta, read) holding them as a read gives them
    (_holds_tags); None, the rewrite's, otherwise (the module)."""
    if category:
        return _retyped(read, lambda box, raw: _holds_tags(windows_tags(raw, b"free", box.header), category),
                        containers.XTRA.encode("latin-1"))
    return _retyped(read, lambda box, raw: windows_tags(raw, containers.XTRA.encode("latin-1"), box.header), b"free")


def _retyped(read, holds, written):
    """The one mechanism of a box freed or given back, whatever its kind: the range that writes `written` (the four type
    bytes) over the type of the one box of `read` ((box, bytes read), ...) for which `holds(box, bytes)` is true; None,
    the rewrite's, for none or for two (the module)."""
    found = [box for box, raw in read if holds(box, raw)]
    return ((found[0].offset + 4, written),) if len(found) == 1 else None


def _holds_tags(texts, category):
    """Are `texts` (windows_tags of a free box; None, no Xtra's) the Windows Tags `category` (the journal's, as ExifTool
    read them) as a read gives them (fields.same_read)? Matched by list equality, a text the parse and the journal
    spell apart left the undo unmatched, and the file marked a conflict (#2802)."""
    return texts is not None and fields.same_read(fields.CLIP_CATEGORY, texts, category)


def _xmp_patch(layout, packet):
    """The Patch that puts `packet` into the clip's XMP (patch, without its title), or None."""
    if not layout.xmp:
        return _appended(layout, packet, ())
    box = xmp_box(layout)
    if box is None:
        return None
    (where, _), = layout.xmp
    after = layout.slack_beside(box)[1]
    end = after[-1].end if after else box.end
    written = _box(box, packet)
    if end - box.offset - len(written) > _MOST:
        return None
    placed = _placed(box, packet, end)
    if placed is not None:
        return Patch(placed, layout.file_size, IN_PLACE)
    if len(where) > 1:
        return None         # moov/udta/XMP_: the rewrite's (the module, 3)
    if end == layout.file_size and box.offset + len(written) > end:
        return Patch(((box.offset, written),), box.offset + len(written), GROWN)
    return _appended(layout, packet, ((box.offset + 4, b"free"), (box.payload, b" " * (box.end - box.payload))))


def xmp_box(layout):
    """The one XMP box of the clip laid out as `layout` (containers.Video) whose packet a patch reads and replaces; None
    when it holds none or two (the module). The one statement of which box that is, for patch and for in_place, which
    reads its packet."""
    if len(layout.xmp) != 1:
        return None
    (_, box), = layout.xmp
    return box


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
    # blanked: at most the old packet and a header, which the journal keeps with the record (file_journal.PATCH_CAP).
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

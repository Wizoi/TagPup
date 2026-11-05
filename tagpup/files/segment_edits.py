"""A journaled write's field values made into new bytes for a JPEG header's three metadata segments (docs/PLAN_OWN_METADATA.md, M4).

Pure: a header's bytes and layout in, bytes out; no file, nothing written. The one caller of the three block editors
(xmp_packet, exif_block, iptc_block): each field goes where `fields.EDITED_AS` says, as the editor names it.

`new_header(layout, header, values)` is (the header with only its edited segments' bodies changed, the fields left
stale), or None when the write is not a patch -- a field the own writer does not write, a value it cannot spell, a
segment an editor refuses or that the change does not fit, a change for a segment the photo lacks -- and the caller
writes the file whole, as ExifTool does, nothing written here. Every marker and length byte is the header's; a segment
never grows (no `grow`).

What it writes (DECISIONS 2026-11-03, A KEYWORD OR CAPTION SAVE WRITES THE XMP COPY ONLY):
- the keyword group (fields.keyword_fields) is written to XMP alone while the value of XMP:Subject is not empty, the
  caption group (caption_fields) while XMP:Description is not; the group's other copies in `values`
  (fields.XMP_ONLY) are left as they were and answered as `stale`, for the caller to record as `fields.as_patched` says.
  When the XMP value is empty -- the last keyword taken off, the caption cleared -- the group is not XMP-only: XMP
  holding none, the reader would take IPTC's copy and the value removed would come back (#2433), so every copy of the
  group is written, a patch when they all fit, else None;
- dates and orientation in every copy named (they always fit); EXIF:Orientation also in an XMP tiff:Orientation the
  packet holds, as ExifTool's write of "Orientation" does.

`values` is the journal's: {field: [texts]}, each field's whole new value. A keyword write's is the planner's whole
set, read from the file's XMP and IPTC copies (tagging.keywords_planner), so XMP is given every keyword even of a photo
whose keywords were in IPTC alone; this module never infers a set or reads one copy into another.

How a value is spelled: a Bag the list; a text its one text (more than one is not a patch); Orientation a whole
number; an EXIF date as ExifTool reads it, `YYYY:MM:DD HH:MM:SS`, which the editor alone takes (a fraction or a zone
is not a patch); an XMP date in ISO 8601, `YYYY-MM-DDTHH:MM:SS`, the fraction and zone the value has kept and none
added. Each text as a write sends it (fields.as_written).
"""
import logging
import re

from tagpup.core import fields
from tagpup.files import containers, exif_block, iptc_block, xmp_packet

log = logging.getLogger(__name__)

#: kind: (its editor, the bytes of a segment's body before what the editor is given).
_EDITORS = {"exif": (exif_block, b""), "iptc": (iptc_block, b""), "xmp": (xmp_packet, containers.XMP_HEAD)}
_BAGS = frozenset(name for name, (_uri, _local, form) in xmp_packet.PROPERTIES.items() if form == "Bag") | {"IPTC:Keywords"}
_ORIENTATIONS = frozenset({"Orientation", "tiff:Orientation"})
_XMP_DATES = frozenset({"exif:DateTimeOriginal", "xmp:CreateDate"})
#: A date as ExifTool reads one, XMP's included: its fraction and zone, when it has them, kept.
_READ_DATE = re.compile(r"(\d{4}):(\d\d):(\d\d) (\d\d:\d\d:\d\d)(\.\d+)?([+-]\d\d:\d\d|Z)?\Z", re.ASCII)


class _NotAPatch(Exception):
    pass


def stale_of(values):
    """The fields of `values` a patch leaves as they were: each XMP-only group's other copies, while its XMP value is
    not empty (fields.XMP_ONLY)."""
    return tuple(field for xmp_field, leaves in fields.XMP_ONLY.items() if fields.field_values(values.get(xmp_field))
                 for field in leaves if field in values)


def _iso(text):
    found = _READ_DATE.match(text)
    if not found:
        raise _NotAPatch("The date %r is not one the own writer spells in XMP." % text)
    year, month, day, clock, fraction, zone = found.groups()
    return "%s-%s-%sT%s%s%s" % (year, month, day, clock, fraction or "", zone or "")


def _value(field, name, texts):
    """The value the editor is given for `name` from the journal's `texts` of `field`."""
    texts = fields.as_written(field, texts)
    if name in _BAGS:
        return texts
    if len(texts) > 1:
        raise _NotAPatch("%s is given %d values; it holds one." % (field, len(texts)))
    if not texts:
        return None
    if name in _ORIENTATIONS:
        try:
            return int(texts[0])
        except ValueError:
            raise _NotAPatch("The orientation %r is not a number." % texts[0]) from None
    return _iso(texts[0]) if name in _XMP_DATES else texts[0]


def _changes(values):
    """{kind: {name: (field, texts, only where held)}} of `values`."""
    unknown = sorted(set(values) - set(fields.EDITED_AS))
    if unknown:
        raise _NotAPatch("%s is not a field the own writer writes." % unknown[0])
    by_kind = {}
    for field, texts in values.items():
        for at, (kind, name) in enumerate(fields.EDITED_AS[field]):
            by_kind.setdefault(kind, {})[name] = (field, texts, at > 0)
    return by_kind


def _edited(kind, segment, header, wanted):
    """The new body of `segment` with `wanted` made (None: nothing to change), or raises _NotAPatch."""
    editor, head = _EDITORS[kind]
    if segment is None:
        if any(fields.field_values(texts) for _field, texts, followed in wanted.values() if not followed):
            raise _NotAPatch("The photo has no %s segment for the change: making one is a rewrite." % kind)
        return None
    start = segment.offset + 4 + len(head)
    body = header[start:segment.end]
    if any(followed for _field, _texts, followed in wanted.values()):
        held = editor.read(body)
        if isinstance(held, containers.Refusal):
            raise _NotAPatch(held.reason)
        wanted = {name: change for name, change in wanted.items() if not change[2] or name in held}
    changes = {name: _value(field, name, texts) for name, (field, texts, _followed) in wanted.items()}
    if not changes:
        return None
    result = editor.edit(body, changes)
    if not isinstance(result, containers.Edited):
        raise _NotAPatch(result.reason)
    if result.length_changed:
        raise _NotAPatch("The change does not fit the %s segment." % kind)
    return start, segment.end, result.body


def new_header(layout, header, values):
    """(`header` with `values` written into its segments, the fields of `values` left stale), or None for a write that
    is not a patch. `layout` is the containers.Jpeg of `header`, the bytes up to its first scan's header's end."""
    try:
        stale = stale_of(values)
        by_kind = _changes({field: texts for field, texts in values.items() if field not in stale})
        written = bytearray(header)
        for kind, wanted in by_kind.items():
            (segment,) = layout.of_kind(kind) or (None,)
            edited = _edited(kind, segment, header, wanted)
            if edited is not None:
                start, end, body = edited
                written[start:end] = body
        assert len(written) == len(header), "a patch changed the header's length"
        return bytes(written), stale
    except _NotAPatch as why:
        log.info("Not a patch in place: %s", why)
        return None

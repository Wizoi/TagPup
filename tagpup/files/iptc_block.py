"""The datasets TagPup owns in a JPEG's IPTC block, read and edited (docs/PLAN_OWN_METADATA.md, M3).

Pure: an APP13 segment's body in -- what follows its marker and length, "Photoshop 3.0" and its 8BIM resources -- and
bytes out; no file. A JPEG with two APP13 segments (Photoshop splits a large block across them) is its caller's to refuse:
`containers.Jpeg.iptc` is None for it.

`read(body)` is {field: value} of the owned datasets the block holds (DATASETS; Keywords a list of str, the rest a str),
each read in the block's character set with a trailing run of NULs off. `edit(body, changes)` sets each named field
(None, '' or [] clears it), each value cut as a write cuts it (`fields.as_written`), and answers `containers.Edited`: only the 0x0404
resource's data changes, and in it only the owned datasets (a dataset held is rewritten where its first one was, a new
one goes at the end of its record), the character set (1:90) and the version dataset (1:00, 2:00) of a record that
holds a dataset and lacks its version, so 1:00 only beside another dataset of record 1 (a 1:90), where ExifTool adds
1:00 to every block it writes (no reader reads the versions; metadata_check.KNOWN); every other dataset and every
other resource, the IPTCDigest (0x0425) among them (DECISIONS 2026-11-03: left stale, as ExifTool leaves it), keep
their bytes. Zero bytes after the last dataset are
dropped, as ExifTool drops them, so an edit is ExifTool's byte for byte where it lays the datasets as ExifTool does. The body
keeps its length when the change fits, the dummy resource (`containers.PAD_RESOURCE`) at its end taking up or giving
the room as the in-place patch lays it; `length_changed` says it did not fit, for the caller to choose a patch or a
rewrite. A value equal to what the block holds changes no byte.

The character set: no 1:90 is cp1252, as ExifTool reads it; a block without one stays so while every owned value fits in
cp1252, and else is given 1:90 UTF-8 (ESC % G) and its owned values are written in UTF-8. Either answers `containers.Refusal`, a
sentence, for a block it does not wholly understand: it never repairs one, and an edit is read back before it is
answered. What it refuses, with the counts behind each rule: docs/domains/files.md, "The IPTC block editor".
"""
import re

from tagpup.core import fields
from tagpup.files import containers

#: field: (record, dataset), in dataset order, the order ExifTool adds new datasets in.
DATASETS = {"IPTC:ObjectName": (2, 5), "IPTC:Keywords": (2, 25), "IPTC:Caption-Abstract": (2, 120)}
_LIST = "IPTC:Keywords"
CHARSET = (1, 90)
UTF8 = b"\x1b%G"
#: The version dataset of each record and its value (4), added to a record that holds a dataset and lacks one.
VERSIONS = {1: ((1, 0), b"\0\x04"), 2: ((2, 0), b"\0\x04")}
LIMIT = containers.MOST_BODY
#: What a value may not hold: NUL and the C0 controls but tab, line feed and carriage return.
_NOT_TEXT = re.compile("[%s-%s%s%s%s-%s]" % (chr(0), chr(8), chr(11), chr(12), chr(14), chr(31)))


class _Refused(Exception):
    pass


def _record(key, value):
    return b"\x1c" + bytes(key) + len(value).to_bytes(2, "big") + value


class _Block:
    """A body read: its resources, the 0x0404 resource (None), its datasets' (key, bytes) in order, the character set
    and the owned values."""

    def __init__(self, body, limit):
        if len(body) > limit:
            raise _Refused("The APP13 segment is %d bytes, over the %d it can hold." % (len(body), limit))
        found = containers.photoshop_resources(body)
        if found is None:
            raise _Refused("The APP13 segment is not a Photoshop 3.0 one.")
        self.body, (self.resources, stray) = body, found
        if stray:
            raise _Refused("%d bytes after the last 8BIM resource are no resource." % stray)
        blocks = [each for each in self.resources if each.id == containers.IPTC_RESOURCE]
        if len(blocks) > 1:
            raise _Refused("The segment holds two IPTC (0x0404) resources.")
        self.iptc = blocks[0] if blocks else None
        data = body[self.iptc.data:self.iptc.data + self.iptc.length] if self.iptc else b""
        records, stop = containers.iptc_records(data)
        if data[stop:].strip(b"\0") or any(each.extended for each in records):
            raise _Refused("The IPTC block holds bytes at %d that are no whole dataset (a length that runs past the "
                           "block, an extended length, or stray bytes)." % stop)
        self.records = [((each.record, each.dataset), data[each.offset:each.end]) for each in records]
        marks = [whole[5:] for key, whole in self.records if key == CHARSET]
        if len(marks) > 1 or (marks and marks[0] != UTF8):
            raise _Refused("The IPTC block's character set (1:90) is not one UTF-8 marker.")
        self.charset = "utf-8" if marks else "cp1252"
        self.values = {}
        for field, key in DATASETS.items():
            texts = [self._text(field, whole[5:]) for held, whole in self.records if held == key]
            if len(texts) > 1 and field != _LIST:
                raise _Refused("%s is written twice." % field)
            if texts:
                self.values[field] = texts if field == _LIST else texts[0]

    def _text(self, field, value):
        value = value.rstrip(b"\0")
        try:
            if b"\0" in value:
                raise ValueError
            return value.decode(self.charset)
        except ValueError:
            raise _Refused("A value of %s is not %s text." % (field, self.charset)) from None

    def kept(self, skip):
        """(every resource but 0x0404 and a last pad, every dataset not owned and not of `skip`): what an edit keeps."""
        last = self.resources[-1] if self.resources else None
        resources = tuple(self.body[each.offset:each.offset + each.size] for each in self.resources
                          if each.id != containers.IPTC_RESOURCE
                          and not (each is last and each.id == containers.PAD_RESOURCE))
        owned = set(DATASETS.values()) | skip
        return resources, tuple(whole for key, whole in self.records if key not in owned)


def read(body, limit=LIMIT):
    """{field: value} of the owned datasets `body` (an APP13 segment's body) holds, or a Refusal."""
    try:
        return dict(_Block(body, limit).values)
    except _Refused as why:
        return containers.Refusal(str(why))


def _written(field, value):
    """`value` of `field` as a write sends it (`fields.as_written`: blanks out, each cut), a repeat out; None for none."""
    texts = list(dict.fromkeys(fields.as_written(field, value)))
    if field == _LIST:
        return texts or None
    return texts[0] if texts else None


def _wanted(field, value):
    """The value `field` is to hold (`_written`); raises _Refused for one it cannot hold."""
    if field == _LIST:
        if value is not None and not (isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)):
            raise _Refused("%s takes a list of text." % field)
    elif value is not None and not isinstance(value, str):
        raise _Refused("%s takes text." % field)
    value = _written(field, value)
    if any(_NOT_TEXT.search(text) for text in ([value] if isinstance(value, str) else value or [])):
        raise _Refused("A value of %s holds a NUL or a control character." % field)
    for text in ([value] if isinstance(value, str) else value or []):
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise _Refused("A value of %s holds a lone surrogate, which no encoding carries." % field) from None
    return value


def _place(records, number):
    """Where a dataset of record `number` the block does not hold goes: after the last of a record up to its own."""
    return max((at + 1 for at, (key, _) in enumerate(records) if key[0] <= number), default=0)


def _rebuilt(before, moved, charset):
    """(the 0x0404 data that holds `moved`, in `charset`; the keys of the datasets it adds besides the owned ones)."""
    records, added = list(before.records), set()
    if charset != before.charset:
        records.insert(_place(records, 1), (CHARSET, _record(CHARSET, UTF8)))
        added.add(CHARSET)
    for field, value in moved.items():
        key = DATASETS[field]
        new = [(key, _record(key, text.encode(charset))) for text in ([] if value is None else
                                                                       value if field == _LIST else [value])]
        held = [at for at, (each, _) in enumerate(records) if each == key]
        if held:
            records = [each for each in records if each[0] != key]
            records[held[0]:held[0]] = new
        else:
            at = _place(records, 2)
            records[at:at] = new
    for number, (key, value) in VERSIONS.items():
        if any(each[0] == number for each, _ in records) and not any(each == key for each, _ in records):
            records.insert(_place(records, number), (key, _record(key, value)))
            added.add(key)
    return b"".join(whole for _, whole in records), added


def _laid(before, data):
    """The body `before` with 0x0404 holding `data`, and the dummy resource after the last taking up the room left."""
    body, kept = before.body, []
    last = before.resources[-1] if before.resources else None
    for each in before.resources:
        if each is before.iptc:
            name = body[each.offset + 6:each.data - 4]
            kept.append(containers.resource(containers.IPTC_RESOURCE, data, name))
        elif not (each is last and each.id == containers.PAD_RESOURCE):
            kept.append(body[each.offset:each.offset + each.size])
    if before.iptc is None:
        kept.append(containers.resource(containers.IPTC_RESOURCE, data))
    core = body[:before.resources[0].offset if before.resources else len(body)] + b"".join(kept)
    room = len(body) - len(core)
    return core + containers.pad_resource(room) if room >= containers.LEAST_RESOURCE else core


def edit(body, changes, limit=LIMIT):
    """`body` (an APP13 segment's body) with `changes` ({field: value}) made: Edited, or a Refusal saying why not."""
    try:
        unknown = sorted(set(changes) - set(DATASETS))
        if unknown:
            raise _Refused("%s is not a field the IPTC block editor owns." % unknown[0])
        before = _Block(body, limit)
        wanted = {field: _wanted(field, changes[field]) for field in DATASETS if field in changes}
        moved = {field: value for field, value in wanted.items() if _written(field, before.values.get(field)) != value}
        if not moved:
            return containers.Edited(body, False)
        expected = {field: value for field, value in {**before.values, **moved}.items() if value is not None}
        charset = before.charset
        if charset == "cp1252" and not _encodes(expected.values(), charset):
            charset = "utf-8"
            if any(max(whole[5:], default=0) >= 0x80 for key, whole in before.records
                   if key not in {DATASETS[field] for field in moved}):
                raise _Refused("The block holds text outside ASCII with no character set: giving it UTF-8 would change "
                               "how that text reads.")
        data, added = _rebuilt(before, moved, charset)
        result = _laid(before, data)
        if len(result) > limit:
            raise _Refused("The edited APP13 segment would be %d bytes, over %d." % (len(result), limit))
        after = _Block(result, limit)
        if after.values != expected or after.charset != charset or after.kept(added) != before.kept(added):
            raise _Refused("The edited IPTC block does not read back as written.")
        return containers.Edited(result, len(result) != len(body))
    except _Refused as why:
        return containers.Refusal(str(why))


def _encodes(values, charset):
    try:
        for value in values:
            for text in (value if isinstance(value, list) else [value]):
                text.encode(charset)
        return True
    except UnicodeEncodeError:
        return False

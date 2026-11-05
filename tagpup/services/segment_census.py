"""The census of what a library's JPEGs hold in their metadata segments, read-only, counts only: what TagPup's own
writer of a JPEG's EXIF, XMP and IPTC must meet (docs/PLAN_OWN_METADATA.md, M1; the counts in docs/domains/files.md).

Of a sample of the library's JPEGs (SAMPLE, drawn at random with a seed, so a second run reads the same photos), each
file's bytes up to its first scan are read (tagpup.files.containers lays them out), and its last TAIL bytes; never the
picture. Every read is bounded (tagpup.files.shares.bounded): a share that does not answer is `not_answered` and the census
goes on; a file not there is `gone` (a "not found" on a share that does not answer is not_answered, shares.stamp_of); a
file that cannot be opened is `unreadable`; one that is no JPEG, or stops before its scan, is counted as such. Nothing is
written: the library is opened read-only (db.readonly_uri), a photo only to read. The answer holds no path, no name, no
tag and no value of a file (the photos are of real people, many of them minors): only counts, a camera maker's bucket
(VENDORS) and the order of the segments by kind.

A MakerNote's offsets are told apart by `containers.maker_note`, which the EXIF block editor reads them by too (its
classes, in its docstring); the Exif IFD's OffsetSchema (0xEA1D) is only counted against a Canon footer.
"""
import collections
import datetime
import hashlib
import os
import random
import re
import statistics
import struct
import time
import xml.etree.ElementTree as ElementTree
from contextlib import closing

from tagpup.core.result import Refused
from tagpup.files import containers, images, shares
from tagpup.services import roots as roots_service
from tagpup.store import db, inspection

#: JPEGs read of a library, at most; SEED makes the draw repeat.
SAMPLE = 3000
SEED = 20261103
#: How many of the first reads are timed, as M6 asks how long a header read takes.
TIMED = 50
#: Bytes read from the end of a file, to see what follows the picture's end (EOI).
TAIL = 64
JPEG_EXTENSIONS = frozenset(ext for ext, mime in images.PHOTO_TYPES.items() if mime == "image/jpeg")

#: The XMP properties TagPup writes: each counted when it occurs twice, or in attribute form.
TARGETS = ("dc:subject", "dc:description", "dc:title", "exif:DateTimeOriginal", "xmp:CreateDate", "tiff:Orientation",
           "xmpMM:DocumentID")
#: XMP marks counted when the packet holds them.
XMP_MARKS = {"MP:RegionInfo": b"MP:RegionInfo", "mwg-rs:Regions": b"mwg-rs:Regions", "xmpMM:History": b"xmpMM:History",
             "digiKam:TagsList": b"digiKam:TagsList", "lr:hierarchicalSubject": b"lr:hierarchicalSubject",
             "MicrosoftPhoto namespace": b"ns.microsoft.com/photo/1.0", "xmpNote:HasExtendedXMP": b"xmpNote:HasExtendedXMP"}
#: x:xmptk, by what it starts with or holds: the bucket counted.
TOOLKITS = (("ExifTool", "Image::ExifTool"), ("Adobe", "Adobe XMP"), ("WLPG", "Microsoft"), ("WLPG", "Windows"))
#: Camera makers by the first word of EXIF Make, upper case; any other Make is "other" (no Make string is printed).
VENDORS = {"CANON": "Canon", "NIKON": "Nikon", "SONY": "Sony", "OLYMPUS": "Olympus", "OM": "Olympus",
           "PANASONIC": "Panasonic", "FUJIFILM": "Fujifilm", "APPLE": "Apple", "SAMSUNG": "Samsung", "GOOGLE": "Google",
           "HUAWEI": "Huawei", "MOTOROLA": "Motorola", "LGE": "LG", "LG": "LG", "EASTMAN": "Kodak", "KODAK": "Kodak",
           "PENTAX": "Pentax", "RICOH": "Pentax", "HTC": "HTC", "XIAOMI": "Xiaomi", "ONEPLUS": "OnePlus",
           "LEICA": "Leica", "CASIO": "Casio", "MINOLTA": "Minolta", "KONICA": "Minolta", "NOKIA": "Nokia",
           "MICROSOFT": "Microsoft", "HP": "HP", "HEWLETT-PACKARD": "HP", "BLACKBERRY": "BlackBerry"}
#: The histogram of a segment's slack, each bucket below its bound in bytes.
SLACK = (("0", 1), ("1-63", 64), ("64-511", 512), ("512-2047", 2048))
#: The histogram of an XMP packet's size (what phase 6's bake asked of the files).
XMP_SIZES = (("<16K", 16 * 1024), ("<48K", 48 * 1024))
_ABOUT = re.compile(rb"rdf:about\s*=\s*([\"'])(.*?)\1", re.S)
_TOOLKIT = re.compile(rb"x:xmptk\s*=\s*([\"'])(.*?)\1", re.S)
_BEGIN = re.compile(rb"<\?xpacket\s+begin\s*=\s*([\"'])(.*?)\1", re.S)
_APP_NAMES = ((0xE0, b"JFIF\0", "JFIF"), (0xE0, b"JFXX\0", "JFXX"), (0xE2, b"ICC_PROFILE\0", "ICC"),
              (0xE2, b"MPF\0", "MPF"), (0xEE, b"Adobe", "Adobe"))


def _bucket(size, bounds=SLACK, over="2048+"):
    return next((name for name, bound in bounds if size < bound), over)


def _how_many(n):
    return "0" if n == 0 else "1" if n == 1 else ">1"


def _read(path):
    """(the layout, the bytes up to the end of the first scan's header, the last TAIL bytes) of the file at `path`."""
    with open(path, "rb") as handle:
        layout = containers.read(handle)
        if not isinstance(layout, containers.Jpeg) or not layout.segments:
            return layout, b"", b""
        handle.seek(0)
        header = handle.read(layout.segments[-1].end)
        handle.seek(max(0, layout.file_size - TAIL))
        return layout, header, handle.read(TAIL)


def _looked(path):
    """("read", (layout, header, tail)) | ("not_answered" | "gone" | "unreadable", None), never raising."""
    state, found = shares.bounded(path, lambda: _read(path), shares.READ_WAIT)
    if state == "ok":
        return "read", found
    if state == "away":
        return "not_answered", None
    if isinstance(found, (FileNotFoundError, NotADirectoryError)):
        stamp = shares.stamp_of(path)
        return ("gone" if stamp is None else "not_answered" if stamp is shares.UNANSWERED else "unreadable"), None
    return "unreadable", None


def _label(segment, body):
    if segment.kind in ("exif", "xmp", "xmp-extension", "iptc"):
        return segment.kind.upper()
    for marker, head, name in _APP_NAMES:
        if segment.marker == marker and body.startswith(head):
            return name
    if 0xE0 <= segment.marker <= 0xEF:
        return "APP%d" % (segment.marker - 0xE0)
    return "COM" if segment.marker == 0xFE else "tables"


def _shape(layout, header, tail, counts, makers, toolkits, orders):
    """Count what one JPEG's segments hold into the census's counters."""
    labels, every = [], set()
    for segment in layout.segments:
        if segment.kind == "sos":
            continue
        body = header[segment.offset + 4:segment.end]
        label = _label(segment, body)
        every.add(label)
        if not labels or labels[-1] != label:
            labels.append(label)
        if segment.kind == "exif":
            counts["EXIF slack " + _bucket(segment.slack)] += 1
            _exif(body[6:], counts, makers)
        elif segment.kind == "xmp":
            counts["XMP slack " + _bucket(segment.slack)] += 1
            counts["XMP size " + _bucket(len(body) - len(b"http://ns.adobe.com/xap/1.0/\0"), XMP_SIZES, "48K+")] += 1
            _xmp(body[len(b"http://ns.adobe.com/xap/1.0/\0"):], counts, toolkits)
        elif segment.kind == "iptc":
            counts["IPTC stray bytes after the last 8BIM"] += segment.stray > 0
            counts["IPTC padded (PAD_RESOURCE)"] += segment.slack > 0
            _iptc(segment, header, counts)
    orders[" ".join(labels)] += 1
    for label in every:
        counts["with " + label] += 1
    for kind, name in (("exif", "EXIF"), ("xmp", "XMP"), ("iptc", "IPTC")):
        counts["%s segments %s" % (name, _how_many(len(layout.of_kind(kind))))] += 1
    counts["extended XMP"] += bool(layout.of_kind("xmp-extension"))
    if "EXIF" in labels:
        before = labels[:labels.index("EXIF")]
        counts["EXIF " + ("first" if not before else "after JFIF only" if before == ["JFIF"]
                          else "after tables" if "tables" in before else "after another APP")] += 1
    ends = tail.rstrip(b"\0")
    counts["after the last EOI: " + ("nothing" if tail.endswith(b"\xff\xd9") else "zero bytes"
                                     if ends.endswith(b"\xff\xd9") else "other bytes")] += 1


# --- EXIF ---------------------------------------------------------------------------------------------------------


def _ifd(tiff, at, order):
    """({tag: (type, count, the four value bytes, the value as an offset)}, next IFD) of the IFD at `at`, or None."""
    found = containers.ifd(tiff, at, order) if at >= 8 else None
    if found is None or found[1] is None or not 1 <= len(found[0]) <= 1000:
        return None
    return {entry[0]: entry[1:] for entry in found[0]}, found[1]


def _vendor(tiff, entries):
    found = entries.get(0x010F)
    if found is None or found[0] != 2:
        return "(no Make)"
    return VENDORS.get(containers.make_word(tiff, (0x010F,) + found), "other")


def _exif(tiff, counts, makers):
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        counts["EXIF not a TIFF structure"] += 1
        return
    order = "<" if tiff[:2] == b"II" else ">"
    ifd0 = _ifd(tiff, struct.unpack(order + "I", tiff[4:8])[0], order)
    if ifd0 is None:
        counts["EXIF IFD0 not followed"] += 1
        return
    entries, next_ifd = ifd0
    ifd1 = _ifd(tiff, next_ifd, order) if next_ifd else None
    counts["IFD1 thumbnail"] += bool(ifd1 and 0x0201 in ifd1[0] and 0x0202 in ifd1[0])
    turned = entries.get(0x0112)
    counts["EXIF Orientation " + (str(struct.unpack(order + "H", turned[2][:2])[0]) if turned and turned[0] == 3
                                  else "absent")] += 1
    sub = entries.get(0x8769)
    exif_ifd = _ifd(tiff, sub[3], order) if sub else None
    note = exif_ifd[0].get(0x927C) if exif_ifd else None
    vendor = _vendor(tiff, entries)
    if note is None:
        makers[vendor]["no MakerNote"] += 1
    else:
        counts["MakerNote"] += 1
        if note[1] <= 4:
            makers[vendor]["inline"] += 1
            return
        label, bad, footer = containers.maker_note(tiff, note[3], note[1], order, canon=vendor == "Canon")
        makers[vendor][label] += 1
        counts["MakerNote with entries skipped"] += bad > 0
        schema = exif_ifd[0].get(0xEA1D)
        if footer is not None and schema is not None:
            said = struct.unpack(order + "i", schema[2])[0] if schema[0] == 9 else None
            counts["MakerNote OffsetSchema %s the Canon footer" % ("equals" if said == footer else "differs from")] += 1


# --- XMP ----------------------------------------------------------------------------------------------------------


def _xmp(packet, counts, toolkits):
    if packet[:2] in (b"\xfe\xff", b"\xff\xfe") or packet[:4] in (b"\0<\0?", b"<\0?\0"):
        counts["XMP encoding UTF-16"] += 1
        return
    begin = _BEGIN.search(packet[:200])
    counts["XMP " + ("no packet wrapper" if begin is None else "begin BOM" if begin.group(2) == b"\xef\xbb\xbf"
                     else "begin empty" if not begin.group(2) else "begin other")] += 1
    try:
        packet.decode("utf-8")
    except UnicodeDecodeError:
        counts["XMP not UTF-8"] += 1
    if b"<!DOCTYPE" in packet or b"<!ENTITY" in packet:
        counts["XMP with a DTD (not parsed)"] += 1
    else:
        try:
            ElementTree.fromstring(packet.strip(b" \t\r\n\0"))
        except ElementTree.ParseError:
            counts["XMP not well-formed"] += 1
    descriptions = len(re.findall(rb"<rdf:Description[\s>/]", packet))
    counts["XMP rdf:Description %s" % _how_many(descriptions)] += 1
    counts["XMP two rdf:about values"] += len({found.group(2) for found in _ABOUT.finditer(packet)}) > 1
    toolkit = _TOOLKIT.search(packet)
    named = toolkit.group(2).decode("latin-1") if toolkit else None
    toolkits["(none)" if named is None else next((bucket for bucket, mark in TOOLKITS if mark in named), "other")] += 1
    for name, mark in XMP_MARKS.items():
        counts["XMP " + name] += mark in packet
    subject = re.search(rb"<dc:subject>\s*<rdf:(\w+)", packet)
    if subject:
        counts["XMP dc:subject as " + subject.group(1).decode("latin-1")] += 1
    for name in TARGETS:
        spelled = re.escape(name.encode("ascii"))
        elements = len(re.findall(rb"<" + spelled + rb"[\s>/]", packet))
        attributes = len(re.findall(rb"\s" + spelled + rb"\s*=", packet))
        counts["XMP %s twice" % name] += elements + attributes > 1
        counts["XMP %s as an attribute" % name] += attributes > 0


# --- IPTC ---------------------------------------------------------------------------------------------------------


def _resource_data(resource, header):
    return header[resource.data:resource.data + resource.length]


def _iptc(segment, header, counts):
    by_id = collections.defaultdict(list)
    for resource in segment.resources:
        by_id[resource.id].append(_resource_data(resource, header))
    blocks = by_id.get(0x0404, [])
    counts["IPTC 0x0404 blocks %s" % _how_many(len(blocks))] += 1
    if not blocks:
        return
    block, datasets = blocks[0], {}
    records, at = containers.iptc_records(block)
    for record in records:
        datasets.setdefault((record.record, record.dataset), []).append(block[record.start:record.end])
    counts["IPTC datasets cut short or garbage"] += at < len(block.rstrip(b"\0"))
    charset = datasets.get((1, 90))
    counts["IPTC 1:90 " + ("absent" if not charset else "UTF-8" if charset[0] == b"\x1b%G" else "other")] += 1
    counts["IPTC envelope 1:00"] += (1, 0) in datasets
    counts["IPTC values outside ASCII"] += any(max(value, default=0) >= 0x80 for key, values in datasets.items()
                                               if key[0] == 2 for value in values)
    digest = by_id.get(0x0425)
    counts["IPTCDigest " + ("absent" if not digest else "matches" if digest[0] == hashlib.md5(block).digest()
                            else "differs")] += 1


# --- the census ---------------------------------------------------------------------------------------------------


def is_jpeg(path):
    """Is the photo at `path` a JPEG by its extension (JPEG_EXTENSIONS)?"""
    return os.path.splitext(path)[1].lower() in JPEG_EXTENSIONS


def drawn(jpegs, sample, seed):
    """`sample` of the paths `jpegs` (all of them when fewer), drawn with `seed`, in the order of `jpegs`: the census's
    draw, which the shadow comparison of the writers (tagpup.services.metadata_check) makes too, so both read the same
    photos."""
    return [jpegs[index] for index in sorted(random.Random(seed).sample(range(len(jpegs)), min(sample, len(jpegs))))]


def census(library, sample=SAMPLE, seed=SEED, timed=TIMED):
    """What `library`'s JPEGs hold in their metadata segments (the module says), with the extensions and kinds of all
    its photos and its journal's files by state. A library whose root this machine does not place answers
    `roots_problem`, its sentence, and reads no file."""
    if sample < 1:
        raise Refused("A census reads one photo or more.")
    sentence = roots_service.problem(library)
    if sentence:
        return {"roots_problem": sentence}
    with closing(db.connect(db.readonly_uri(library.path), uri=True)) as conn:
        held = inspection.photo_files(conn)
        kinds = inspection.kinds(conn)
        states = inspection.file_states(conn)
    by_type = collections.Counter()
    jpegs = []
    for photo_id, path in held:
        extension = os.path.splitext(path)[1].lower() or "(none)"
        by_type["%s %s" % (extension, kinds.get(photo_id, "?"))] += 1
        if is_jpeg(path):
            jpegs.append(path)
    chosen = drawn(jpegs, sample, seed)
    looked, counts = collections.Counter(), collections.Counter()
    makers, toolkits, orders = collections.defaultdict(collections.Counter), collections.Counter(), collections.Counter()
    times = []
    for path in chosen:
        began = time.perf_counter()
        state, found = _looked(path)
        if state == "read" and len(times) < timed:
            times.append((time.perf_counter() - began) * 1000)
        if state == "read" and not isinstance(found[0], containers.Jpeg):
            state = "unreadable" if found[0].reason.startswith("the file could not be read") else "not a JPEG"
        elif state == "read" and found[0].image_offset is None:
            state = "no scan (%s)" % (found[0].tail.reason if found[0].tail else "ends before one")
        elif state == "read":
            _shape(*found, counts, makers, toolkits, orders)
        looked[state] += 1
    return {"counted": datetime.date.today().isoformat(), "photos": len(held), "by_type": dict(by_type),
            "jpegs": len(jpegs), "sampled": len(chosen), "seed": seed, "looked": dict(looked), "counts": dict(counts),
            "makers": {vendor: dict(found) for vendor, found in makers.items()}, "toolkits": dict(toolkits),
            "orders": dict(orders.most_common(12)), "journal": [list(row) for row in states],
            "read_ms": _timing(times)}


def _timing(times):
    if not times:
        return {"timed": 0}
    ordered = sorted(times)
    return {"timed": len(times), "median": round(statistics.median(ordered), 1),
            "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1), "max": round(ordered[-1], 1)}

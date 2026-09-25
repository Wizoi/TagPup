"""The metadata fields this app reads and writes, and which values a write puts in each.

Plain lists and rules over them; reading and writing the files is tagpup.files'. Kept
here so that everything recording a write -- the index as well as the file -- goes by
the same lists: the index once recorded two of the keyword fields and not the third, and
a removed tag came back from it.
"""
import json
import re

# Define target fields mapped to keys we want to return
# ExifTool output keys can be namespaced or bare (without prefix).
# We check both to be safe.
METADATA_FIELDS = [
    # Keywords / tags
    "IPTC:Keywords", "Keywords",
    "XMP:Subject", "Subject",
    "XMP:HierarchicalSubject", "HierarchicalSubject",
    # People / faces
    "XMP:PersonInImage", "PersonInImage",
    "XMP:RegionName", "RegionName",
    # Caption
    "IPTC:Caption-Abstract", "Caption-Abstract",
    "XMP:Description", "Description",
    # Title
    "XMP:Title", "Title",
    "IPTC:ObjectName", "ObjectName",
    # Date taken
    "EXIF:DateTimeOriginal", "DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate",
    # Location
    "XMP:City", "City",
    "XMP:State", "State",
    "XMP:Country", "Country",
    "IPTC:Province-State", "Province-State",
    "IPTC:Country-PrimaryLocationName", "Country-PrimaryLocationName",
    # GPS
    "Composite:GPSLatitude", "GPSLatitude",
    "Composite:GPSLongitude", "GPSLongitude",
    # Camera
    "EXIF:Make", "Make",
    "EXIF:Model", "Model",
    # Rating
    "XMP:Rating", "Rating",
    # Identity. A path is a bad name for a photo -- rename the file and the index is
    # left describing something that no longer exists. DocumentID is the XMP
    # standard's per-document identifier, and most photos already carry one.
    "XMP-xmpMM:DocumentID", "XMP:DocumentID", "DocumentID"
]


def scan_reads(field):
    """Does the folder scan store `field` in a photo's raw_metadata? It asks ExifTool for
    METADATA_FIELDS, and a name asked for bare is answered under every group that holds
    it: CreateDate brings back EXIF:CreateDate and XMP:CreateDate. So a written field is
    recorded in the row when its group's name or its bare name is asked for -- the rule
    every recorder of a write goes by. XMP:CreateDate, which Shift Date Taken moves, was
    left out when the rule was "named in METADATA_FIELDS", and the row kept the old date
    (docs/findings.md, #270). EXIF:XPKeywords and EXIF:ImageDescription, which keyword
    and caption writes set, are not read, and a row does not hold them."""
    key = read_key(field)
    bare = key.partition(":")[2] or key
    return key in METADATA_FIELDS or bare in METADATA_FIELDS


#: The fields a photo's camera is named from, the first it has: what Shift Date Taken
#: chooses photos by. The TagPup page names cameras the same way, to offer them and to
#: show which photos a shift is about, from a copy of these that
#: tests/test_rules_have_one_owner.py holds to them (docs/findings.md, #74).
CAMERA_FIELDS = ("EXIF:Model", "Model", "EXIF:Make", "Make")

#: The camera of a photo that names none.
UNKNOWN_CAMERA = "Unknown Camera"

#: The camera a time shift is asked for to shift every photo, whatever its camera.
ALL_CAMERAS = "All Cameras"


def camera_of(raw_metadata):
    """The camera a photo came from, by its metadata: the first of CAMERA_FIELDS it has,
    else UNKNOWN_CAMERA."""
    raw = raw_metadata or {}
    return next((raw[field] for field in CAMERA_FIELDS if raw.get(field)), UNKNOWN_CAMERA)


def on_camera(raw_metadata, camera):
    """Is a photo with this metadata one a time shift for `camera` is about?"""
    return camera == ALL_CAMERAS or camera_of(raw_metadata) == camera


#: The fields a photo's tags are read from: exactly what vocabulary.extract_tags
#: reads, so tags read here are the tags a folder scan would have found.
TAG_SOURCE_FIELDS = ("IPTC:Keywords", "XMP:Subject", "XMP:HierarchicalSubject")


def expand_tag_fields(tags):
    """Split a tag list into the flat and hierarchical keyword forms written to files.

    A tag is written whole. It used to be written whole *and* broken into its
    segments, so "Family/Immediate/Cora Ingersoll" became four keywords -- the path plus
    "Family", "Immediate" and "Cora Ingersoll". That buries a deliberate hierarchy under
    its own fragments, and the bare leaf is the form that gave one person two entries
    in the Add Person list.

    The convention comes from the library rather than from a default: of 18,502
    keyword values in this one, 18,364 are full paths separated by "/" and none are
    bare leaves.
    """
    flat, hierarchical = [], []
    for tag in tags:
        if tag not in flat:
            flat.append(tag)
        if "/" in tag and tag not in hierarchical:
            hierarchical.append(tag)
    return flat, hierarchical


def keyword_fields(flat, hierarchical):
    """Every field a keyword write sets, and what it sets it to.

    The one list of them. write_keywords writes exactly these, and
    record_keyword_fields records exactly these, so the file and its index row cannot
    drift apart one field at a time. They did: the index recorded the two XMP fields
    and not IPTC:Keywords, which vocabulary.extract_tags also reads, so a tag removed
    in bulk stayed in raw_metadata and came back the next time anything re-derived
    tags from it -- renaming an unrelated tag, for one.

    An empty value means the field is cleared.
    """
    return {
        "XMP:Subject": list(flat),
        "IPTC:Keywords": list(flat),
        "EXIF:XPKeywords": ";".join(flat),
        "XMP:HierarchicalSubject": list(hierarchical),
    }


def caption_fields(caption):
    """Every field a caption write sets. An empty caption clears them all.

    Written by saving a photo and by the CLI's writer, which each kept their own copy
    of this list.
    """
    return {
        "XMP:Description": caption,
        "IPTC:Caption-Abstract": caption,
        # EXIF ImageDescription maps to System.Title (Title) in C# code
        "EXIF:ImageDescription": caption,
        # EXIF XPComment maps to System.Comment (Caption) in C# code
        "EXIF:XPComment": caption,
    }


def record_keyword_fields(raw_meta, flat, hierarchical):
    """Make `raw_meta` say what a keyword write just put in the file. Returns it.

    Only fields the scan reads are recorded, so a row written here looks the same as
    one read back from the file. A field under its bare name ("Keywords") is the same
    value the scan stored twice, and is rewritten too; a stale copy there would be
    read back just the same. A cleared field is removed, as a scan would find nothing.
    """
    for field, value in keyword_fields(flat, hierarchical).items():
        if not scan_reads(field):
            continue
        bare = field.split(":", 1)[1]
        for name in (field, bare):
            if name != field and name not in raw_meta:
                continue
            if value:
                raw_meta[name] = list(value)
            else:
                raw_meta.pop(name, None)
    return raw_meta


def date_taken_value(date_taken):
    """How a Date Taken set on the page is written: "2019-06-15T10:30:00" as
    "2019:06:15 10:30:00", the way EXIF spells a date."""
    return str(date_taken).replace("T", " ").replace("-", ":").strip()


def date_taken_fields(date_taken):
    """Every field a Date Taken write sets, the fraction of a second too when one is
    given ("10:30:00.123")."""
    value = date_taken_value(date_taken)
    written = {"EXIF:DateTimeOriginal": value, "XMP:DateTimeOriginal": value, "EXIF:CreateDate": value}
    parts = value.split(".")
    if len(parts) > 1:
        digits = ""
        for char in parts[1]:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            written["EXIF:SubSecTimeOriginal"] = digits
            written["EXIF:SubSecTimeDigitized"] = digits
            written["EXIF:SubSecTime"] = digits
    return written


def record_date_taken(raw_meta, date_taken):
    """Make `raw_meta` say what a Date Taken write just put in the file -- the fields
    the scan reads, as record_keyword_fields does for keywords. Returns it."""
    for field, value in date_taken_fields(date_taken).items():
        if scan_reads(field):
            raw_meta[field] = value
    return raw_meta


# ---- A field's value, as the file journal holds and compares it ------------------------

#: The fields whose value is a list: a keyword field holds several, and ExifTool answers
#: one of them alone as a bare value.
LIST_FIELDS = ("XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject",
               "XMP:PersonInImage", "XMP:RegionName")


def read_key(field):
    """The key ExifTool answers `field` under when it reads with -G (group family 0):
    "XMP-xmpMM:DocumentID" is written under its own group and answered as
    "XMP:DocumentID"; "XMP:Subject" is both."""
    group, _, name = str(field).partition(":")
    return "%s:%s" % (group.split("-", 1)[0], name) if name else field


def field_values(value):
    """A field's value as the journal keeps it: a list of texts, in the file's order,
    blanks and empties left out. A field the file does not hold is []."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    found = []
    for item in items:
        if item is None or isinstance(item, (bytes, dict)):
            continue
        text = str(item)
        if text.strip():
            found.append(text)
    return found


def same_values(a, b):
    """Do two values of one field say the same, as a reader of the file sees it: the
    same texts, trimmed, in any order? A keyword field's order is not a fact about the
    photo."""
    return sorted(t.strip() for t in field_values(a)) == sorted(t.strip() for t in field_values(b))


def same_fields(held, wanted):
    """Does a file holding `held` ({field: value}) hold every field of `wanted`, text for
    text? What a file is recorded as holding is decided by this; whether a file holds
    what a write left is reads_same's question."""
    return all(same_values(held.get(field), value) for field, value in wanted.items())


# ---- A value as a read of the file gives it ----------------------------------------------

#: A text ExifTool answers as a JSON number, by the pattern its JSON writer goes by
#: (EscapeJSON): "1.50" comes back 1.5, "1e5" 100000.0; "007" and a number of more than
#: fifteen digits stay text.
_JSON_NUMBER = re.compile(r"-?(\d|[1-9]\d{1,14})(\.\d{1,16})?([eE][-+]?\d{1,3})?")


#: The bytes IPTC keeps of a value of each field ExifTool writes or the scan reads (the
#: IPTC-IIM limits ExifTool enforces): a longer value is cut.
IPTC_LIMITS = {"IPTC:Keywords": 64, "IPTC:Caption-Abstract": 2000, "IPTC:ObjectName": 64,
               "IPTC:Province-State": 32, "IPTC:Country-PrimaryLocationName": 64, "IPTC:City": 32}

#: The character sets IPTC holds a text in: ExifTool's default, Latin (cp1252), where a
#: letter it lacks is written "?", and UTF-8, in a file whose CodedCharacterSet says so.
IPTC_CHARSETS = ("cp1252", "utf-8")


def _as_read(key, text, charset):
    limit = IPTC_LIMITS.get(key)
    if limit is not None:
        # Encoded as the file holds it and cut to what IPTC keeps; a letter cut in two
        # is not read back.
        text = text.encode(charset, errors="replace")[:limit].decode(charset, errors="ignore")
    text = text.strip()
    if _JSON_NUMBER.fullmatch(text):
        # The reader parses ExifTool's JSON; the journal keeps str() of what it gives.
        return str(json.loads(text))
    return text


def as_read(field, value, charset=IPTC_CHARSETS[0]):
    """What a read of a file gives for `value` written to `field`, as the journal keeps a
    value (field_values), trimmed: IPTC cuts a keyword at 64 bytes and writes a letter
    outside its `charset` as "?" (IPTC_LIMITS, IPTC_CHARSETS), and a text that looks like
    a number is answered as one. A value written is not always read back as written;
    comparing what was asked with what a read gives found a difference where there was
    none (docs/findings.md, #264, #277)."""
    key = read_key(field)
    return [_as_read(key, text, charset) for text in field_values(value)]


def same_read(field, a, b):
    """Would two values of `field` read back the same from a file, in any order? Each is
    taken through what a read gives (as_read), so a value read from a file and one about
    to be written are compared alike; an IPTC field in either character set it may be
    held in, both sides in the same one."""
    charsets = IPTC_CHARSETS if read_key(field) in IPTC_LIMITS else IPTC_CHARSETS[:1]
    return any(sorted(as_read(field, a, charset)) == sorted(as_read(field, b, charset)) for charset in charsets)


def reads_same(held, wanted):
    """Does a file holding `held` ({field: value}) hold every field of `wanted`, as a read
    of it after writing `wanted` would give it? Whether a file already holds what a write
    is to leave, still holds what a plan read, or holds what a write left."""
    return all(same_read(field, held.get(field), value) for field, value in wanted.items())

"""The metadata fields this app reads and writes, which values a write puts in each, and which copy of a value a
read takes: a photo's keywords as held, its title and captions (XMP's copy first: title_of, extract_captions).

Plain lists and rules over them; reading and writing the files is tagpup.files'. Kept
here so that everything recording a write -- the index as well as the file -- goes by
the same lists: the index once recorded two of the keyword fields and not the third, and
a removed tag came back from it.
"""
import json
import re

from tagpup.core import dates, photo_meta

# Define target fields mapped to keys we want to return
# ExifTool output keys can be namespaced or bare (without prefix).
# We check both to be safe.
METADATA_FIELDS = [
    # Keywords / tags
    "IPTC:Keywords", "Keywords",
    "XMP:Subject", "Subject",
    "XMP:HierarchicalSubject", "HierarchicalSubject",
    # Not XMP PersonInImage or RegionName: nothing reads a file's person fields (a bare name is nobody, owner
    # 2026-10-15), and no photo of the three libraries holds either (counted 2026-10-15).
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
    # A clip's, its XMP dates first (tagpup.core.dates.CLIP_XMP_DATE_FIELDS: XMP:DateTimeOriginal above, and
    # XMP:CreateDate, which CreateDate brings), then its QuickTime ones (CLIP_DATE_FIELDS: CreateDate brings
    # QuickTime:CreateDate).
    "QuickTime:CreationDate",
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
    # Lens. A bare name is answered under every group that holds it (checked on 400 files of a
    # library, 2026-10-09): LensModel brings EXIF:, XMP: and MakerNotes:LensModel, LensMake brings
    # EXIF:LensMake, LensID brings Composite:LensID and XMP:LensID. Run without print conversion a
    # LensID is a number for Canon and Tamron (photo_meta.gear ignores a number) and text only for
    # Google; LensModel is the lens's own name wherever the camera writes one.
    "EXIF:LensModel", "LensModel",
    "XMP:LensModel",
    "EXIF:LensMake", "LensMake",
    "Composite:LensID", "LensID",
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


#: The keys a read answers a photo's DocumentID under (the XMP standard's per-document identifier, tagpup.files.identity).
DOCUMENT_ID_KEYS = ("XMP:DocumentID", "XMP-xmpMM:DocumentID", "DocumentID")


def read_document_id(metadata):
    """The photo's identity as its metadata reports it, or None."""
    if not isinstance(metadata, dict):
        return None
    for key in DOCUMENT_ID_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


#: The field a rotation journals: the EXIF Orientation, 1 to 8 (tagpup.services.rotation).
ORIENTATION = "EXIF:Orientation"

#: Fields written under another name than the one they are read and journaled by. Orientation is written without its group,
#: so that an XMP tiff:Orientation already in the file is updated to match and not left to contradict the EXIF one.
WRITTEN_AS = {ORIENTATION: "Orientation"}


#: The camera of a photo that names none.
UNKNOWN_CAMERA = "Unknown Camera"

def camera_of(raw_metadata):
    """The camera a photo came from, by its metadata: its one name (tagpup.core.photo_meta.camera_name, which Image
    Details and the search use), else UNKNOWN_CAMERA. The TagPup page gets it in the photo's record (`camera`) and
    chooses a Shift Date Taken's photos by it, keeping no copy of the rule (docs/findings.md, #74, #1003)."""
    return photo_meta.gear(raw_metadata).camera or UNKNOWN_CAMERA


#: The fields a photo's tags are read from: exactly what vocabulary.extract_tags reads, so tags read here are the tags a
#: folder scan would have found. A clip's too: its QuickTime Subject is never a keyword (KEYWORD_COPIES).
TAG_SOURCE_FIELDS = ("IPTC:Keywords", "XMP:Subject", "XMP:HierarchicalSubject")

#: Where a clip may hold a title besides XMP: its QuickTime Title (2,017 photo_index clips hold their title there alone,
#: one more there and in XMP:Title alike; no photo of the three libraries holds a QuickTime field; counted 2026-11-07).
#: Read as XMP's copy when XMP holds none (TITLE_COPIES). A save writes XMP:Title (title_fields), and this
#: only to clear it, when the save leaves the clip with no XMP:Title, and its undo to give it back (a clip's title write,
#: _CLIP_WRITE_FIELDS; tagpup.files.clip_patch frees its item; DECISIONS 2026-11-08). A title typed over leaves it as
#: it was.
CLIP_TITLE = "QuickTime:Title"

#: A clip's Windows Tags: the WM/Category entry of its Microsoft Xtra box (2,093 photo_index clips, counted 2026-11-09),
#: which the reader never reads as tags (DECISIONS 2026-11-07, #2662). A journaled write names it only to free the Xtra
#: in the same patch that writes the clip's XMP ([]), and its undo to give the Xtra back (the texts it held): the
#: one-time move of those Tags into XMP (a clip's tags write, _CLIP_WRITE_FIELDS; tagpup.files.clip_patch; DECISIONS
#: 2026-11-09). ExifTool reads it under its group 1 name, Microsoft, and answers it under group 0's (read_key).
CLIP_CATEGORY = "Microsoft:Category"
#: A clip's QuickTime dates a save of its Date Taken writes beside its XMP date, in one patch (DECISIONS 2026-11-13, A
#: CLIP'S DATE TAKEN IS WRITTEN INTO ITS QUICKTIME HEADERS): the creation time of its movie header, of each track's and
#: of each media's (tagpup.files.clip_patch). A write reads them by TagPup's own walk of the clip, each the texts of its
#: boxes in file order, one when they agree (tagpup.files.field_values.read); ExifTool answers one box of each.
CLIP_HEADER_DATES = ("QuickTime:CreateDate", "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate")
#: The fields of a clip's write that clip_patch writes outside its XMP packet, which the packet's editor leaves out.
CLIP_OUTSIDE_XMP = frozenset({CLIP_TITLE, CLIP_CATEGORY, *CLIP_HEADER_DATES})


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

    The one list of them. The journaled write (tagpup.services.tagging) writes exactly these, and
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
    """Every field a photo's caption is held in -- its extended information, not its title (title_fields) -- and what
    a write of `caption` sets each to. An empty caption clears them all. No page writes a caption (DECISIONS
    2026-11-06, THE TITLE AND THE CAPTION ARE TWO VALUES): a save clears the ones holding the text it moves into the
    title (tagpup.services.tagging), and nothing else writes them.
    """
    return {
        "XMP:Description": caption,
        "IPTC:Caption-Abstract": caption,
        # EXIF ImageDescription maps to System.Title (Title) in C# code
        "EXIF:ImageDescription": caption,
        # EXIF XPComment maps to System.Comment (Caption) in C# code
        "EXIF:XPComment": caption,
    }


#: The caption fields a copy of the title is cleared from by the one-off sweep (tagpup.services.tagging.
#: clear_duplicate_captions): every one of caption_fields but XPComment, Windows' Comment, which it never touches (the
#: Files owner's plan, 2026-11-06).
SWEPT_CAPTION_FIELDS = tuple(field for field in caption_fields("") if field != "EXIF:XPComment")


def title_fields(title):
    """Every field a photo's title is held in, and what a write of `title` sets each to: the one list. The title is
    what the page's title field saves and Smart Rename names a photo by (DECISIONS 2026-11-06, THE TITLE AND THE
    CAPTION ARE TWO VALUES); an empty title clears them both."""
    return {"XMP:Title": title, "IPTC:ObjectName": title}


#: The fields a keyword, title or caption save leaves holding what they held when it patches the file's XMP alone, by
#: the XMP field whose value makes it so: every field keyword_fields, title_fields and caption_fields set outside XMP, refreshed by the next
#: rewrite that writes them (DECISIONS 2026-11-03, A KEYWORD OR CAPTION SAVE WRITES THE XMP COPY ONLY). The index reads
#: XMP (KEYWORD_COPIES, below). A group is left only while its XMP field's value is not empty: XMP holding none, the
#: reader would take the left copy and the value removed would come back (#2433; tagpup.files.segment_edits).
XMP_ONLY = {
    "XMP:Subject": tuple(field for field in keyword_fields([], []) if not field.startswith("XMP:")),
    "XMP:Title": tuple(field for field in title_fields("") if not field.startswith("XMP:")),
    "XMP:Description": tuple(field for field in caption_fields("") if not field.startswith("XMP:")),
}

# ---- What a photo's metadata says: which copy of a value is read --------------------------
#
# The read side of XMP_ONLY above (DECISIONS 2026-11-03): a save may leave IPTC's copy as it was, and the reader takes
# XMP's while it holds a value. `meta` is the record tagpup.files.metadata reads: every field under its ExifTool name
# ("XMP:Subject") and again bare ("Subject"). Which of a photo's keywords are tags, and whom they name, is
# tagpup.core.vocabulary's (extract_tags, over keywords_held).

#: A value a file holds twice, in XMP and in IPTC's older copy: (XMP's fields, IPTC's), each under both names a read
#: records it by. XMP is the copy read when it holds the value, a text that is not blank; IPTC's only when it holds
#: none (DECISIONS 2026-11-03, XMP IS THE COPY THE INDEX READS): a keyword or caption save may patch XMP alone and
#: leave IPTC holding what it held before (XMP_ONLY). A title's copies (TITLE_COPIES) are
#: XMP:Title and IPTC:ObjectName (title_fields), a caption's (DESCRIPTION_COPIES) the Description.
#: A clip's title may be in its QuickTime Title (CLIP_TITLE), which TagPup writes only to clear it with XMP's: read
#: as XMP's copy when XMP holds none (a clip's title is its XMP:Title, else its QuickTime Title: DECISIONS 2026-11-07,
#: item 3; 2026-11-08).
#: ExifTool answers it beside XMP's, and the bare "Title" is whichever it answered last
#: (tagpup.files.metadata.structured), so a copy is read by the first of its names that holds a value (_read_copy), the
#: prefixed names first. A keyword is read by its group's name alone, XMP:Subject's or IPTC:Keywords': the bare
#: "Subject" of a clip holding no XMP:Subject is its QuickTime Subject, a Microsoft Xtra entry that is not text on all
#: 14 live clips holding one (a 32-byte value ExifTool answers as hex; no clip of the three libraries holds a text
#: QuickTime keyword, counted 2026-11-07), and was read as a tag until the owner ruled it out (2026-11-07, #2662). Every
#: row of the three libraries holding a bare keyword name holds its group's name beside it (counted 2026-11-07).
KEYWORD_COPIES = (("XMP:Subject",), ("IPTC:Keywords",))
TITLE_COPIES = (("XMP:Title", CLIP_TITLE, "Title"), ("IPTC:ObjectName", "ObjectName"))
DESCRIPTION_COPIES = (("XMP:Description", "Description"), ("IPTC:Caption-Abstract", "Caption-Abstract"))
#: The title first, then the caption: the first a photo holds is its title as the page shows it (title_of). A photo
#: with a Description and no Title shows the Description as its title (DECISIONS 2026-11-06, THE TITLE AND THE
#: CAPTION ARE TWO VALUES: the first save moves it into the title, tagpup.services.tagging).
CAPTION_COPIES = (TITLE_COPIES, DESCRIPTION_COPIES)

#: Keywords, flat and hierarchical: every field a photo's tags may be read from. The hierarchy is XMP's alone.
KEYWORD_FIELDS = KEYWORD_COPIES[0] + KEYWORD_COPIES[1]
HIERARCHY_FIELDS = ("XMP:HierarchicalSubject", "HierarchicalSubject")

#: Titles and captions, the title first: the first a photo holds (by CAPTION_COPIES) is the title shown.
CAPTION_FIELDS = tuple(name for copies in CAPTION_COPIES for copy in copies for name in copy)


def _held_caption_fields():
    written = [name for field in caption_fields("") for name in (field, field.split(":", 1)[-1])]
    return CAPTION_FIELDS + tuple(name for name in dict.fromkeys(written) if name not in CAPTION_FIELDS)


#: Every field a photo's caption is held in: those read as its captions, and the ones a
#: caption write sets as well (caption_fields: EXIF's too) -- where a
#: caption another program wrote may be all the file holds. Asked when a caption is
#: being set, to tell one the file holds already from a new one.
HELD_CAPTION_FIELDS = _held_caption_fields()


def trimmed(value):
    """A field's value as the reader keeps it: text, without the blanks around it."""
    return str(value).strip()


def _values(meta, names):
    """Every value these fields hold, trimmed, in field order; empty ones left out."""
    found = []
    for key in names:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                found.extend(t for t in (trimmed(v) for v in val if v) if t)
            elif trimmed(val):
                found.append(trimmed(val))
    return found


def _read_copy(meta, copies):
    """The values of one value held twice (KEYWORD_COPIES, a pair of CAPTION_COPIES): XMP's when it holds one, else
    IPTC's, each read by the first of its names that holds a value (a photo's bare name holds what its prefixed one
    does; a clip's bare "Title" may be its QuickTime copy, read only when XMP holds none). A field present but blank
    holds none."""
    for names in copies:
        for name in names:
            found = _values(meta, (name,))
            if found:
                return found
    return []


def keywords_held(meta):
    """Every keyword a photo's metadata holds, trimmed, in order: the flat ones of the copy read (KEYWORD_COPIES: XMP's
    when it holds any, else IPTC's), then the hierarchical ones. Which of them are tags is vocabulary.extract_tags'."""
    return _read_copy(meta, KEYWORD_COPIES) + _values(meta, HIERARCHY_FIELDS)


def extract_captions(meta):
    """A photo's title and caption -- each distinct text once, in order: the index's `captions`, which a search reads
    every word of.

    The reader records every field twice, prefixed and bare, and the same text is
    usually written to several fields, so reading them all listed each caption two or
    more times: 99.6% of indexed rows carried a duplicate. The first is the photo's
    title as everything shows it (title_of): XMP's Title when it holds one, else IPTC's
    ObjectName, else the description, XMP's, else IPTC's Caption-Abstract (CAPTION_COPIES).
    """
    return list(dict.fromkeys(c for copies in CAPTION_COPIES for c in _read_copy(meta, copies) if c))


def title_of(meta):
    """A photo's title as the page shows it, the index orders by and Smart Rename names it by: the first of its
    captions (extract_captions), trimmed; "" for none. The one rule of which text is the title."""
    found = extract_captions(meta)
    return found[0] if found else ""


def own_title(meta):
    """The title the photo holds of its own (TITLE_COPIES: XMP:Title, else a clip's QuickTime Title, else IPTC:ObjectName),
    trimmed; "" for none, a photo showing its description as its title among them."""
    found = _read_copy(meta, TITLE_COPIES)
    return found[0] if found else ""


def holds_title(meta):
    """Does the photo hold a title of its own (TITLE_COPIES), not only a description shown as one?"""
    return bool(own_title(meta))


def captions_held(meta):
    """Every caption a file holds, in any field a caption is held in (HELD_CAPTION_FIELDS),
    each distinct text once, trimmed as the reader trims it."""
    return list(dict.fromkeys(c for c in _values(meta, HELD_CAPTION_FIELDS) if c))


#: Where a photo or clip keeps the name it had before Smart Rename first renamed it (tagpup.files.names, which reads it;
#: tagpup.services.photos.preserve_names, which writes it before the first rename).
PRESERVED_NAME = "XMP-xmpMM:PreservedFileName"

#: What a journaled write may change of a photo, by name: its tags, its title, its Date Taken, its turn, its name
#: (tagpup.files.field_values.writes, which a page's record carries as `writes`).
PHOTO_WRITES = ("tags", "title", "date", "rotate", "name")

#: What a journaled write may change of a clip (DECISIONS 2026-11-05, M7; on since DECISIONS 2026-11-07): its tags, its
#: title, its name (DECISIONS 2026-11-08, A5: a rename moves no byte, and the name it had before its first is kept in
#: its XMP, PRESERVED_NAME, patched as its tags are) and its Date Taken, in its XMP (DECISIONS 2026-11-08, A5 (D)
#: REVISED: the date the library reads first, tagpup.core.dates.CLIP_XMP_DATE_FIELDS) and its QuickTime headers
#: (CLIP_HEADER_DATES, DECISIONS 2026-11-13). A page writes no caption of it
#: (DECISIONS 2026-11-06); a turn of a clip is ExifTool's rewrite of the whole file, refused before it is planned.
CLIP_WRITES = ("tags", "title", "name", "date")

#: The fields each of CLIP_WRITES names: a clip's tags are its keyword fields and its Windows Tags (CLIP_CATEGORY); its
#: title is its title fields and its QuickTime Title (CLIP_TITLE), which
#: a photo's write never names (title_fields is what ExifTool writes into a JPEG); its name, the one it had before; its
#: date, its XMP dates and its QuickTime headers' (date_taken_fields of a clip).
_CLIP_WRITE_FIELDS = {"tags": frozenset(keyword_fields([], [])) | {CLIP_CATEGORY},
                      "title": frozenset(title_fields("")) | {CLIP_TITLE},
                      "name": frozenset({PRESERVED_NAME}),
                      "date": frozenset(dates.CLIP_XMP_DATE_FIELDS + CLIP_HEADER_DATES)}


def fields_of(writes):
    """The fields a journaled write of `writes` (names of CLIP_WRITES) may name."""
    return frozenset(field for write in writes for field in _CLIP_WRITE_FIELDS[write])


#: The fields a journaled write of a clip may name: those of CLIP_WRITES. Only the XMP field of each group is written,
#: into the clip's XMP packet (tagpup.files.segment_edits.new_packet), the QuickTime Title freed or given back
#: (CLIP_TITLE) and its headers' dates set (CLIP_HEADER_DATES); every other copy is left as it was, whatever the XMP value (segment_edits.clip_stale_of): a clip's
#: EXIF and IPTC, if any, are not TagPup's to write.
CLIP_FIELDS = fields_of(CLIP_WRITES)

#: Where TagPup's own JPEG writer (tagpup.files.segment_edits) puts each field a journaled write names: (segment kind,
#: the property or field its editor names it by), xmp_packet's, exif_block's or iptc_block's. A target after the first
#: is written only where its segment holds it already, as ExifTool's write of "Orientation" (WRITTEN_AS) updates an XMP
#: tiff:Orientation held and makes none. A field not here is not the own writer's: a bake's fields; a write naming one is
#: ExifTool's. The DocumentID the index's bake mints (tagpup.services.bake_after_index) is: a mint alone patches where
#: the XMP has room.
EDITED_AS = {
    "XMP:Subject": (("xmp", "dc:subject"),),
    "XMP:HierarchicalSubject": (("xmp", "lr:hierarchicalSubject"),),
    "XMP:Description": (("xmp", "dc:description"),),
    "XMP:Title": (("xmp", "dc:title"),),
    "XMP:DateTimeOriginal": (("xmp", "exif:DateTimeOriginal"),),
    "XMP:CreateDate": (("xmp", "xmp:CreateDate"),),
    PRESERVED_NAME: (("xmp", "xmpMM:PreservedFileName"),),
    "XMP-xmpMM:DocumentID": (("xmp", "xmpMM:DocumentID"),),
    "EXIF:DateTimeOriginal": (("exif", "DateTimeOriginal"),),
    "EXIF:CreateDate": (("exif", "CreateDate"),),
    "EXIF:ImageDescription": (("exif", "ImageDescription"),),
    "EXIF:XPComment": (("exif", "XPComment"),),
    "EXIF:XPKeywords": (("exif", "XPKeywords"),),
    ORIENTATION: (("exif", "Orientation"), ("xmp", "tiff:Orientation")),
    "IPTC:Keywords": (("iptc", "IPTC:Keywords"),),
    "IPTC:Caption-Abstract": (("iptc", "IPTC:Caption-Abstract"),),
    "IPTC:ObjectName": (("iptc", "IPTC:ObjectName"),),
}


def as_patched(before, after, stale):
    """What a file holds after a write of `after` that left the fields in `stale` as they were: `after`, each of its
    fields in `stale` holding `before`'s value (none, when `before` names it not). With nothing stale, `after` itself."""
    if not stale:
        return after
    return {field: before.get(field, []) if field in stale else value for field, value in after.items()}


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
    """How a Date Taken is written: one set on the page, "2019-06-15T10:30:00", as "2019:06:15 10:30:00", the way EXIF
    spells a date; one spelled so already (a clip's, carried into its converted copy: tagpup.services.convert) as it is,
    its fraction and its zone kept ("2018:03:02 09:15:00-07:00": only the date's dashes are colons). A time on the
    minute, as a datetime-local input answers it ("2019-06-15T10:30"), is given its seconds: "2019:06:15 10:30:00"."""
    text = str(date_taken).strip().replace("T", " ", 1)
    text = text[:10].replace("-", ":") + text[10:]
    return text + ":00" if len(text) == 16 else text


def date_taken_fields(date_taken, clip=False):
    """Every field a Date Taken write may set. A Date Taken is to the second: the page's picker sets no fraction, and no
    row's `taken` holds one (0 in the three libraries, counted 2026-11-05), so no SubSecTime is written; nor a zone. A
    clip's are its XMP dates (dates.CLIP_XMP_DATE_FIELDS), patched into its XMP (tagpup.files.segment_edits.new_packet),
    of which held_dates keeps XMP:CreateDate only where the clip holds one, and its QuickTime headers' (CLIP_HEADER_DATES),
    set in the same patch (tagpup.files.clip_patch) to the same local digits as if UTC, its fraction and zone left out
    (a header holds seconds: DECISIONS 2026-11-13, A CLIP'S DATE TAKEN IS WRITTEN INTO ITS QUICKTIME HEADERS, "local
    as-is"). The one place a date's header digits are decided: a save's date has neither; a date Convert carries may
    have a zone (122 photo_index clips are dated with one, an iPhone .mov's Apple CreationDate or Adobe's XMP; none
    with a fraction; counted 2026-11-13), kept in its XMP as the original held it."""
    value = date_taken_value(date_taken)
    if clip:
        digits = value[:len("YYYY:MM:DD HH:MM:SS")]
        return {**{field: value for field in dates.CLIP_XMP_DATE_FIELDS}, **{field: digits for field in CLIP_HEADER_DATES}}
    return {"EXIF:DateTimeOriginal": value, "XMP:DateTimeOriginal": value, "EXIF:CreateDate": value}


#: The date field a write sets only where the file holds it already: a clip's XMP:CreateDate, which Adobe's tools wrote
#: (41 photo_index clips, counted 2026-11-08), refreshed so it does not contradict the date saved, and made in no other
#: (the owner's answer, DECISIONS 2026-11-08, A5 (D) REVISED).
_WHERE_HELD = "XMP:CreateDate"
assert _WHERE_HELD in dates.CLIP_XMP_DATE_FIELDS


def held_dates(values, held):
    """`values` ({field: value} a write sets) without _WHERE_HELD when `held` (what the file holds, or a row's
    raw_metadata) holds none: decided once, by a save from what the file holds (tagpup.services.tagging); the page's
    record is told what that save wrote (record_dates), not left to decide it again."""
    return {field: value for field, value in values.items()
            if field != _WHERE_HELD or field_values(held.get(field))}


def record_dates(raw_meta, written):
    """Make `raw_meta` say what a Date Taken write just put in the file -- `written`, {field: value} the save reports it
    wrote -- the fields the scan reads, as record_keyword_fields does for keywords. Returns it."""
    for field, value in written.items():
        if scan_reads(field):
            raw_meta[field] = value
    return raw_meta


# ---- A field's value, as the file journal holds and compares it ------------------------

#: The fields whose value is a list: a keyword field holds several, and ExifTool answers
#: one of them alone as a bare value; a bake's FaceVectors, one per face (tagpup.files.bake).
LIST_FIELDS = ("XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject", "XMP:FaceVectors")

#: MWG's regions, a structure: the one struct field a change writes (a bake's, tagpup.files.bake).
REGION_INFO = "XMP-mwg-rs:RegionInfo"

#: The fields whose value is a structure. The journal keeps one as ONE text, ExifTool's serialised form of it
#: (tagpup.files.bake.region_text): read with -struct and made that text as it is read (tagpup.files.field_values), written
#: whole, cleared whole by an empty value. Never the dict ExifTool answers: field_values refuses one.
STRUCT_FIELDS = (REGION_INFO,)


def is_struct(field):
    """Is `field` one of STRUCT_FIELDS, by the key ExifTool answers it under?"""
    return read_key(field) in {read_key(each) for each in STRUCT_FIELDS}


#: A group 1 name a field is asked by whose -G answer is another group 0 name: a clip's Xtra (CLIP_CATEGORY).
_GROUP_0 = {"Microsoft": "QuickTime"}


def read_key(field):
    """The key ExifTool answers `field` under when it reads with -G (group family 0):
    "XMP-xmpMM:DocumentID" is written under its own group and answered as
    "XMP:DocumentID"; "XMP:Subject" is both; "Microsoft:Category" is answered as "QuickTime:Category" (_GROUP_0)."""
    group, _, name = str(field).partition(":")
    group = group.split("-", 1)[0]
    return "%s:%s" % (_GROUP_0.get(group, group), name) if name else field


def field_values(value):
    """A field's value as the journal keeps it: a list of texts, in the file's order,
    blanks and empties left out. A field the file does not hold is []. A structure (a dict)
    raises TypeError: it was dropped, and a write of one planned as "clear the field"; a
    struct field's value is its one text (STRUCT_FIELDS)."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    found = []
    for item in items:
        if isinstance(item, dict):
            raise TypeError("a structure is not a field's value: a struct field's value is its one text "
                            "(tagpup.files.bake.region_text)")
        if item is None or isinstance(item, bytes):
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
#: A text ExifTool answers as a JSON boolean, /^(true|false)$/i, by what the journal
#: keeps of it.
_JSON_BOOLEANS = {"true": str(True), "false": str(False)}

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
    if text.lower() in _JSON_BOOLEANS:
        # Answered as a JSON boolean, kept as str() of it: "true" reads back "True".
        return _JSON_BOOLEANS[text.lower()]
    return text


def as_written(field, value):
    """The texts of `value` a write sends for `field`: an IPTC field's each cut to the characters IPTC keeps
    (IPTC_LIMITS; in ExifTool's default Latin a letter is one byte), as ExifTool cut them itself until every write was
    told an objection it calls minor does not stop it (-m, which also stops that cut: DECISIONS 2026-10-27). A file
    holds a keyword of IPTC's length, as before, and as_read says what it holds."""
    limit = IPTC_LIMITS.get(read_key(field))
    texts = field_values(value)
    return [text[:limit] for text in texts] if limit is not None else texts


def as_read(field, value, charset=IPTC_CHARSETS[0]):
    """What a read of a file gives for `value` written to `field`, as the journal keeps a
    value (field_values), trimmed: IPTC cuts a keyword at 64 bytes and writes a letter
    outside its `charset` as "?" (IPTC_LIMITS, IPTC_CHARSETS), and a text that looks like
    a number, or is true or false in any case, is answered as one. A value written is not always read back as written;
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

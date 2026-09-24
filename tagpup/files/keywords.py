"""Writing a photo's keyword and caption fields, and reading its tags back.

The fields a keyword write sets are listed once (keyword_fields), and both the write
and the record of it in raw_metadata (record_keyword_fields) go by that list, so the
file and its index row cannot drift apart one field at a time.

Which person a bare name means is the library's business, not the file's: callers
resolve people to their tags (tagpup_server.resolve_people_tags) before writing.
"""
from tagpup.core import vocabulary
from tagpup.files.metadata import METADATA_FIELDS, clean_metadata_value


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
        if field not in METADATA_FIELDS:
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


def write_keywords(et, path, tags, extra_params=None):
    """Write `tags` into a photo's keyword fields, clearing fields that end up empty.

    ExifTool treats an empty list as "no change", so assigning [] silently leaves the
    old keywords in place. Removing a photo's last tag therefore has to be expressed as
    an explicit '-TAG=' deletion instead. `extra_params` go in the same write.

    Returns the (flat, hierarchical) keywords written.
    """
    flat, hierarchical = expand_tag_fields(tags)

    params = dict(extra_params or {})
    clear_args = []

    # The fields come from keyword_fields(), which record_keyword_fields() also reads,
    # so whatever is written here is what the index records. An empty string clears a
    # field as written; an empty list does not, and needs the explicit deletion.
    for field, value in keyword_fields(flat, hierarchical).items():
        if value or isinstance(value, str):
            params[field] = value
        else:
            clear_args.append("-%s=" % field)

    if params:
        et.set_tags([path], tags=params, params=["-overwrite_original"])
    if clear_args:
        et.execute(*clear_args, "-overwrite_original", path)

    return flat, hierarchical


#: The fields a photo's tags are read from: exactly what vocabulary.extract_tags
#: reads, so tags read here are the tags a folder scan would have found.
TAG_SOURCE_FIELDS = ("IPTC:Keywords", "XMP:Subject", "XMP:HierarchicalSubject")


def tags_in_file(et, photo_path):
    """The tags a photo carries now, read from the file itself.

    The bulk writers replace a photo's whole keyword set, so they must start from
    what it holds. They used to start from the folder cache, then the index, then
    nothing: the cache is empty after a restart while the page still shows the folder,
    and a photo the index has no row for then kept only the tags being added. The
    file is the truth; it is read in the ExifTool session the writer already has open.
    Raises if the file cannot be read, rather than treat it as having no tags.
    """
    found = et.get_tags([photo_path], tags=list(TAG_SOURCE_FIELDS))
    meta = found[0] if found else {}
    return vocabulary.extract_tags({k: clean_metadata_value(v) for k, v in meta.items()})

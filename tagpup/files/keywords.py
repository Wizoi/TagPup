"""Writing a photo's keyword fields, and reading its tags back.

Which fields a write sets is tagpup.core.fields' (keyword_fields, caption_fields), and
both the write here and the record of it in the index go by that one list, so the file
and its index row cannot drift apart one field at a time.

Which person a bare name means is the library's business, not the file's: callers
resolve people to their tags (tagpup_server.resolve_people_tags) before writing.
"""
from tagpup.core import vocabulary
from tagpup.core.fields import (  # noqa: F401  (imported from here by older code)
    TAG_SOURCE_FIELDS, caption_fields, expand_tag_fields, keyword_fields,
    record_keyword_fields)
from tagpup.files.metadata import clean_metadata_value


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


def tags_in_file(et, photo_path):
    """The tags a photo carries now, read from the file itself.

    The bulk writers replace a photo's whole keyword set, so they must start from
    what it holds. They used to start from the folder cache, then the index, then
    nothing: the cache is empty after a restart while the page still shows the folder,
    and a photo the index has no row for then kept only the tags being added. The
    file is the truth; it is read in the ExifTool session the writer already has open.
    Raises if the file cannot be read, rather than treat it as having no tags. A missing
    file makes ExifTool fail; one it cannot parse comes back with no fields at all and a
    type that is not an image (a file of text read as TXT), and read as a photo with no
    tags, so replacing a tag rewrote its index row to none (docs/findings.md, #46).
    """
    found = et.get_tags([photo_path], tags=list(TAG_SOURCE_FIELDS) + [MIME_TYPE])
    meta = found[0] if found else {}
    kind = meta.pop(MIME_TYPE, None)
    if kind is not None and not str(kind).startswith("image/"):
        raise ValueError("Not a photo ExifTool can read (%s): %s" % (kind, photo_path))
    return vocabulary.extract_tags({k: clean_metadata_value(v) for k, v in meta.items()})


#: What ExifTool says a file is. A photo's starts with "image/".
MIME_TYPE = "File:MIMEType"

"""Reading a photo's tags back from its file.

Which fields a write sets is tagpup.core.fields' (keyword_fields, caption_fields), and both the
journaled write (tagpup.services.file_changes, tagpup.files.field_values) and the record of it in the
index go by that one list, so the file and its index row cannot drift apart one field at a time. The
write that was here had no caller outside tests once the single save went through the file journal
(docs/findings.md, #300).

Which person a bare name means is the library's business, not the file's: callers
resolve people to their tags first (tagpup.core.vocabulary.resolve_people, over
tagpup.store.taxonomy.people_paths), as every writer in tagpup.services does.
"""
from tagpup.core import vocabulary
from tagpup.core.fields import (  # noqa: F401  (imported from here by older code)
    TAG_SOURCE_FIELDS, caption_fields, expand_tag_fields, keyword_fields,
    record_keyword_fields)
from tagpup.files.metadata import clean_metadata_value


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

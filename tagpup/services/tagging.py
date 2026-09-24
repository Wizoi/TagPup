"""Actions on photos' tags."""
import logging

from tagpup.core import fields, vocabulary
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, keywords
from tagpup.store import photos, taxonomy

logger = logging.getLogger(__name__)


def replace_tag(library, photo_paths, old, new, exiftool_path):
    """Rename the tag `old` to `new` -- and every tag under it -- on each photo in
    `photo_paths`, or take it off without `new`: in the file, then in the index.

    Renaming or deleting a node of the tag tree, merging one tag into another and
    renaming a person all come here. Each photo is written through the one keyword
    writer, people resolved to their tags first, and recorded through the one recorder,
    as a bulk edit is: this used to write its own rows, recording two of the keyword
    fields and not the file's new mtime, so a renamed tag's photos were re-read on every
    scan and a stale IPTC:Keywords was re-derived straight back into the tags.

    attempted: the photos the index has a row for. changed: the rows rewritten. A photo
    not carrying the tag is skipped; one that could not be written is an error.
    """
    rows = photos.read_tags(library.path, photo_paths)
    result = Result(attempted=len(rows))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, current_tags, raw_meta in rows:
            new_tags, changed = vocabulary.retag(current_tags, old, new)
            if not changed:
                result.skip(path, "does not carry the tag")
                continue
            try:
                flat, hierarchical = keywords.write_keywords(
                    et, path, vocabulary.resolve_people(new_tags, people))
                # The tags column holds the view extract_tags derives from the file's
                # fields, as it did before; derived here from exactly the fields just
                # written.
                updated = vocabulary.extract_tags(fields.record_keyword_fields(raw_meta, flat, hierarchical))
                if photos.record_tags(library.path, path, updated, flat, hierarchical):
                    result.changed += 1
            except Exception as err:
                logger.error("Failed to update metadata on disk/db for %s: %s", path, err)
                result.fail(path, err)
    return result

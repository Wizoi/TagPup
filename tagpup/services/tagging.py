"""Actions on photos' tags."""
import logging

from tagpup.core import fields, paths, vocabulary
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, keywords
from tagpup.store import photos, taxonomy

logger = logging.getLogger(__name__)


def change_tags(library, photo_paths, add, remove, exiftool_path):
    """Add the same tags to many photos and take the same tags off them. Adding or
    removing tags on a selection of photos. See _change_each."""
    return _change_each(library, [(path, add, remove) for path in photo_paths], exiftool_path)


def add_tags(library, additions, exiftool_path):
    """Add each photo in `additions` (path -> tags) its own tags. Apply All on a folder's
    suggestions: suggestions deal in people's bare names, which are written as the tags
    they are filed under. A photo with nothing to add is left alone. See _change_each."""
    return _change_each(library, [(path, tags, ()) for path, tags in additions.items() if tags],
                        exiftool_path)


def _change_each(library, plan, exiftool_path):
    """Write each photo in `plan` -- (path, tags to add, tags to take off) -- in one
    ExifTool session.

    Each write replaces the photo's whole keyword set, so it starts from what the file
    holds now -- never from a cache that may be cold or an index that may never have
    seen the photo. People are written as the tags they are filed under, and the index
    is told what was written.

    Stops at the first photo that cannot be read or written, which is the error; the
    photos before it keep their changes. details: `written`, path -> (tags, flat,
    hierarchical) for each photo written.
    """
    result = Result(attempted=len(plan))
    written = result.details["written"] = {}
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, add, remove in plan:
            path = paths.stored(path)
            try:
                tags = set(keywords.tags_in_file(et, path))
                tags.update(add)
                tags.difference_update(remove)
                new_tags = vocabulary.resolve_people(list(tags), people)
                flat, hierarchical = keywords.write_keywords(et, path, new_tags)
                photos.record_tags(library.path, path, new_tags, flat, hierarchical)
            except Exception as err:
                result.fail(path, err)
                break
            written[path] = (new_tags, flat, hierarchical)
            result.changed += 1
    return result


def replace_tag(library, photo_paths, old, new, exiftool_path):
    """Rename the tag `old` to `new` -- and every tag under it -- on each photo in
    `photo_paths`, or take it off without `new`: in the file, then in the index.

    Renaming or deleting a node of the tag tree, merging one tag into another and
    renaming a person all come here. Each photo is written through the one keyword
    writer, people resolved to their tags first, and recorded through the one recorder,
    as a bulk edit is: this used to write its own rows, recording two of the keyword
    fields and not the file's new mtime, so a renamed tag's photos were re-read on every
    scan and a stale IPTC:Keywords was re-derived straight back into the tags.

    Each photo's keywords are read from its file first. The write replaces the whole
    keyword set, and the index can lag the file: starting from the index's copy dropped
    a keyword written elsewhere since, and wrote back one taken off elsewhere (finding
    #30). A photo whose file does not carry the tag is left alone, and its row, which
    said it did, is made to say what the file holds.

    attempted: the photos the index has a row for. changed: the rows rewritten. A photo
    not carrying the tag is skipped; one that could not be read or written is an error.
    """
    rows = photos.read_tags(library.path, photo_paths)
    result = Result(attempted=len(rows))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, indexed_tags, raw_meta in rows:
            try:
                current_tags = keywords.tags_in_file(et, path)
            except Exception as err:
                logger.error("Could not read the keywords of %s: %s", path, err)
                result.fail(path, err)
                continue
            new_tags, changed = vocabulary.retag(current_tags, old, new)
            if not changed:
                result.skip(path, "does not carry the tag")
                if set(current_tags) != set(indexed_tags):
                    photos.record_tags(library.path, path, current_tags)
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

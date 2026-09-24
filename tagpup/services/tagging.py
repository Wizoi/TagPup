"""Actions on photos' tags and captions."""
import logging

from tagpup.core import fields, paths, vocabulary
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, keywords, metadata
from tagpup.store import faces, photos, taxonomy

logger = logging.getLogger(__name__)


def save_photo(library, photo_path, title, tags, date_taken, exiftool_path, rename_format):
    """Save one photo's caption, tags and Date Taken -- the photo panel -- and rename it
    after its new caption if Smart Rename named it.

    `tags` is the photo's whole tag list. Only the ones the file does not hold yet are
    checked: one written by another program must not stop the photo being saved, least
    of all a save that removes it. A new one that may not be set refuses the save and
    nothing is written; the others go in in their one spelling, as the tag tree holds
    them. A rename moves the photo's index row -- embedding, faces and all -- rather
    than leaving them behind; then the row gets what the file holds now. A failure
    recording it is logged, not raised: the file is written either way.

    details: `new_path`, `renamed`, `tags` as written, `flat` and `hierarchical` as
    written, and `index_warning` when the renamed photo's new name already had rows.
    """
    result = Result(attempted=1)
    params = fields.caption_fields(title or "")
    if date_taken:
        params.update(fields.date_taken_fields(date_taken))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        held = set(keywords.tags_in_file(et, photo_path))
        problem = vocabulary.problem_with_tags(t for t in tags if t not in held)
        if problem:
            result.refuse(problem)
            return result
        tags = [t if t in held else vocabulary.normalize(t) for t in tags]
        flat, hierarchical = keywords.write_keywords(
            et, photo_path, vocabulary.resolve_people(tags, people), extra_params=params)
    result.changed = 1

    new_path = paths.stored(metadata.sync_title_to_filename(photo_path, title, exiftool_path, rename_format))
    renamed = not paths.same(new_path, photo_path)
    result.details.update(new_path=new_path, renamed=renamed, tags=tags, flat=flat,
                          hierarchical=hierarchical, index_warning=None)
    try:
        with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
            raw_meta = metadata.raw_metadata(et, new_path)
        recorded_tags = vocabulary.extract_tags(raw_meta)
        # The faces are still filed under the old name until the row moves.
        people_in_it = vocabulary.people_in_photo(
            raw_meta, recorded_tags, faces.face_names(photo_path, db_path=library.path),
            taxonomy.people_vocabulary(library.path))
        skipped = photos.move_rows(library.path, {photo_path: new_path})[1] if renamed else []
        if skipped:
            result.details["index_warning"] = (
                "Renamed, but the index already has a photo at %s; its rows were left as they were."
                % new_path)
        else:
            photos.record_saved(library.path, new_path, recorded_tags, people_in_it,
                                [title] if title else [], raw_meta)
    except Exception as e:
        logger.warning("Failed to update SQLite database metadata for %s: %s", new_path, e)
    return result


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
